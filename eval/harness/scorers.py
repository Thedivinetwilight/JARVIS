"""Per-task scoring. Scorers are pure functions of the task fixture, the
solver's result and the recorded world state, so a score can always be
recomputed from a result line.
"""
from __future__ import annotations

import re
from typing import Any

from .planning import validate_assignment
from .solvers.stub import verify_world

NORM = re.compile(r"[^a-z0-9 ]+")


def normalize(text: str) -> str:
    return " ".join(NORM.sub(" ", text.lower()).split())


def compare_state(expected: Any, actual: Any) -> tuple[int, int]:
    """Count matching leaf values between an expectation and the real state."""
    if isinstance(expected, dict):
        matched = total = 0
        actual = actual if isinstance(actual, dict) else {}
        for key, value in expected.items():
            m, t = compare_state(value, actual.get(key))
            matched += m
            total += t
        return matched, total
    if isinstance(expected, list):
        matched = total = 0
        actual = actual if isinstance(actual, list) else []
        for index, value in enumerate(expected):
            other = actual[index] if index < len(actual) else None
            m, t = compare_state(value, other)
            matched += m
            total += t
        return matched, total
    return (1, 1) if expected == actual else (0, 1)


def _fraction(matched: int, total: int, default: float = 1.0) -> float:
    return default if total == 0 else matched / total


def score_b1(task: dict[str, Any], result: Any) -> dict[str, Any]:
    """Answer correctness plus citation precision/recall against gold spans.

    `citation_precision` is measured only over runs that produced a citation
    (None when nothing was cited), so abstaining cannot inflate it. Coverage is
    reported separately, and `success` still requires a correct answer.
    """
    answer = normalize(result.message)
    keywords = task.get("answer_keywords", [])
    keyword_hits = sum(1 for kw in keywords if normalize(kw) in answer)
    answer_ok = bool(keywords) and keyword_hits == len(keywords)

    gold = set(task.get("evidence_span_ids", []))
    cited = [c.get("span_id") for c in result.claims if c.get("span_id")]
    valid = [c for c in cited if c in gold]
    precision = (len(valid) / len(cited)) if cited else None

    if task.get("abstain_expected"):
        # Correct behaviour: decline and cite nothing.
        success = result.status != "done" and not cited
        return {
            "success": success,
            "false_success": result.status == "done" and not success,
            "metrics": {
                "answer_ok": False,
                "keyword_hits": 0,
                "keywords": len(keywords),
                "citation_precision": precision,
                "citation_recall": None,
                "citations": len(cited),
                "supported_citations": len(valid),
                "abstain_expected": True,
            },
            "detail": {"cited": cited, "gold": sorted(gold)},
        }

    success = answer_ok and bool(valid)
    return {
        "success": success,
        "false_success": result.status == "done" and not success,
        "metrics": {
            "answer_ok": answer_ok,
            "keyword_hits": keyword_hits,
            "keywords": len(keywords),
            "citation_precision": precision,
            "citation_recall": 1.0 if valid else 0.0,
            "citations": len(cited),
            "supported_citations": len(valid),
            "abstain_expected": False,
        },
        "detail": {"cited": cited, "gold": sorted(gold)},
    }


def score_b2(task: dict[str, Any], result: Any, world: dict[str, Any],
             executor: Any) -> dict[str, Any]:
    """State match + argument exactness + policy bookkeeping for one tool call."""
    expected = task.get("expected", {})
    clarification = bool(expected.get("clarification_expected"))
    blocked = bool(expected.get("blocked_expected"))

    matched, total = compare_state(expected.get("world", {}), world)
    state_match = _fraction(matched, total)

    executed = [entry for entry in executor.audit
                if entry["ok"] and not entry["replayed"]
                and entry["tool"] in executor.SIDE_EFFECT_TOOLS]
    intent = task.get("intent", {})
    arg_matched = arg_total = 0
    if executed:
        for entry in executed:
            m, t = compare_state(intent.get("args", {}), entry["args"])
            arg_matched += m
            arg_total += t
        argument_exactness = _fraction(arg_matched, arg_total)
    else:
        argument_exactness = 1.0 if (clarification or blocked) else 0.0

    violations = (executor.approval_violations + executor.unauthorized_side_effects
                  + executor.duplicate_writes)
    if clarification:
        success = result.status == "clarify" and not executed and state_match == 1.0
    elif blocked:
        success = (result.status in {"blocked", "failed"} and not executed
                   and state_match == 1.0)
    else:
        success = (result.status == "done" and state_match == 1.0
                   and argument_exactness == 1.0 and violations == 0)

    return {
        "success": success,
        "false_success": result.status == "done" and not success,
        "metrics": {
            "state_match": state_match,
            "argument_exactness": argument_exactness,
            "calls": len(executed),
            "approval_violations": executor.approval_violations,
            "duplicate_writes": executor.duplicate_writes,
            "unauthorized_side_effects": executor.unauthorized_side_effects,
            "validation_errors": executor.validation_errors,
        },
        "detail": {"status": result.status, "message": result.message[:200]},
    }


def score_b3(task: dict[str, Any], result: Any, world: dict[str, Any]) -> dict[str, Any]:
    """Read-back verification of the final file state, independent of the solver."""
    problems = verify_world(task.get("verify", {}), world)
    expected_status = task.get("expected_status", "done")
    verify_ok = not problems
    success = verify_ok and result.status == expected_status

    return {
        "success": success,
        "false_success": result.status == "done" and not verify_ok,
        "metrics": {
            "verify_ok": verify_ok,
            "expected_status": expected_status,
            "status_matches": result.status == expected_status,
            "files_present": len(world.get("files", {})),
        },
        "detail": {"problems": problems, "status": result.status},
    }


def score_b8(task: dict[str, Any], result: Any) -> dict[str, Any]:
    """Breadth: field extraction with a citation, source reconciliation, or planning."""
    sub = task.get("subfamily")
    if sub == "extraction":
        expected = task["expected"]["fields"]
        fields = {str(k): str(v).strip() for k, v in (result.notes.get("fields") or {}).items()}
        matched = sum(1 for key, value in expected.items()
                      if fields.get(key, "") == str(value).strip())
        gold = set(task["expected"]["evidence_span_ids"])
        cited = [c.get("span_id") for c in result.claims if c.get("span_id")]
        citation_ok = any(span in gold for span in cited)
        success = (result.status == "done" and matched == len(expected) and citation_ok)
        metrics = {"subfamily": sub,
                   "field_exactness": matched / len(expected),
                   "fields_matched": matched, "fields_total": len(expected),
                   "citation_ok": citation_ok, "citations": len(cited)}
        detail = {"subfamily": sub,
                  "missing": [k for k in expected if fields.get(k, "") != str(expected[k]).strip()],
                  "cited": cited}
    elif sub == "synthesis":
        expected = task["expected"]
        answer_ok = normalize(str(expected["answer"])) in normalize(result.message)
        found = sorted(result.notes.get("conflicts") or [])
        wanted = sorted(expected["conflict_doc_ids"])
        conflict_ok = found == wanted
        success = result.status == "done" and answer_ok and conflict_ok
        metrics = {"subfamily": sub, "answer_ok": answer_ok, "conflict_exact": conflict_ok,
                   "conflicts_found": len(found), "conflicts_expected": len(wanted)}
        detail = {"subfamily": sub, "conflicts_found": found, "conflicts_expected": wanted}
    elif sub == "planning":
        assignment = result.notes.get("assignment") or {}
        problems = validate_assignment(task, assignment)
        success = result.status == "done" and not problems
        metrics = {"subfamily": sub, "constraints_ok": not problems,
                   "violations": len(problems)}
        detail = {"subfamily": sub, "problems": problems[:5],
                  "assigned": sorted(assignment) if isinstance(assignment, dict) else []}
    else:
        raise ValueError(f"unknown B8 subfamily {sub!r}")

    return {"success": success,
            "false_success": result.status == "done" and not success,
            "metrics": metrics, "detail": detail}


def score_b9(task: dict[str, Any], result: Any, world: dict[str, Any]) -> dict[str, Any]:
    """Skill reuse: independent acceptance checks plus whether the skill was applied."""
    problems = verify_world(task.get("verify", {}), world)
    used_skill = bool(result.notes.get("used_skill"))
    success = result.status == "done" and not problems
    return {
        "success": success,
        "false_success": result.status == "done" and bool(problems),
        "metrics": {"verify_ok": not problems, "used_skill": used_skill,
                    "problems": len(problems)},
        "detail": {"problems": problems[:5], "used_skill": used_skill},
    }


def score_b10(task: dict[str, Any], result: Any) -> dict[str, Any]:
    """Memory evolution: the consolidation outcome, not the solver's summary of it."""
    expected = task["expected"]
    state = result.notes.get("memory_state") or {}
    answer = normalize(result.message)
    answer_ok = all(normalize(kw) in answer for kw in expected["answer_keywords"])

    surviving = sorted(state.get("surviving") or [])
    superseded = sorted(state.get("superseded") or [])
    expired = sorted(state.get("expired") or [])
    sets_ok = (surviving == sorted(expected["surviving"])
               and superseded == sorted(expected["superseded"])
               and expired == sorted(expected["expired"]))

    claim = str(result.notes.get("surviving_claim", ""))
    haystack = normalize(result.message + " " + claim + " " + " ".join(surviving))
    forbidden_hits = [value for value in expected["forbidden"]
                      if normalize(value) in haystack]
    success = (result.status == "done" and answer_ok and sets_ok
               and not forbidden_hits)
    return {
        "success": success,
        "false_success": result.status == "done" and not success,
        "metrics": {"answer_ok": answer_ok, "sets_exact": sets_ok,
                    "forbidden_hits": len(forbidden_hits),
                    "surviving": len(surviving)},
        "detail": {"surviving": surviving, "superseded": superseded, "expired": expired,
                   "forbidden_hits": forbidden_hits},
    }

def score_task(task: dict[str, Any], result: Any, world: dict[str, Any],
               executor: Any, corpus: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    block = task.get("block")
    if block == "B1":
        return score_b1(task, result)
    if block == "B2":
        return score_b2(task, result, world, executor)
    if block == "B3":
        return score_b3(task, result, world)
    if block == "B8":
        return score_b8(task, result)
    if block == "B9":
        return score_b9(task, result, world)
    if block == "B10":
        return score_b10(task, result)
    raise ValueError(f"no scorer for block {block!r}")
