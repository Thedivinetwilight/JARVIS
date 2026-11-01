"""The sandboxed adapter worker: one process, one adapter, no network.

Protocol (one JSON object per line):

* worker → parent ``{"event": "ready", "adapter": {...}, "sandbox": {...}, "pid": ..., "load_ms": ...}``
  or ``{"event": "load_error", "error": {...}}``;
* parent → worker ``{"task_id": "...", "op": "asr"|"tts"|"turn", ...}`` or ``{"op": "stop"}``;
* worker → parent ``{"task_id": "...", "ok": true, "output": {...}, "timings": {...},
  "sandbox": {...}, "peak_rss_mb": ...}``;
* worker → parent ``{"event": "exit", ...}`` then exit 0 (or 5 if the network was attempted).

Two details matter for trustworthy numbers:

1. the sandbox is installed **before** the adapter module is imported, so import-time network
   access is caught too;
2. the adapter's ``stdout`` is redirected to ``stderr`` before import, so a chatty adapter cannot
   corrupt the protocol, and the parent keeps its output as a log.
"""

from __future__ import annotations

import importlib.util
import json
import os
import resource
import sys
import time
import traceback
from pathlib import Path

from . import EXIT_NETWORK_VIOLATION, EXIT_OK, EXIT_USAGE
from .isolation import AdapterSandbox

KIND_TO_CALLABLE = {"asr": "transcribe", "tts": "synthesise", "turn": "respond"}
MAX_TEXT_CHARS = 5000


def _peak_rss_mb() -> float:
    """Peak resident set size of this process, in MB. Linux reports KiB; macOS reports bytes."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":  # pragma: no cover - not the target platform
        return round(usage / (1024 * 1024), 1)
    return round(usage / 1024, 1)


def _load_adapter(path: Path):
    spec = importlib.util.spec_from_file_location("jarvis_bench_candidate_adapter", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import adapter file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _adapter_descriptor(module) -> dict:
    descriptor = getattr(module, "ADAPTER", None)
    if not isinstance(descriptor, dict):
        raise ValueError("adapter file must define ADAPTER = {...}")
    kind = descriptor.get("kind")
    if kind not in KIND_TO_CALLABLE:
        raise ValueError(f"ADAPTER['kind'] must be one of {sorted(KIND_TO_CALLABLE)}, got {kind!r}")
    return {
        "name": str(descriptor.get("name") or "unnamed"),
        "version": str(descriptor.get("version") or "unversioned"),
        "kind": kind,
        "languages_claimed": [str(code) for code in descriptor.get("languages", [])],
        "streaming": bool(descriptor.get("streaming")),
        "notes": str(descriptor.get("notes") or ""),
        # A claim is a claim: it is recorded, and the benchmark still measures what it measures.
        "languages_claim_recorded_not_measured": True,
    }


def _sanitise_output(kind: str, payload) -> dict:
    if not isinstance(payload, dict):
        raise ValueError(f"adapter returned {type(payload).__name__}, expected dict")
    output: dict = {"keys": sorted(str(key) for key in payload)}
    if kind in ("asr", "turn"):
        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError("adapter output needs a string 'text'")
        output["text"] = text[:MAX_TEXT_CHARS]
        output["text_truncated"] = len(text) > MAX_TEXT_CHARS
        if isinstance(payload.get("language"), str):
            output["language"] = payload["language"][:32]
    if kind == "tts":
        audio_bytes = payload.get("audio_bytes")
        if not isinstance(audio_bytes, int) or audio_bytes < 0:
            raise ValueError("tts adapter output needs an integer 'audio_bytes'")
        output["audio_bytes"] = audio_bytes
        if isinstance(payload.get("sample_rate"), int):
            output["sample_rate"] = payload["sample_rate"]
        if isinstance(payload.get("duration_s"), (int, float)):
            output["duration_s"] = round(float(payload["duration_s"]), 4)
    return output


def _run_task(module, descriptor: dict, task: dict) -> dict:
    kind = descriptor["kind"]
    expected = KIND_TO_CALLABLE[kind]
    op = task.get("op")
    if op != kind:
        raise ValueError(f"worker for {kind!r} asked to run {op!r}")
    request = {
        "audio_path": task.get("audio_path"),
        "text": task.get("text"),
        "language": task.get("language") or "",
        "condition": task.get("condition") or "",
        "sample_rate": task.get("sample_rate"),
        "repeat": task.get("repeat"),
    }
    streaming_missing = descriptor["streaming"] is False
    name = expected
    if not streaming_missing and hasattr(module, f"{expected}_stream"):
        name = f"{expected}_stream"

    started = time.perf_counter()
    if name.endswith("_stream"):
        iterator = getattr(module, name)(request)
        chunks = 0
        first_at = None
        last = None
        completed = None
        for item in iterator:
            now = time.perf_counter()
            chunks += 1
            if first_at is None:
                first_at = now
            last = now
            completed = item
        finished = time.perf_counter()
        output = _sanitise_output(kind, completed if completed is not None else {})
        timings = {
            "start_to_first_output_ms": round(((first_at or finished) - started) * 1000, 3),
            "start_to_complete_ms": round(((last or finished) - started) * 1000, 3),
            "stream_chunks": chunks,
            "streaming": True,
        }
    else:
        result = getattr(module, expected)(request)
        finished = time.perf_counter()
        output = _sanitise_output(kind, result)
        elapsed = round((finished - started) * 1000, 3)
        timings = {
            "start_to_first_output_ms": elapsed,
            "start_to_complete_ms": elapsed,
            "stream_chunks": 1,
            "streaming": False,
        }
    timings["adapter_call_ms"] = timings["start_to_complete_ms"]
    if kind == "tts" and output.get("sample_rate") and output.get("audio_bytes"):
        seconds = output["audio_bytes"] / (2 * float(output["sample_rate"]))
        timings["synthesised_audio_s"] = round(seconds, 4)
        if timings["adapter_call_ms"] > 0:
            timings["real_time_factor"] = round(seconds / (timings["adapter_call_ms"] / 1000.0), 4)
    return {"ok": True, "output": output, "timings": timings}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python3 -m app.bench.worker")
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--allow-loopback-ipc", action="store_true")
    arguments = parser.parse_args(argv)

    protocol = sys.stdout
    # Adapter chatter must not corrupt the protocol stream.
    sys.stdout = sys.stderr

    def emit(payload: dict) -> None:
        protocol.write(json.dumps(payload, sort_keys=True) + "\n")
        protocol.flush()

    sandbox = AdapterSandbox(allow_loopback=arguments.allow_loopback_ipc)
    sandbox.install()
    os.environ["PYTHONNOUSERSITE"] = "1"

    module = None
    descriptor: dict = {}
    load_started = time.perf_counter()
    try:
        module = _load_adapter(Path(arguments.adapter))
        descriptor = _adapter_descriptor(module)
    except Exception as exc:
        emit({
            "event": "load_error",
            "error": {"type": type(exc).__name__, "message": str(exc)[:500],
                      "code": "adapter_load_failed"},
            "traceback_tail": traceback.format_exc(limit=3).splitlines()[-3:],
            "sandbox": sandbox.status(),
        })
        return EXIT_USAGE
    load_ms = round((time.perf_counter() - load_started) * 1000, 3)
    emit({
        "event": "ready",
        "adapter": descriptor,
        "pid": os.getpid(),
        "load_ms": load_ms,
        "peak_rss_mb_after_load": _peak_rss_mb(),
        "sandbox": sandbox.status(),
    })

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            task = json.loads(line)
        except json.JSONDecodeError:
            emit({"event": "protocol_error", "message": "task line was not JSON"})
            continue
        if task.get("op") == "stop":
            break
        task_id = task.get("task_id")
        try:
            result = _run_task(module, descriptor, task)
            result.update({
                "task_id": task_id,
                "sandbox": sandbox.status(),
                "peak_rss_mb": _peak_rss_mb(),
            })
            emit(result)
        except Exception as exc:  # adapter failure is data, not a crash
            emit({
                "task_id": task_id,
                "ok": False,
                "error": {"type": type(exc).__name__, "message": str(exc)[:500],
                          "code": "adapter_error"},
                "sandbox": sandbox.status(),
                "peak_rss_mb": _peak_rss_mb(),
            })
        if sandbox.violated:
            # Fail closed: the run ends here, with the attempt recorded.
            emit({"event": "violation", "sandbox": sandbox.status()})
            break

    emit({"event": "exit", "sandbox": sandbox.status(), "peak_rss_mb": _peak_rss_mb()})
    return EXIT_NETWORK_VIOLATION if sandbox.violated else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
