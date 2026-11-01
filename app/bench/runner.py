"""Build a plan, run it, and assemble a result document.

The run is deliberately boring: a fixed, ordered task list; one sandboxed worker per adapter; a
deadline per item; every failure recorded as data; and a result that says ``not_measured`` wherever
nothing was measured. Nothing in this module decides which candidate is better.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import BENCH_VERSION, EXIT_NETWORK_VIOLATION, EXIT_OK, EXIT_RUN_FAILED
from . import languages as languages_module
from . import scoring, stats
from .adapter import AdapterError, describe_artifact, require_artifacts
from .fixtures import Fixture, FixtureError, generate_synthetic, load_pack
from .hostinfo import HostInfo, load_declared

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Short, generic, generated sentences for the speech-generation phase. No personal content.
TTS_TEXTS: tuple[dict, ...] = (
    {"id": "tts.short", "text": "Lights on."},
    {"id": "tts.medium",
     "text": "Set a reminder for tomorrow morning and read it back to me so I can confirm it."},
    {"id": "tts.long",
     "text": ("Here is the summary you asked for. The meeting moved to Thursday at half past "
              "nine, the report is due on the fifteenth, and the two numbers to remember are "
              "forty-two and seventeen. Say repeat if you want any part of that again.")},
)

#: How many item records are kept in the result. Aggregates are always complete; the cap only
#: bounds the detail list for very long runs.
ITEM_DETAIL_CAP = 20000

PHASES = ("asr", "tts", "turn", "e2e")


@dataclass
class RunConfig:
    out_dir: Path
    fixtures_dir: Path | None = None
    asr_adapter: str | None = None
    tts_adapter: str | None = None
    turn_adapter: str | None = None
    asr_model: str | None = None
    tts_model: str | None = None
    asr_model_sha256: str | None = None
    tts_model_sha256: str | None = None
    languages: list[str] = field(default_factory=list)
    repeat: int = 3
    warmup: int = 1
    seed: int = 20261101
    host_role: str = "development"
    timeout_s: float = 60.0
    allow_loopback: bool = False
    hash_models: bool = True
    include_hostname: bool = False
    host_facts: Path | None = None
    criteria: Path | None = None

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.host_role not in ("development", "dedicated_server"):
            problems.append("host role must be 'development' or 'dedicated_server'")
        if not 1 <= int(self.repeat) <= 50:
            problems.append("repeat must be between 1 and 50")
        if not 0 <= int(self.warmup) <= 20:
            problems.append("warmup must be between 0 and 20")
        if self.timeout_s <= 0:
            problems.append("timeout must be positive")
        return problems


@dataclass
class Plan:
    document: dict
    tasks: list[dict]
    fixtures: list[Fixture]
    artifacts: dict

    @property
    def plan_hash(self) -> str:
        return self.document["plan_hash"]


# --------------------------------------------------------------------------- planning
def reproduce_command(config: RunConfig) -> str:
    """The exact command that reproduces this configuration, recorded inside the plan."""
    import shlex

    from .adapter import display_path as _display

    def publishable(value) -> str:
        """Paths are shown as ``<repo>/…`` or ``~/…``: the command stays readable and shareable."""
        return _display(Path(value))

    parts = ["python3", "-m", "app.bench", "--out", publishable(config.out_dir)]
    for flag, value in (("--asr-adapter", config.asr_adapter),
                        ("--tts-adapter", config.tts_adapter),
                        ("--turn-adapter", config.turn_adapter),
                        ("--asr-model", config.asr_model),
                        ("--tts-model", config.tts_model),
                        ("--asr-model-sha256", config.asr_model_sha256),
                        ("--tts-model-sha256", config.tts_model_sha256),
                        ("--fixtures", config.fixtures_dir),
                        ("--host-facts", config.host_facts),
                        ("--criteria", config.criteria)):
        if value:
            parts += [flag, publishable(value)]
    if config.languages:
        parts += ["--languages", ",".join(config.languages)]
    if config.repeat != 3:
        parts += ["--repeat", str(config.repeat)]
    if config.warmup != 1:
        parts += ["--warmup", str(config.warmup)]
    if config.seed != 20261101:
        parts += ["--seed", str(config.seed)]
    if config.host_role != "development":
        parts += ["--host-role", config.host_role]
    if config.timeout_s != 60.0:
        parts += ["--timeout", str(config.timeout_s)]
    if config.allow_loopback:
        parts.append("--allow-loopback-ipc")
    if not config.hash_models:
        parts.append("--no-hash-models")
    if config.include_hostname:
        parts.append("--include-hostname")
    return " ".join(shlex.quote(part) for part in parts)


def _fixture_document(fixture: Fixture, base: Path) -> dict:
    """A fixture record whose path is relative to the run directory: publishable, and stable
    enough that the same inputs hash the same way on another machine."""
    document = fixture.to_dict()
    try:
        document["path"] = str(Path(document["path"]).relative_to(Path(base)))
    except ValueError:
        document["path"] = Path(document["path"]).name
    return document


def _git_state() -> dict:
    def run(*arguments: str) -> str | None:
        try:
            completed = subprocess.run(["git", *arguments], cwd=REPO_ROOT, capture_output=True,
                                       text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git on the host
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {
        "commit": commit,
        "dirty": bool(status),
        "dirty_files": len([line for line in (status or "").splitlines() if line.strip()]),
    }


def _plan_hash(document: dict) -> str:
    import hashlib

    # The command string and the timestamp are provenance, not the work: two runs of the same
    # configuration must hash the same even if they were told to write to different directories.
    payload = {key: value for key, value in document.items()
               if key not in ("created_at", "plan_hash", "reproduce_command")}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def text_fixtures() -> list[dict]:
    return [{"id": item["id"], "text": item["text"], "chars": len(item["text"])}
            for item in TTS_TEXTS]


def build_plan(config: RunConfig, *, require_adapters: bool) -> Plan:
    """Turn the configuration into a fixed, hashable plan. Raises on anything unusable."""
    problems = config.validate()
    if problems:
        raise AdapterError("; ".join(problems))

    config.out_dir = Path(config.out_dir)
    fixture_dir = config.out_dir / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    synthetic = generate_synthetic(fixture_dir, seed=config.seed)

    matrix = languages_module.load_matrix()
    allowed = {language.code for language in matrix}
    try:
        scheduled = languages_module.select(config.languages)
    except languages_module.LanguageMatrixError as exc:
        raise AdapterError(str(exc)) from exc
    scheduled_codes = [language.code for language in scheduled]

    licensed: list[Fixture] = []
    if config.fixtures_dir:
        try:
            licensed = load_pack(Path(config.fixtures_dir), allowed_languages=allowed)
        except FixtureError as exc:
            raise AdapterError(str(exc)) from exc

    artifacts: dict = {"adapters": {}, "models": {}}
    for label, path, model_path, declared in (
        ("asr", config.asr_adapter, config.asr_model, config.asr_model_sha256),
        ("tts", config.tts_adapter, config.tts_model, config.tts_model_sha256),
        ("turn", config.turn_adapter, None, None),
    ):
        if path:
            artifacts["adapters"][label] = describe_artifact(path, kind="adapter")
        if model_path:
            artifacts["models"][label] = describe_artifact(
                model_path, kind="model", declared_sha256=declared,
                compute_hash=config.hash_models,
            )
    if require_adapters:
        missing = [label for label in ("asr", "tts") if label not in artifacts["adapters"]]
        if missing and not (config.asr_adapter or config.tts_adapter):
            raise AdapterError(
                "no adapter supplied: pass --asr-adapter and/or --tts-adapter with a local file; "
                "this benchmark has no default model and never downloads one"
            )
        fatal = require_artifacts(artifacts["adapters"], artifacts["models"])
        if fatal:
            raise AdapterError("; ".join(fatal))

    has_asr = "asr" in artifacts["adapters"]
    has_tts = "tts" in artifacts["adapters"]
    has_turn = "turn" in artifacts["adapters"]

    fixtures = synthetic + licensed
    tasks = _build_tasks(fixtures, scheduled_codes, has_asr=has_asr, has_tts=has_tts,
                         has_turn=has_turn, repeat=config.repeat, warmup=config.warmup)
    by_id = {fixture.id: fixture for fixture in fixtures}
    for task in tasks:
        task["_fixture"] = by_id.get(task.get("fixture_id"))

    phases = sorted({task["phase"] for task in tasks})
    document = {
        "plan_version": 1,
        "bench_version": BENCH_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git": _git_state(),
        "host_role": config.host_role,
        "seed": config.seed,
        "repeat": config.repeat,
        "warmup": config.warmup,
        "timeout_s": config.timeout_s,
        "allow_loopback_ipc": config.allow_loopback,
        "hash_models": config.hash_models,
        "phases": phases,
        "languages": {
            "proposal": languages_module.PROPOSAL_ID,
            "proposal_is_a_requirement": False,
            "scheduled": scheduled_codes,
            "matrix": [language.to_dict() for language in matrix],
        },
        "fixtures": {
            "synthetic": [_fixture_document(fixture, config.out_dir) for fixture in synthetic],
            "licensed": [_fixture_document(fixture, config.out_dir) for fixture in licensed],
            "pack_supplied": bool(config.fixtures_dir),
        },
        "text_fixtures": text_fixtures(),
        "artifacts": {
            "adapters": {label: artifact.to_dict() for label, artifact in artifacts["adapters"].items()},
            "models": {label: artifact.to_dict() for label, artifact in artifacts["models"].items()},
        },
        "task_count": len(tasks),
        "reproduce_command": reproduce_command(config),
        "timing_boundaries": {
            "asr": "adapter invocation start (whole fixture delivered) to adapter returning text; "
                   "IPC to the worker is excluded",
            "tts": "adapter invocation start (whole sentence delivered) to the adapter returning "
                   "audio; IPC excluded",
            "e2e": "parent wall clock from posting the audio to the recogniser to receiving the "
                   "first audio from the synthesiser; local IPC between harness and workers is "
                   "included and is part of the pipeline cost, network is not",
            "cold": "the first measured item of a phase in a freshly started worker",
            "warm": "measured items after the first, in the same worker",
        },
        "methodology": stats.methodology_block(),
        "measurement_scope": {
            "acoustic": "synthetic fixtures measure latency, throughput, memory and robustness "
                        "only; they contain no speech, so WER, entity accuracy and task success "
                        "are not measured from them",
            "language": "WER, entity accuracy and task success are measured per language from "
                        "licensed fixtures in that language, and from nothing else",
        },
    }
    document["plan_hash"] = _plan_hash(document)

    return Plan(document=document, tasks=tasks, fixtures=fixtures, artifacts=artifacts)


def _build_tasks(fixtures: list[Fixture], scheduled: list[str], *, has_asr: bool, has_tts: bool,
                 has_turn: bool, repeat: int, warmup: int) -> list[dict]:
    tasks: list[dict] = []
    total_rounds = repeat + warmup

    def add(phase: str, round_index: int, *, fixture: Fixture | None = None, text: dict | None = None):
        warm = round_index < warmup
        repeat_index = round_index - warmup
        base = {
            "phase": phase,
            "warmup": warm,
            "repeat": repeat_index,
            "language": (fixture.language if fixture else None),
            "condition": (fixture.condition if fixture else "clean"),
        }
        if fixture is not None:
            base.update({
                "fixture_id": fixture.id,
                "scope": "language" if fixture.language else "acoustic",
                "audio_path": str(fixture.path),
                "audio_seconds": fixture.duration_s,
                "sample_rate": fixture.sample_rate,
                "contains_speech": fixture.contains_speech,
                # The declared transcript is part of the licensed pack, not personal data; the
                # turn phase needs an input sentence and this is the honest one to use.
                "reference_text": fixture.transcript,
            })
        if text is not None:
            base.update({"fixture_id": text["id"], "scope": "acoustic", "text": text["text"],
                         "text_chars": text["chars"]})
        identifier = "-".join(
            str(part) for part in (
                phase,
                base.get("language") or "neutral",
                base.get("fixture_id"),
                base["condition"],
                "warm" if warm else f"r{repeat_index}",
            )
        )
        base["task_id"] = identifier
        tasks.append(base)

    if has_asr:
        for round_index in range(total_rounds):
            for fixture in fixtures:
                # A language that was not scheduled is not run at all, even if audio exists for it.
                if fixture.language and fixture.language not in scheduled:
                    continue
                add("asr", round_index, fixture=fixture)
    if has_tts:
        for round_index in range(total_rounds):
            for text in text_fixtures():
                add("tts", round_index, text=text)
    if has_turn:
        for round_index in range(total_rounds):
            for fixture in fixtures:
                if not fixture.transcript:
                    continue
                if fixture.language and fixture.language not in scheduled:
                    continue
                add("turn", round_index, fixture=fixture)
    if has_asr and has_tts:
        for round_index in range(total_rounds):
            for fixture in fixtures:
                if fixture.language and fixture.language not in scheduled:
                    continue
                add("e2e", round_index, fixture=fixture)
    order = {"asr": 0, "turn": 1, "tts": 2, "e2e": 3}
    tasks.sort(key=lambda task: (task["warmup"], order[task["phase"]],
                                 str(task.get("language") or ""), task["fixture_id"],
                                 task["condition"], task["repeat"]))
    return tasks


# --------------------------------------------------------------------------- worker client
class WorkerError(Exception):
    pass


class WorkerClient:
    """One sandboxed adapter process, with a deadline per request."""

    def __init__(self, adapter_path: str, *, label: str, allow_loopback: bool, timeout_s: float,
                 log_path: Path) -> None:
        self.label = label
        self.timeout_s = timeout_s
        self.log_path = log_path
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        environment["PYTHONNOUSERSITE"] = "1"
        environment.pop("http_proxy", None)
        environment.pop("https_proxy", None)
        environment.pop("HTTP_PROXY", None)
        environment.pop("HTTPS_PROXY", None)
        command = [sys.executable, "-m", "app.bench.worker", "--adapter", str(adapter_path)]
        if allow_loopback:
            command.append("--allow-loopback-ipc")
        self._log = log_path.open("ab")
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
            env=environment, bufsize=0,
        )
        self._buffer = bytearray()
        self.adapter: dict | None = None
        self.load_ms: float | None = None
        self.peak_rss_mb: float = 0.0
        self.violation: dict | None = None
        #: A load failure is recorded, not raised here: the caller needs the sandbox state, because
        #: an adapter that reaches for the network at import time must end the run as a violation,
        #: not as a plain load error.
        self.ready: dict | None = None
        self.load_error: str | None = None
        try:
            self.ready = self._handshake()
        except WorkerError as exc:
            self.load_error = str(exc)

    # ------------------------------------------------------------------ io
    def _read_line(self, timeout_s: float) -> str | None:
        deadline = time.monotonic() + timeout_s
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                return line.decode("utf-8", errors="replace")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                return None
            self._buffer.extend(chunk)

    def _send(self, payload: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def _handshake(self) -> dict:
        line = self._read_line(max(10.0, self.timeout_s))
        if line is None:
            self.process.kill()
            raise WorkerError(
                f"{self.label} adapter did not report readiness within {self.timeout_s}s "
                f"(see {self.log_path.name})"
            )
        event = json.loads(line)
        sandbox = event.get("sandbox") or {}
        if sandbox.get("violations"):
            self.violation = sandbox
        if event.get("event") != "ready":
            self.process.kill()
            message = event.get("error", {}).get("message", "unknown load failure")
            raise WorkerError(f"{self.label} adapter failed to load: {message}")
        self.adapter = event.get("adapter")
        self.load_ms = event.get("load_ms")
        self.peak_rss_mb = max(self.peak_rss_mb, float(event.get("peak_rss_mb_after_load") or 0.0))
        return event

    # ------------------------------------------------------------------ requests
    def request(self, task: dict) -> dict:
        self._send(task)
        line = self._read_line(self.timeout_s)
        if line is None:
            self.kill()
            raise WorkerError(
                f"{self.label} adapter exceeded the {self.timeout_s}s deadline on "
                f"{task['task_id']}"
            )
        payload = json.loads(line)
        sandbox = payload.get("sandbox") or {}
        if sandbox.get("violations"):
            self.violation = sandbox
        self.peak_rss_mb = max(self.peak_rss_mb, float(payload.get("peak_rss_mb") or 0.0))
        return payload

    def close(self, timeout_s: float = 5.0) -> None:
        try:
            if self.process.poll() is None:
                self._send({"op": "stop"})
                self._read_line(timeout_s)
        except (OSError, ValueError):  # pragma: no cover - worker already gone
            pass
        finally:
            self.kill()

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn process
                self.process.kill()
                self.process.wait(timeout=3)
        for handle in (self.process.stdin, self.process.stdout):
            try:
                if handle is not None:
                    handle.close()
            except OSError:  # pragma: no cover
                pass
        try:
            self._log.close()
        except OSError:  # pragma: no cover
            pass


# --------------------------------------------------------------------------- execution
@dataclass
class RunOutcome:
    status: str
    samples: list[dict]
    failures: list[dict]
    worker_metrics: dict
    network: dict
    exit_code: int


def _empty_network() -> dict:
    return {"attempts": 0, "denials": 0, "violations": [], "loopback_allowed": False,
            "denied": True, "log_sha256": None}


def execute(plan: Plan, config: RunConfig) -> RunOutcome:
    samples: list[dict] = []
    failures: list[dict] = []
    network = _empty_network()
    network["loopback_allowed"] = config.allow_loopback
    workers: dict[str, WorkerClient] = {}
    worker_metrics: dict[str, dict] = {}
    restarts = {"asr": 0, "tts": 0, "turn": 0}
    paths = {"asr": config.asr_adapter, "tts": config.tts_adapter, "turn": config.turn_adapter}
    log_dir = config.out_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    def ensure(phase: str) -> WorkerClient:
        if phase in workers and workers[phase].process.poll() is None:
            return workers[phase]
        path = paths[phase]
        if not path:
            raise WorkerError(f"no {phase} adapter configured")
        if phase in workers:  # restart after a crash or timeout
            restarts[phase] += 1
            if restarts[phase] > 3:
                raise WorkerError(f"{phase} adapter failed {restarts[phase]} times; aborting")
        client = WorkerClient(path, label=phase, allow_loopback=config.allow_loopback,
                              timeout_s=config.timeout_s,
                              log_path=log_dir / f"{phase}.log")
        workers[phase] = client
        worker_metrics[phase] = {"load_ms": client.load_ms, "adapter": client.adapter,
                                 "restarts": restarts[phase],
                                 "peak_rss_mb": client.peak_rss_mb}
        if client.violation:
            note_network(client)  # an import-time attempt still ends the run
        if client.load_error:
            raise WorkerError(client.load_error)
        return client

    def note_network(client: WorkerClient) -> bool:
        sandbox = client.violation
        if not sandbox:
            return False
        network["violations"].extend(sandbox.get("violation_detail", []))
        network["attempts"] = max(network["attempts"], int(sandbox.get("attempts", 0)))
        network["denials"] = max(network["denials"], int(sandbox.get("guard_denials", 0)))
        return True

    status = "completed"
    try:
        for task in plan.tasks:
            phase = task["phase"]
            if phase in ("turn", "e2e"):
                needed = ("asr",) if phase == "turn" else ("asr", "tts")
            else:
                needed = (phase,)
            if any(not paths[name] for name in needed):
                continue
            try:
                sample = _run_task(task, phase, ensure, config, note_network)
            except WorkerError as exc:
                if network["violations"]:
                    status = "violated"
                    break
                failures.append({"task_id": task["task_id"], "phase": phase, "warmup": task["warmup"],
                                 "code": "worker_error", "message": str(exc)[:300]})
                continue
            if sample is None:
                continue
            if network["violations"]:
                status = "violated"
                break
            if task["warmup"]:
                continue
            if sample.get("ok"):
                samples.append(sample)
            else:
                failures.append({
                    "task_id": sample["task_id"], "phase": phase,
                    "code": sample.get("error", {}).get("code", "adapter_error"),
                    "message": sample.get("error", {}).get("message", "")[:300],
                })
    finally:
        for phase, client in workers.items():
            metrics = worker_metrics.setdefault(phase, {"load_ms": client.load_ms,
                                                        "adapter": client.adapter,
                                                        "restarts": restarts.get(phase, 0)})
            metrics["peak_rss_mb"] = round(max(float(metrics.get("peak_rss_mb") or 0.0),
                                               float(client.peak_rss_mb or 0.0)), 1)
            if client.violation:
                network["violations"].extend(client.violation.get("violation_detail", []))
                network["attempts"] = max(network["attempts"], int(client.violation.get("attempts", 0)))
            client.close()

    if status != "violated":
        measured = [sample for sample in samples if sample.get("ok")]
        if not measured:
            status = "failed"
        elif failures:
            status = "incomplete"
    log_files = sorted((config.out_dir / "logs").glob("*.log"))
    if log_files:
        import hashlib

        digest = hashlib.sha256()
        for path in log_files:
            digest.update(path.read_bytes())
        network["log_sha256"] = digest.hexdigest()
        network["log_files"] = [path.name for path in log_files]
    exit_code = EXIT_OK
    if status == "violated":
        exit_code = EXIT_NETWORK_VIOLATION
    elif status == "failed":
        exit_code = EXIT_RUN_FAILED
    return RunOutcome(status=status, samples=samples, failures=failures,
                      worker_metrics=worker_metrics, network=network, exit_code=exit_code)


def _wire(task: dict) -> dict:
    """The task as sent to a worker: private harness keys (leading underscore) never leave here."""
    return {key: value for key, value in task.items() if not key.startswith("_")}


def _run_task(task: dict, phase: str, ensure, config: RunConfig, note_network) -> dict | None:
    started = time.perf_counter()
    if phase == "asr":
        client = ensure("asr")
        payload = client.request({**_wire(task), "op": "asr"})
        if payload.get("event"):
            raise WorkerError(f"unexpected event from asr worker: {payload.get('event')}")
        wall_ms = round((time.perf_counter() - started) * 1000, 3)
        if note_network(client):
            return {"task_id": task["task_id"], "ok": False,
                    "error": {"code": "network_violation", "message": "adapter attempted network access"}}
        return _asr_sample(task, payload, wall_ms, client)
    if phase == "tts":
        client = ensure("tts")
        payload = client.request({**_wire(task), "op": "tts"})
        wall_ms = round((time.perf_counter() - started) * 1000, 3)
        if note_network(client):
            return {"task_id": task["task_id"], "ok": False,
                    "error": {"code": "network_violation", "message": "adapter attempted network access"}}
        return _tts_sample(task, payload, wall_ms, client)
    if phase == "turn":
        client = ensure("turn")
        payload = client.request({**_wire(task), "op": "turn", "text": task.get("reference_text")})
        wall_ms = round((time.perf_counter() - started) * 1000, 3)
        if note_network(client):
            return {"task_id": task["task_id"], "ok": False,
                    "error": {"code": "network_violation", "message": "adapter attempted network access"}}
        return _turn_sample(task, payload, wall_ms)
    if phase == "e2e":
        asr_client = ensure("asr")
        asr_payload = asr_client.request({**_wire(task), "op": "asr"})
        if note_network(asr_client):
            return {"task_id": task["task_id"], "ok": False,
                    "error": {"code": "network_violation", "message": "adapter attempted network access"}}
        if not asr_payload.get("ok"):
            return {"task_id": task["task_id"], "phase": "e2e", "ok": False,
                    "error": asr_payload.get("error", {"code": "asr_failed"})}
        text = (asr_payload.get("output") or {}).get("text", "")
        tts_client = ensure("tts")
        tts_payload = tts_client.request({
            "task_id": task["task_id"] + "-tts", "op": "tts", "text": text,
            "language": task.get("language"), "condition": task.get("condition"),
            "repeat": task.get("repeat"),
        })
        wall_ms = round((time.perf_counter() - started) * 1000, 3)
        if note_network(tts_client):
            return {"task_id": task["task_id"], "ok": False,
                    "error": {"code": "network_violation", "message": "adapter attempted network access"}}
        return _e2e_sample(task, asr_payload, tts_payload, wall_ms)
    raise WorkerError(f"unknown phase {phase}")


def _base_sample(task: dict, payload: dict, wall_ms: float) -> dict:
    sandbox = payload.get("sandbox") or {}
    return {
        "task_id": task["task_id"],
        "phase": task["phase"],
        "scope": task.get("scope"),
        "language": task.get("language"),
        "fixture_id": task.get("fixture_id"),
        "condition": task.get("condition"),
        "repeat": task.get("repeat"),
        "ok": bool(payload.get("ok")),
        "wall_ms": wall_ms,
        "sandbox_attempts": int(sandbox.get("attempts", 0)),
    }


def _asr_sample(task: dict, payload: dict, wall_ms: float, client: WorkerClient) -> dict:
    sample = _base_sample(task, payload, wall_ms)
    if not payload.get("ok"):
        sample["error"] = payload.get("error", {"code": "adapter_error"})
        sample["ok"] = False
        return sample
    timings = payload.get("timings", {})
    output = payload.get("output", {})
    sample.update({
        "latency_ms": timings.get("start_to_complete_ms"),
        "first_output_ms": timings.get("start_to_first_output_ms"),
        "streaming": timings.get("streaming"),
        "text": output.get("text", ""),
        "audio_seconds": task.get("audio_seconds"),
        "real_time_factor": (
            round(float(task.get("audio_seconds") or 0.0) / (timings["start_to_complete_ms"] / 1000.0), 4)
            if timings.get("start_to_complete_ms") else None
        ),
        "contains_speech": task.get("contains_speech"),
    })
    return _score(sample, task)


def _score(sample: dict, task: dict) -> dict:
    """Attach WER / entity / task-success only when the fixture carries a reference."""
    fixture = task.get("_fixture")
    if not fixture or not fixture.transcript:
        sample["wer"] = None
        sample["entity_accuracy"] = None
        sample["task_success"] = None
        sample["not_measured"] = {
            "wer": "the fixture has no reference transcript (synthetic audio carries no words)",
            "entity_accuracy": "the fixture declares no critical entities",
            "task_success": "not measurable without a reference transcript",
        }
        return sample
    scored = scoring.word_error_rate(fixture.transcript, sample.get("text", ""))
    entities = scoring.entity_hits(fixture.entities, sample.get("text", ""))
    success = scoring.task_success(fixture.entities, sample.get("text", ""))
    sample["wer"] = scored["wer"]
    sample["wer_detail"] = scored
    sample["entity_accuracy"] = entities["accuracy"]
    sample["entity_detail"] = entities
    sample["task_success"] = success["success"]
    sample["task_detail"] = success
    sample["not_measured"] = {
        "wer": None,
        "entity_accuracy": None if entities["accuracy"] is not None else entities["not_measured"],
        "task_success": None if success.get("success") is not None else success.get("not_measured"),
    }
    return sample


def _tts_sample(task: dict, payload: dict, wall_ms: float, client: WorkerClient) -> dict:
    sample = _base_sample(task, payload, wall_ms)
    if not payload.get("ok"):
        sample["error"] = payload.get("error", {"code": "adapter_error"})
        sample["ok"] = False
        return sample
    timings = payload.get("timings", {})
    output = payload.get("output", {})
    sample.update({
        "latency_ms": timings.get("start_to_first_output_ms"),
        "complete_ms": timings.get("start_to_complete_ms"),
        "streaming": timings.get("streaming"),
        "audio_bytes": output.get("audio_bytes"),
        "text_chars": task.get("text_chars"),
        "real_time_factor": timings.get("real_time_factor"),
        "synthesised_audio_s": timings.get("synthesised_audio_s"),
        "not_measured": {"wer": "text-to-speech has no word error rate",
                         "entity_accuracy": "not applicable to synthesis",
                         "task_success": "not applicable to synthesis"},
    })
    return sample


def _turn_sample(task: dict, payload: dict, wall_ms: float) -> dict:
    sample = _base_sample(task, payload, wall_ms)
    if not payload.get("ok"):
        sample["error"] = payload.get("error", {"code": "adapter_error"})
        sample["ok"] = False
        return sample
    timings = payload.get("timings", {})
    sample.update({
        "latency_ms": timings.get("start_to_complete_ms"),
        "text": (payload.get("output") or {}).get("text", ""),
        "not_measured": {"wer": "a turn component is not an ASR system"},
    })
    return sample


def _e2e_sample(task: dict, asr_payload: dict, tts_payload: dict, wall_ms: float) -> dict:
    sample = {
        "task_id": task["task_id"],
        "phase": "e2e",
        "scope": task.get("scope"),
        "language": task.get("language"),
        "fixture_id": task.get("fixture_id"),
        "condition": task.get("condition"),
        "repeat": task.get("repeat"),
        "ok": bool(tts_payload.get("ok")),
        "wall_ms": wall_ms,
        "latency_ms": wall_ms,
        "first_audio_ms": wall_ms,
        "asr_ms": (asr_payload.get("timings") or {}).get("start_to_complete_ms"),
        "tts_ms": (tts_payload.get("timings") or {}).get("start_to_first_output_ms"),
        "audio_seconds": task.get("audio_seconds"),
        "text": (asr_payload.get("output") or {}).get("text", ""),
        "audio_bytes": (tts_payload.get("output") or {}).get("audio_bytes"),
        "turn_component": "supplied" if task.get("turn_used") else
                          "none: recognised text passed to synthesis, no assistant reasoning",
    }
    if not tts_payload.get("ok"):
        sample["error"] = tts_payload.get("error", {"code": "tts_failed"})
    return sample


# --------------------------------------------------------------------------- result assembly
def build_result(plan: Plan, config: RunConfig, outcome: RunOutcome) -> dict:
    from .results import assemble_result

    return assemble_result(plan, config, outcome, host=HostInfo(
        role=config.host_role,
        include_hostname=config.include_hostname,
        declared=load_declared(config.host_facts),
    ))


def run(config: RunConfig) -> tuple[dict, int]:
    from .decision import evaluate
    from .report import write_report
    from .results import write_result

    plan = build_plan(config, require_adapters=True)
    by_id = {fixture.id: fixture for fixture in plan.fixtures}
    for task in plan.tasks:
        task["_fixture"] = by_id.get(task.get("fixture_id"))
    (config.out_dir / "plan.json").write_text(
        json.dumps(plan.document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    outcome = execute(plan, config)
    result = build_result(plan, config, outcome)
    result["decision"] = evaluate(result, config.criteria)
    write_result(result, config.out_dir / "result.json")
    write_report(config.out_dir / "report.md", result)
    return result, outcome.exit_code


def clean_out_dir(path: Path) -> None:
    """Remove a previous fixtures directory so a re-run cannot inherit stale audio by accident."""
    fixtures = Path(path) / "fixtures"
    if fixtures.exists():
        shutil.rmtree(fixtures)


__all__ = [
    "Plan",
    "RunConfig",
    "RunOutcome",
    "WorkerClient",
    "WorkerError",
    "build_plan",
    "build_result",
    "execute",
    "reproduce_command",
    "run",
    "text_fixtures",
]
