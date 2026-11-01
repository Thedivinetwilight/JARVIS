"""The machine-readable result document: assembly, validation, and the honesty rules.

A result is only useful if a reader can tell what was measured from what was merely prepared, so
the document keeps three kinds of content strictly apart:

* **numbers**, each beside its ``n`` and its gate outcome;
* **``not_measured``**, a required key for every metric the benchmark knows about, holding either
  ``null`` (measured) or a reason string (not measured, and why);
* **``claims``**, fixed booleans stating what a run does *not* mean, so a completed run can never
  be quoted as "voice works".

Validation runs on every write. It refuses a document that promotes a value which did not clear its
gate, that claims a language tier or a fluency level, that lets a development-host run look
decision-eligible, that lets a network violation stand beside a non-rejected decision, or that
carries something credential-shaped or an absolute personal path.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import BENCH_VERSION, GATES, RESULT_SCHEMA
from . import languages as languages_module
from . import stats

REQUIRED_TOP_LEVEL = (
    "schema", "bench_version", "run", "identity", "host", "host_role",
    "comparable_to_owner_server", "decision_eligible", "plan", "methodology", "adapters",
    "phases", "overall", "languages", "conditions", "items", "failures", "network",
    "not_measured", "claims", "gate_status", "model_selection", "decision",
)

REQUIRED_NOT_MEASURED = (
    "wer", "entity_accuracy", "task_success", "latency_p50", "latency_p95",
    "throughput", "peak_memory", "power", "thermal", "gpu",
)

STATUSES = ("completed", "incomplete", "failed", "violated")
DECISIONS = ("not_evaluated", "rejected", "not_decidable", "eligible_for_owner_review")
HOST_ROLES = ("development", "dedicated_server")

SECRET_PATTERNS = (
    re.compile(r"jvs_[A-Za-z0-9]"),
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"(?i)\bpassword\s*[=:]"),
    re.compile(r"(?i)\bapi[_-]?key\s*[=:]"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{8,}"),
)

PERSONAL_PATH = re.compile(r"(?<!<)/home/[^/\s\"]+/")


class ResultError(Exception):
    """A result document is not acceptable. Carries every problem found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems[:5]))
        self.problems = problems


# --------------------------------------------------------------------------- helpers
def is_reported(block: dict | None) -> bool:
    """True only when a metric block cleared its gate and carries at least one sample."""
    if not isinstance(block, dict):
        return False
    return block.get("not_measured") is None and (block.get("n") or 0) > 0


def _latency(values) -> dict:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return stats.summarise_latency(numbers).to_dict()


def _ok_samples(samples: list[dict], phase: str, **filters) -> list[dict]:
    selected = [sample for sample in samples if sample.get("phase") == phase and sample.get("ok")]
    for key, value in filters.items():
        selected = [sample for sample in selected if sample.get(key) == value]
    return selected


def _first_output_ms(sample: dict, phase: str):
    """When the first output of an item was available. For synthesis that is the whole latency."""
    for key in ("first_output_ms", "first_audio_ms"):
        if sample.get(key) is not None:
            return sample[key]
    return sample.get("latency_ms") if phase == "tts" else None


def _phase_block(samples: list[dict], phase: str) -> dict:
    every = _ok_samples(samples, phase)
    acoustic = _ok_samples(samples, phase, scope="acoustic")
    language = _ok_samples(samples, phase, scope="language")
    return {
        "samples": len(every),
        "latency_all": _latency([sample.get("latency_ms") for sample in every]),
        "latency_acoustic": _latency([sample.get("latency_ms") for sample in acoustic]),
        "latency_language": _latency([sample.get("latency_ms") for sample in language]),
        "first_output": _latency([_first_output_ms(sample, phase) for sample in every]),
    }


def _cold_warm(samples: list[dict]) -> dict:
    """The first measured item of a phase is 'cold', the rest are 'warm'. Stated, never implied."""
    blocks: dict[str, dict] = {}
    for phase in ("asr", "tts", "turn", "e2e"):
        phase_samples = _ok_samples(samples, phase)
        if not phase_samples:
            continue
        warm = phase_samples[1:]
        blocks[phase] = {
            "cold_latency_ms": phase_samples[0].get("latency_ms"),
            "cold_note": "one observation: descriptive only, no percentile and no interval",
            "warm": _latency([sample.get("latency_ms") for sample in warm]),
            "warmup_items_discarded_before_measurement": True,
        }
    return blocks


def _throughput(samples: list[dict]) -> dict:
    asr_rtf = [sample.get("real_time_factor") for sample in _ok_samples(samples, "asr")
               if isinstance(sample.get("real_time_factor"), (int, float))]
    tts_rtf = [sample.get("real_time_factor") for sample in _ok_samples(samples, "tts")
               if isinstance(sample.get("real_time_factor"), (int, float))]
    audio_seconds = sum(float(sample.get("audio_seconds") or 0.0)
                        for sample in _ok_samples(samples, "asr"))
    wall_seconds = sum(float(sample.get("wall_ms") or 0.0)
                       for sample in _ok_samples(samples, "asr")) / 1000.0
    return {
        "asr_audio_seconds": round(audio_seconds, 3),
        "asr_wall_seconds": round(wall_seconds, 3),
        "asr_audio_seconds_per_wall_second": (round(audio_seconds / wall_seconds, 4)
                                              if wall_seconds > 0 else None),
        "asr_real_time_factor_median": (round(stats.percentile(asr_rtf, 0.5), 4)
                                        if asr_rtf else None),
        "tts_real_time_factor_median": (round(stats.percentile(tts_rtf, 0.5), 4)
                                        if tts_rtf else None),
        "n": len(asr_rtf) + len(tts_rtf),
        "not_measured": None if (asr_rtf or tts_rtf) else "no throughput sample",
    }


def _conditions(samples: list[dict]) -> list[dict]:
    names = sorted({sample.get("condition") for sample in samples if sample.get("condition")})
    blocks = []
    for condition in names:
        entry = {"condition": condition, "by_phase": {}}
        for phase in ("asr", "e2e"):
            selected = [sample for sample in _ok_samples(samples, phase)
                        if sample.get("condition") == condition]
            if selected:
                entry["by_phase"][phase] = _latency([sample.get("latency_ms") for sample in selected])
        blocks.append(entry)
    return blocks


# --------------------------------------------------------------------------- languages
def _language_blocks(samples: list[dict], plan_document: dict, scheduled: list[str]) -> list[dict]:
    counts: dict[str, int] = {}
    for fixture in plan_document["fixtures"]["licensed"]:
        if fixture.get("language"):
            counts[fixture["language"]] = counts.get(fixture["language"], 0) + 1
    matrix = [languages_module.Language(code=item["code"], name=item["name"],
                                       proposal_status=item["proposal_status"])
              for item in plan_document["languages"]["matrix"]]
    entries = languages_module.build_entries(matrix, scheduled=scheduled, fixture_counts=counts)

    for entry in entries:
        if not entry.scheduled:
            continue  # build_entries already recorded the reason and "no inherited result"
        if entry.fixtures == 0:
            continue  # scheduled but nothing to run it on: still not_run, reason already present

        language_samples = _ok_samples(samples, "asr", scope="language", language=entry.code)
        e2e_samples = _ok_samples(samples, "e2e", scope="language", language=entry.code)
        entry.samples = {"asr": len(language_samples), "e2e": len(e2e_samples),
                         "fixtures": entry.fixtures}
        if not language_samples:
            entry.status = languages_module.STATUS_NOT_MEASURED
            entry.not_measured = {
                "everything": "licensed fixtures exist for this language but no recognition "
                              "sample succeeded: see failures; nothing is inferred from other "
                              "languages",
            }
            continue

        latency = _latency([sample.get("latency_ms") for sample in language_samples])
        wers = [sample.get("wer") for sample in language_samples
                if isinstance(sample.get("wer"), (int, float))]
        wer = stats.summarise_wer(wers, label=f"wer:{entry.code}")
        entity_values = [sample.get("entity_accuracy") for sample in language_samples
                         if isinstance(sample.get("entity_accuracy"), (int, float))]
        successes = [sample.get("task_success") for sample in language_samples
                     if isinstance(sample.get("task_success"), bool)]
        entity = ({"n": len(entity_values), "mean": round(sum(entity_values) / len(entity_values), 6),
                   "ci95": None, "not_measured": None if len(entity_values) >= stats.MIN_SAMPLES_RATE
                   else "insufficient_samples", "gate": f"n >= {stats.MIN_SAMPLES_RATE}"}
                  if entity_values else
                  {"n": 0, "mean": None, "ci95": None, "not_measured": "no_declared_entities",
                   "gate": f"n >= {stats.MIN_SAMPLES_RATE}"})
        success = (stats.summarise_rate(sum(1 for value in successes if value), len(successes))
                   if successes else
                   {"successes": 0, "n": 0, "value": None, "ci95": None,
                    "not_measured": "no_declared_entities", "gate": f"n >= {stats.MIN_SAMPLES_RATE}"})

        reported = {name: block for name, block in
                    (("wer", wer), ("latency", latency), ("entity_accuracy", entity),
                     ("task_success", success)) if is_reported(block)}
        if not reported:
            entry.status = languages_module.STATUS_NOT_MEASURED
            entry.below_gate = {
                name: {**block, "gate_cleared": False}
                for name, block in (("wer", wer), ("latency", latency),
                                    ("entity_accuracy", entity), ("task_success", success))
                if block.get("n")
            }
            entry.not_measured = {
                "everything": "samples were collected from this language's own fixtures but no "
                              "metric cleared its pre-registered sample gate",
            }
            entry.notes.append(
                "point estimates are kept under below_gate for inspection and must not be quoted "
                "as a result")
            continue

        entry.status = languages_module.STATUS_MEASURED
        entry.latency = latency
        entry.wer = wer
        entry.entity_accuracy = entity
        entry.task_success = success
        entry.not_measured = {
            key: value for key, value in {
                "wer": wer.get("not_measured"),
                "entity_accuracy": entity.get("not_measured"),
                "task_success": success.get("not_measured"),
                "latency_p50": latency.get("not_measured"),
                "throughput": "per-language throughput is not separated from the phase total",
            }.items() if value
        }
        entry.notes.append(
            "measured from this language's own licensed fixtures only; the tier stays "
            "not_assigned because C-03-AC4 needs a support bar the owner has not set (Q19)")
    return [entry.to_dict() for entry in entries]


def _aggregate_reported(document: dict, metric: str) -> bool:
    return any(is_reported(entry.get(metric)) for entry in document.get("languages", []))


# --------------------------------------------------------------------------- assembly
def assemble_result(plan, config, outcome, *, host) -> dict:
    plan_document = plan.document
    samples = outcome.samples
    scheduled = plan_document["languages"]["scheduled"]
    peaks = [float(metrics.get("peak_rss_mb") or 0.0)
             for metrics in outcome.worker_metrics.values()]
    by_phase = {phase: _phase_block(samples, phase)
                for phase in ("asr", "tts", "turn", "e2e")
                if phase in plan_document["phases"] or _ok_samples(samples, phase)}
    latency_reported = any(is_reported(block["latency_all"]) for block in by_phase.values())

    overall = {
        "by_phase": by_phase,
        "throughput": _throughput(samples),
        "peak_worker_memory_mb": round(max(peaks), 1) if peaks else None,
        "peak_worker_memory_note": "maximum resident set size observed by getrusage in the adapter "
                                   "worker; the harness process is not included",
        "cold_warm": _cold_warm(samples),
        "latency_reported": latency_reported,
    }

    power = host.measure_power()
    host_document = host.to_dict()
    host_document["power"]["measurement"] = power

    language_blocks = _language_blocks(samples, plan_document, scheduled)
    document: dict = {
        "schema": RESULT_SCHEMA,
        "bench_version": BENCH_VERSION,
        "run": {
            "id": f"run_{plan_document['plan_hash'][:12]}",
            "started_at": plan_document.get("created_at"),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": outcome.status,
            "exit_code": outcome.exit_code,
            "samples": len(samples),
            "failures": len(outcome.failures),
        },
        "identity": {
            "plan_hash": plan_document["plan_hash"],
            "git": plan_document["git"],
            "bench_version": BENCH_VERSION,
            "reproduce_command": plan_document.get("reproduce_command"),
        },
        "host": host_document,
        "host_role": config.host_role,
        # The benchmark cannot tell what machine it is on: the role is the operator's declaration.
        "host_role_source": "operator_declaration",
        "comparable_to_owner_server": config.host_role == "dedicated_server",
        # Fit to be judged: the owner's own host, a completed run, no boundary breach, and at least
        # one scheduled language. It still does not mean "decided" — see the decision block.
        "decision_eligible": (config.host_role == "dedicated_server"
                              and outcome.status == "completed"
                              and not (outcome.network or {}).get("violations")
                              and bool(scheduled)),
        "plan": {
            "plan_hash": plan_document["plan_hash"],
            "host_role": plan_document["host_role"],
            "seed": plan_document["seed"],
            "repeat": plan_document["repeat"],
            "warmup": plan_document["warmup"],
            "phases": plan_document["phases"],
            "task_count": plan_document["task_count"],
            "languages_scheduled": scheduled,
            "fixture_pack_supplied": plan_document["fixtures"]["pack_supplied"],
            "fixtures_synthetic": len(plan_document["fixtures"]["synthetic"]),
            "fixtures_licensed": len(plan_document["fixtures"]["licensed"]),
            "allow_loopback_ipc": plan_document["allow_loopback_ipc"],
            "hash_models": plan_document["hash_models"],
            "timing_boundaries": plan_document["timing_boundaries"],
            "measurement_scope": plan_document["measurement_scope"],
        },
        "methodology": plan_document["methodology"],
        "adapters": {
            "declared": plan_document["artifacts"]["adapters"],
            "models": plan_document["artifacts"]["models"],
            "runtime": {
                label: {
                    "load_ms": metrics.get("load_ms"),
                    "restarts": metrics.get("restarts"),
                    "descriptor": metrics.get("adapter"),
                }
                for label, metrics in outcome.worker_metrics.items()
            },
        },
        "phases": {
            phase: {
                "ran": bool(_ok_samples(samples, phase)),
                "samples": len(_ok_samples(samples, phase)),
                "failures": len([failure for failure in outcome.failures
                                 if failure.get("phase") == phase]),
            }
            for phase in ("asr", "tts", "turn", "e2e")
        },
        "overall": overall,
        "languages": language_blocks,
        "conditions": _conditions(samples),
        "items": samples[:20000],
        "items_truncated": len(samples) > 20000,
        "failures": outcome.failures,
        "network": dict(outcome.network),
        "not_measured": {},
        "claims": {
            "voice_capability_implemented": False,
            "benchmark_is_not_a_capability": True,
            "model_selected": False,
            "provider_selected": False,
            "hardware_purchased": False,
            "statistical_rate_established": False,
            "language_fluency_claimed": False,
            "results_apply_to_other_languages": False,
        },
        "gate_status": dict(GATES),
        "model_selection": "not_performed",
        "decision": {"decision": "not_evaluated", "reasons": ["decision not evaluated"]},
    }
    document["not_measured"] = _not_measured(plan_document, document, host_document, power,
                                             latency_reported, scheduled)
    return document


def _not_measured(plan_document: dict, document: dict, host_document: dict, power: dict,
                  latency_reported: bool, scheduled: list[str]) -> dict:
    scheduled_entries = [entry for entry in document["languages"] if entry.get("scheduled")]
    with_samples = [entry for entry in scheduled_entries
                    if (entry.get("samples") or {}).get("asr")]
    if _aggregate_reported(document, "wer"):
        language_reason = None
    elif not plan_document["fixtures"]["pack_supplied"]:
        language_reason = ("synthetic fixtures contain no speech and carry no reference "
                           "transcript; supply a licensed fixture pack to measure word error rate")
    elif not scheduled:
        language_reason = "a licensed pack was supplied but no language was scheduled"
    elif with_samples:
        language_reason = (
            f"recognition samples exist per language but fewer than {stats.MIN_SAMPLES_WER} items "
            f"per language cleared the WER sample gate; the point estimates are kept under "
            f"below_gate and must not be quoted as a result")
    else:
        language_reason = ("licensed fixtures were supplied but no language produced a "
                           "recognition sample: see failures")

    latency_gate = (f"no phase reached the pre-registered sample gate for a percentile "
                    f"(n >= {stats.MIN_SAMPLES_LATENCY} per cell)")

    return {
        "wer": language_reason,
        "entity_accuracy": None if _aggregate_reported(document, "entity_accuracy") else
        "no measured recognition sample declared critical entities",
        "task_success": None if _aggregate_reported(document, "task_success") else
        "no measured recognition sample declared critical entities",
        "latency_p50": None if latency_reported else latency_gate,
        "latency_p95": None if latency_reported else latency_gate,
        "throughput": document["overall"]["throughput"]["not_measured"],
        "peak_memory": None if document["overall"]["peak_worker_memory_mb"]
        else "no adapter worker ran",
        "power": None if power.get("status") == "available" else power.get("reason"),
        "thermal": None if host_document["thermal"].get("status") == "available"
        else host_document["thermal"].get("reason"),
        "gpu": None if host_document["gpu"].get("status") == "available"
        else host_document["gpu"].get("reason"),
    }


# --------------------------------------------------------------------------- validation
def validate_result(document: dict) -> list[str]:
    problems: list[str] = []
    for key in REQUIRED_TOP_LEVEL:
        if key not in document:
            problems.append(f"missing top-level key: {key}")
    if problems:
        return problems

    if document.get("schema") != RESULT_SCHEMA:
        problems.append(f"schema must be {RESULT_SCHEMA!r}")
    if document.get("bench_version") != BENCH_VERSION:
        problems.append(f"bench_version must be {BENCH_VERSION!r}")

    role = document.get("host_role")
    if role not in HOST_ROLES:
        problems.append(f"host_role must be one of {HOST_ROLES}, got {role!r}")
    if role == "development":
        if document.get("comparable_to_owner_server") is not False:
            problems.append("a development-host run is never comparable to the owner's server")
        if document.get("decision_eligible") is not False:
            problems.append("a development-host run is never decision-eligible")
    elif role == "dedicated_server":
        if document.get("comparable_to_owner_server") is not True:
            problems.append("a dedicated_server run must say it is comparable to the owner's server")

    run = document.get("run") or {}
    if run.get("status") not in STATUSES:
        problems.append(f"run.status must be one of {STATUSES}, got {run.get('status')!r}")

    not_measured = document.get("not_measured") or {}
    for key in REQUIRED_NOT_MEASURED:
        if key not in not_measured:
            problems.append(f"not_measured is missing the explicit key {key!r}")
        else:
            value = not_measured[key]
            if value is not None and (not isinstance(value, str) or not value.strip()):
                problems.append(f"not_measured[{key!r}] must be null or a non-empty reason")

    # Both directions: a reason may not hide a measurement, and a measurement may not be hidden.
    for metric in ("wer", "entity_accuracy", "task_success"):
        reported = _aggregate_reported(document, metric)
        if reported and not_measured.get(metric) is not None:
            problems.append(f"{metric} is reported per language but marked not measured")
        if not reported and not_measured.get(metric) is None:
            problems.append(f"{metric} is marked measured but no language reports it")

    overall = document.get("overall") or {}
    latency_reported = bool(overall.get("latency_reported"))
    for key in ("latency_p50", "latency_p95"):
        if latency_reported and not_measured.get(key) is not None:
            problems.append(f"{key} is reported but marked not measured")
        if not latency_reported and not_measured.get(key) is None:
            problems.append(f"{key} is marked measured but no phase reports it")

    host = document.get("host") or {}
    for key, block in (("power", host.get("power")), ("thermal", host.get("thermal")),
                       ("gpu", host.get("gpu"))):
        status = (block or {}).get("status")
        if status == "available" and not_measured.get(key) is not None:
            problems.append(f"host reports {key} available but not_measured hides it")
        if status != "available" and not_measured.get(key) is None:
            problems.append(f"host does not expose {key} but not_measured does not say so")

    for entry in document.get("languages", []):
        if entry.get("status") == languages_module.STATUS_MEASURED:
            if not (entry.get("samples") or {}).get("asr"):
                problems.append(f"{entry.get('code')}: measured without a recognition sample")
            if not any(is_reported(entry.get(metric)) for metric in
                       ("wer", "latency", "entity_accuracy", "task_success")):
                problems.append(f"{entry.get('code')}: measured without a gate-cleared metric")
    problems.extend(languages_module.validate_entries(document.get("languages", [])))

    claims = document.get("claims") or {}
    for key in ("voice_capability_implemented", "model_selected", "provider_selected",
                "hardware_purchased", "statistical_rate_established", "language_fluency_claimed",
                "results_apply_to_other_languages"):
        if claims.get(key) is not False:
            problems.append(f"claims[{key!r}] must be false")
    if claims.get("benchmark_is_not_a_capability") is not True:
        problems.append("claims['benchmark_is_not_a_capability'] must be true")

    if (document.get("gate_status") or {}).get("G-O1") != "blocked":
        problems.append("gate_status must keep G-O1 blocked")
    if document.get("model_selection") != "not_performed":
        problems.append("model_selection must be 'not_performed'")

    network = document.get("network") or {}
    decision = (document.get("decision") or {}).get("decision")
    if network.get("violations"):
        if run.get("status") != "violated":
            problems.append("network violations require run.status == 'violated'")
        if decision != "rejected":
            problems.append("network violations require decision == 'rejected'")
        if document.get("decision_eligible") is not False:
            problems.append("a network violation is never decision-eligible")
    if decision not in DECISIONS:
        problems.append(f"decision must be one of {DECISIONS}, got {decision!r}")
    if decision == "eligible_for_owner_review":
        if document.get("decision_eligible") is not True:
            problems.append("an eligible run must also be marked decision-eligible")
        if role != "dedicated_server":
            problems.append("only a dedicated_server run can be eligible for owner review")
        if run.get("status") != "completed":
            problems.append("only a completed run can be eligible for owner review")
        if not (document.get("plan") or {}).get("languages_scheduled"):
            problems.append("a run with no scheduled language is not eligible for owner review")
    for failure in document.get("failures", []):
        if not failure.get("task_id") or not failure.get("code"):
            problems.append("every failure must carry task_id and code")
            break

    text = json.dumps(document, ensure_ascii=False)
    for pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            problems.append(f"result contains something credential-shaped: {match.group(0)[:24]!r}")
    personal = PERSONAL_PATH.search(text)
    if personal:
        problems.append("result contains an absolute personal path; sanitise it before writing")
    return problems


def write_result(document: dict, path: Path) -> None:
    """Validate, then write atomically. An invalid document is never written."""
    problems = validate_result(document)
    if problems:
        raise ResultError(problems)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def load_result(path: Path) -> dict:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = validate_result(document)
    if problems:
        raise ResultError(problems)
    return document


__all__ = [
    "PERSONAL_PATH",
    "REQUIRED_NOT_MEASURED",
    "REQUIRED_TOP_LEVEL",
    "ResultError",
    "assemble_result",
    "is_reported",
    "load_result",
    "validate_result",
    "write_result",
]
