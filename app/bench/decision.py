"""The rejection/decision protocol.

This module exists so that "we benchmarked it" cannot quietly become "we picked it". It answers one
question — *does this run clear a bar the owner wrote down in advance?* — and it may only answer:

``rejected``                 a bar was breached, or the run broke a boundary;
``not_decidable``            the run cannot answer the question (no bar, wrong host, gaps, failures);
``eligible_for_owner_review`` every bar was cleared, on the owner's own host, with nothing hidden.

It never returns "selected", "recommended" or "winner", and it never compares two candidates: a
comparison needs the same criteria applied to both, and that is a document a human writes.

Gate **G-O1** remains ``blocked`` regardless of the verdict. A cleared benchmark is not a decision:
the M5 workload review, the G-W and G-C gates, the local-boundary acceptance and the owner's own
approval (A9) are all still outstanding, and no run of this benchmark can supply any of them.

The criteria file is operator-supplied JSON, deliberately small so its absence is obvious::

    {
      "schema": "jarvis.voice-bench-criteria/1",
      "criteria_id": "owner-workload-2026-11",
      "min_samples_per_language": 20,
      "languages_required": ["en"],
      "thresholds": {"wer_mean_max": 0.08, "latency_p50_ms_max": 1500,
                     "latency_p95_ms_max": 3000, "task_success_min": 0.90}
    }

There is no thresholds default. A missing bar produces ``not_decidable``, never a pass.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .adapter import display_path

CRITERIA_SCHEMA = "jarvis.voice-bench-criteria/1"

DECISION_REJECTED = "rejected"
DECISION_NOT_DECIDABLE = "not_decidable"
DECISION_ELIGIBLE = "eligible_for_owner_review"
DECISION_NOT_EVALUATED = "not_evaluated"
DECISIONS = (DECISION_NOT_EVALUATED, DECISION_REJECTED, DECISION_NOT_DECIDABLE, DECISION_ELIGIBLE)

#: Threshold names understood by this module, with the direction that counts as a pass.
THRESHOLDS: dict[str, str] = {
    "wer_mean_max": "max",
    "latency_p50_ms_max": "max",
    "latency_p95_ms_max": "max",
    "task_success_min": "min",
    "entity_accuracy_min": "min",
    "peak_memory_mb_max": "max",
}

#: Keys a criteria file may not contain. A bar file that names a product is a purchase order.
FORBIDDEN_KEYS = ("tier", "fluency", "language_tier", "support_tier", "selected", "selection",
                  "winner", "recommended", "recommendation", "vendor", "model", "preferred",
                  "purchase", "budget")

#: What still stands between an ``eligible_for_owner_review`` verdict and gate G-O1 opening. Listed
#: in the decision itself so the string cannot be quoted without them.
OUTSTANDING_INPUTS = (
    "M5 workload review complete and reviewed",
    "G-W and G-C passed with their own evidence",
    "local-first boundary accepted",
    "dedicated-server baseline and per-language latency measured on the owner's host",
    "owner approval (A9)",
)


class CriteriaError(Exception):
    """The criteria file is unusable. Never downgraded into a pass."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_criteria(path: Path) -> dict:
    """Read and validate the operator's bar. Raises :class:`CriteriaError` on anything doubtful."""
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CriteriaError(f"cannot read criteria file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CriteriaError(f"criteria file is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise CriteriaError("criteria document must be a JSON object")
    problems = validate_criteria(document)
    if problems:
        raise CriteriaError("; ".join(problems))
    document = dict(document)
    document["_sha256"] = _sha256(path)
    document["_display_path"] = display_path(path)
    return document


def validate_criteria(document: dict) -> list[str]:
    problems: list[str] = []
    if document.get("schema") != CRITERIA_SCHEMA:
        problems.append(f"criteria schema must be {CRITERIA_SCHEMA!r}")
    if not str(document.get("criteria_id") or "").strip():
        problems.append("criteria_id is required: a bar without a name cannot be cited")
    for key in document:
        if key.startswith("_"):
            continue
        if key.lower() in FORBIDDEN_KEYS:
            problems.append(f"criteria key {key!r} is not a measurement bar; this tool does not "
                            f"select a product, a language tier or a server")
    allowed = {"schema", "criteria_id", "min_samples_per_language", "languages_required",
               "thresholds", "notes"}
    unknown = sorted(set(document) - allowed - {key for key in document if key.startswith("_")})
    if unknown:
        problems.append(f"unknown criteria keys: {', '.join(unknown)}")
    thresholds = document.get("thresholds")
    if not isinstance(thresholds, dict) or not thresholds:
        problems.append("thresholds must be a non-empty object; there are no default bars")
    else:
        unknown_thresholds = sorted(set(thresholds) - set(THRESHOLDS))
        if unknown_thresholds:
            problems.append(f"unknown thresholds: {', '.join(unknown_thresholds)}")
        for name, value in thresholds.items():
            if name in THRESHOLDS and (not isinstance(value, (int, float)) or value != value):
                problems.append(f"threshold {name!r} must be a number")
            elif name in THRESHOLDS and value < 0:
                problems.append(f"threshold {name!r} must not be negative")
    minimum = document.get("min_samples_per_language", 0)
    if not isinstance(minimum, int) or minimum < 0:
        problems.append("min_samples_per_language must be a non-negative integer")
    required = document.get("languages_required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        problems.append("languages_required must be a list of language codes")
    return problems


# --------------------------------------------------------------------------- observation
def _observed(result: dict, name: str) -> dict:
    """A run-wide observation (as opposed to a per-language one)."""
    if name == "peak_memory_mb_max":
        value = (result.get("overall") or {}).get("peak_worker_memory_mb")
        return {"value": value, "n": 1 if value else 0,
                "not_measured": None if value else "no adapter worker ran"}
    raise CriteriaError(f"threshold {name!r} is not measured per run")


def _language_observation(entry: dict, name: str) -> dict:
    if name == "wer_mean_max":
        block = entry.get("wer") or {}
        return {"value": block.get("mean"), "n": block.get("n", 0),
                "not_measured": block.get("not_measured")}
    if name == "latency_p50_ms_max":
        block = entry.get("latency") or {}
        return {"value": block.get("p50_ms"), "n": block.get("n", 0),
                "not_measured": block.get("not_measured")}
    if name == "latency_p95_ms_max":
        block = entry.get("latency") or {}
        return {"value": block.get("p95_ms"), "n": block.get("n", 0),
                "not_measured": block.get("not_measured")}
    if name == "task_success_min":
        block = entry.get("task_success") or {}
        return {"value": block.get("value"), "n": block.get("n", 0),
                "not_measured": block.get("not_measured")}
    if name == "entity_accuracy_min":
        block = entry.get("entity_accuracy") or {}
        return {"value": block.get("mean"), "n": block.get("n", 0),
                "not_measured": block.get("not_measured")}
    if name == "peak_memory_mb_max":
        return {"value": None, "n": 0,
                "not_measured": "peak memory is reported per run, not per language"}
    raise CriteriaError(f"unknown threshold {name!r}")


def _passes(name: str, value: float, threshold: float) -> bool:
    return value <= threshold if THRESHOLDS[name] == "max" else value >= threshold


def evaluate(result: dict, criteria_path: Path | None) -> dict:
    """Return the decision block. Never raises for a bad bar: it fails closed instead."""
    reasons: list[str] = []
    blocks: list[dict] = []
    run = result.get("run") or {}
    network = result.get("network") or {}
    host_role = result.get("host_role")

    def finish(decision: str, extra: dict | None = None) -> dict:
        document = {
            "decision": decision,
            "reasons": reasons or ["no reason recorded"],
            "host_role": host_role,
            "comparable_to_owner_server": bool(result.get("comparable_to_owner_server")),
            "criteria": criteria_summary,
            "per_language": blocks,
            "gate_status": {"G-O1": "blocked"},
            "model_selection": "not_performed",
            "outstanding_before_voice_implementation": list(OUTSTANDING_INPUTS),
            "decision_is_not_a_purchase": True,
        }
        if extra:
            document.update(extra)
        return document

    criteria_summary: dict = {"supplied": False, "path": None, "sha256": None, "criteria_id": None,
                              "error": None}
    criteria: dict | None = None
    if criteria_path is not None:
        try:
            criteria = load_criteria(Path(criteria_path))
        except CriteriaError as exc:
            criteria_summary["error"] = str(exc)[:400]
            reasons.append(f"criteria file rejected: {criteria_summary['error']}")
            return finish(DECISION_NOT_DECIDABLE)
        criteria_summary.update({
            "supplied": True,
            "path": criteria["_display_path"],
            "sha256": criteria["_sha256"],
            "criteria_id": criteria["criteria_id"],
            "thresholds": criteria["thresholds"],
            "min_samples_per_language": criteria.get("min_samples_per_language", 0),
            "languages_required": criteria.get("languages_required", []),
        })

    # 1. Boundary breaches decide immediately, in one direction only.
    if network.get("violations"):
        reasons.append(f"{len(network['violations'])} denied network attempt(s) were made by the "
                       f"adapter: the local-only boundary is not negotiable")
        return finish(DECISION_REJECTED, {"boundary": "network"})
    if run.get("status") == "failed":
        reasons.append("the run produced no measured sample: there is nothing to decide")
        return finish(DECISION_NOT_DECIDABLE)

    # 2. Anything that leaves the question unanswerable.
    if criteria is None:
        reasons.append("no criteria file was supplied: the owner has not set a bar, so no verdict "
                       "can be justified")
        return finish(DECISION_NOT_DECIDABLE)
    if run.get("status") != "completed":
        reasons.append(f"run status is {run.get('status')!r}: a run with failures cannot clear a bar")
    if host_role != "dedicated_server":
        reasons.append("this run was made on a development host, so its timing is not evidence "
                       "about the owner's dedicated server (Q20 is still open)")
    scheduled = (result.get("plan") or {}).get("languages_scheduled") or []
    if not scheduled:
        reasons.append("no language was scheduled, so there is no per-language result to judge")

    required = criteria.get("languages_required", [])
    missing_required = [code for code in required if code not in scheduled]
    if missing_required:
        reasons.append("the criteria require languages that were not scheduled: "
                       + ", ".join(sorted(missing_required)))

    minimum = int(criteria.get("min_samples_per_language", 0))
    thresholds = criteria.get("thresholds", {})
    for entry in result.get("languages", []):
        code = entry.get("code")
        if code not in scheduled:
            continue
        language_reasons: list[str] = []
        if entry.get("status") != "measured":
            language_reasons.append(f"status {entry.get('status')!r}: no metric cleared its gate")
            blocks.append({"language": code, "verdict": DECISION_NOT_DECIDABLE,
                           "reasons": language_reasons, "observations": {}})
            continue
        observations: dict[str, dict] = {}
        verdict = "pass"
        for name, threshold in thresholds.items():
            if name == "peak_memory_mb_max":
                observed = _observed(result, name)
            else:
                observed = _language_observation(entry, name)
            observations[name] = {**observed, "threshold": threshold, "direction": THRESHOLDS[name]}
            value = observed.get("value")
            n = observed.get("n") or 0
            if value is None:
                verdict = DECISION_NOT_DECIDABLE
                language_reasons.append(
                    f"{name}: not measured ({observed.get('not_measured') or 'no value'})")
                continue
            if n < minimum:
                verdict = DECISION_NOT_DECIDABLE
                language_reasons.append(
                    f"{name}: {n} samples is below the criteria minimum of {minimum}")
                continue
            if not _passes(name, float(value), float(threshold)):
                verdict = DECISION_REJECTED
                language_reasons.append(
                    f"{name}: observed {value} against a bar of {threshold}")
        blocks.append({"language": code, "verdict": verdict,
                       "reasons": language_reasons or ["every bar was cleared"],
                       "observations": observations})

    verdicts = {block["verdict"] for block in blocks}
    if DECISION_REJECTED in verdicts:
        reasons.extend(f"{block['language']}: {reason}"
                       for block in blocks if block["verdict"] == DECISION_REJECTED
                       for reason in block["reasons"])
        return finish(DECISION_REJECTED)
    if DECISION_NOT_DECIDABLE in verdicts:
        reasons.extend(f"{block['language']}: {reason}"
                       for block in blocks if block["verdict"] == DECISION_NOT_DECIDABLE
                       for reason in block["reasons"])
        return finish(DECISION_NOT_DECIDABLE)
    if reasons:
        return finish(DECISION_NOT_DECIDABLE)
    reasons.append("every bar in the criteria file was cleared on a dedicated host, with no "
                   "failures and no network attempt; this run may now be reviewed by the owner")
    return finish(DECISION_ELIGIBLE)


def compare(candidates: list[dict]) -> dict:
    """Refuse to rank candidates. Present so the refusal is explicit and testable."""
    return {
        "comparison": "not_performed",
        "reason": "ranking requires the same criteria applied to every candidate by a human; this "
                  "tool reports measurements and never orders products",
        "candidates": [{"run": item.get("run", {}).get("id"),
                        "decision": item.get("decision", {}).get("decision")}
                       for item in candidates],
        "model_selection": "not_performed",
    }


__all__ = [
    "CRITERIA_SCHEMA",
    "DECISIONS",
    "DECISION_ELIGIBLE",
    "DECISION_NOT_DECIDABLE",
    "DECISION_NOT_EVALUATED",
    "DECISION_REJECTED",
    "CriteriaError",
    "OUTSTANDING_INPUTS",
    "THRESHOLDS",
    "compare",
    "evaluate",
    "load_criteria",
    "validate_criteria",
]
