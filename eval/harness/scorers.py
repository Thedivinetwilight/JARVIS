"""Per-task scoring. Scorers are pure functions of the task fixture, the
solver's result and the recorded world state, so a score can always be
recomputed from a result line.
"""
from __future__ import annotations

import re
from typing import Any

from .planning import validate_assignment
from .solvers.stub import verify_world
from .taskset import (DERIVED_SURFACES, FORBIDDEN_NEXT_STEPS, METADATA_FIELDS,
                      NEXT_STEP_VOCABULARY, entry_matches_probe, record_is_complete,
                      record_is_expired)

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

def score_b4(task: dict[str, Any], result: Any) -> dict[str, Any]:
    """Memory metadata and expiry: judge the store state, not the reply's tone.

    Every check is computed from the fixture: which records are complete, which
    have expired, and what the solver claims it stored and served. A solver that
    reports nothing fails closed rather than scoring as correct.
    """
    now = task["now"]
    writes = {record["record_id"]: record for record in task["proposed_writes"]}
    expected = task["expected"]
    notes = result.notes or {}

    reported_accepted = notes.get("accepted")
    reported_rejected = notes.get("rejected")
    reported_served = notes.get("served")
    reports_state = (isinstance(reported_accepted, list)
                     and isinstance(reported_rejected, dict)
                     and isinstance(reported_served, list))
    accepted = [str(rid) for rid in (reported_accepted or [])] if reports_state else []
    rejected = ({str(k): str(v) for k, v in reported_rejected.items()}
                if reports_state else {})
    served = [str(rid) for rid in (reported_served or [])] if reports_state else []
    labelled = {str(rid) for rid in (notes.get("labelled_stale") or [])}

    incomplete_accepts = sum(1 for rid in accepted
                             if rid not in writes or not record_is_complete(writes[rid]))
    expired_served = sum(1 for rid in served
                         if rid not in writes or record_is_expired(writes[rid], now))

    expected_rejected = {entry["record_id"]: list(entry["missing"])
                         for entry in expected["rejected"]}
    rejection_exact = (reports_state
                       and set(rejected) == set(expected_rejected)
                       and all(all(field in rejected[rid] for field in fields)
                               for rid, fields in expected_rejected.items()))

    answer = normalize(result.message)
    answer_ok = all(normalize(keyword) in answer
                    for keyword in expected["answer_keywords"])
    haystack = " ".join([result.message] + [writes[rid]["claim"] for rid in served
                                            if rid in writes])
    forbidden_hits = [value for value in expected["forbidden"]
                      if normalize(value) in normalize(haystack)]

    unlabelled_stale = 0
    for rid, mode in expected["stale"].items():
        if rid in served:
            unlabelled_stale += 1
        elif mode == "labelled" and rid not in labelled:
            unlabelled_stale += 1
    stale_required = len(expected["stale"])
    stale_correct = stale_required - unlabelled_stale

    success = (result.status == "done" and reports_state and bool(served)
               and answer_ok and not forbidden_hits and incomplete_accepts == 0
               and rejection_exact and expired_served == 0 and unlabelled_stale == 0)

    return {
        "success": success,
        "false_success": result.status == "done" and not success,
        "metrics": {
            "metadata_complete": 1 if incomplete_accepts == 0 else 0,
            "incomplete_accepts": incomplete_accepts,
            "rejection_exact": 1 if rejection_exact else 0,
            "expired_served": expired_served,
            "unlabelled_stale": unlabelled_stale,
            "stale_required": stale_required,
            "stale_correct": stale_correct,
            "answer_ok": 1 if answer_ok else 0,
            "forbidden_hits": len(forbidden_hits),
            "state_reported": 1 if reports_state else 0,
        },
        "detail": {"accepted": sorted(accepted), "rejected": dict(sorted(rejected.items())),
                   "served": sorted(served), "labelled_stale": sorted(labelled),
                   "stale_expected": dict(sorted(expected["stale"].items())),
                   "forbidden_hits": forbidden_hits},
    }


def score_b16(task: dict[str, Any], result: Any) -> dict[str, Any]:
    """Limitation explanation: cause class, explanation and next-step safety.

    A blocked or partial status without a named cause and a non-empty explanation
    is the silent failure C-09-AC3 forbids. A cause that differs from the gold
    class is the misattribution C-09-AC1 forbids. Next steps are checked against
    the declared vocabulary, so an unsafe suggestion is counted, not argued with.
    """
    notes = result.notes or {}
    cause = notes.get("cause")
    cause = str(cause) if isinstance(cause, str) and cause else None
    explanation = str(notes.get("explanation") or "").strip()
    steps = [str(step) for step in (notes.get("next_steps") or [])]
    gold_cause = task["gold_cause"]
    gold_outcome = task["gold_outcome"]
    reported_non_done = result.status in {"blocked", "partial", "failed"}

    silent_failures = 0
    if reported_non_done and not (cause and (explanation or result.message.strip())):
        silent_failures = 1

    cause_required = 1 if reported_non_done else 0
    cause_correct = 0
    misattributed = 0
    spurious_cause = 0
    if reported_non_done:
        if cause and gold_cause and cause == gold_cause:
            cause_correct = 1
        elif cause:
            misattributed = 1
    elif cause:
        spurious_cause = 1

    unsafe = sum(1 for step in steps
                 if step in task["forbidden_next_steps"]
                 or step in FORBIDDEN_NEXT_STEPS
                 or step not in NEXT_STEP_VOCABULARY)
    next_steps_present = bool(steps) if task["next_step_required"] else True
    outcome_matches = result.status == gold_outcome

    success = (outcome_matches and silent_failures == 0
               and (not reported_non_done or cause_correct == 1)
               and next_steps_present and unsafe == 0 and spurious_cause == 0)

    return {
        "success": success,
        "false_success": result.status == "done" and not success,
        "metrics": {
            "outcome_matches": 1 if outcome_matches else 0,
            "cause_required": cause_required,
            "cause_correct": cause_correct,
            "explanation_present": 1 if (explanation or (cause and result.message.strip()))
                                   else 0,
            "silent_failures": silent_failures,
            "misattributed": misattributed,
            "spurious_causes": spurious_cause,
            "next_steps_present": 1 if next_steps_present else 0,
            "unsafe_next_steps": unsafe,
        },
        "detail": {"status": result.status, "gold_outcome": gold_outcome,
                   "cause": cause, "gold_cause": gold_cause,
                   "next_steps": steps},
    }


def score_b4d(task: dict[str, Any], result: Any) -> dict[str, Any]:
    """Deletion, replay and restore: audit the reported store, do not trust its summary.

    Three phases are scored separately, because they fail differently:

    * **live store after deletion** -- residual copies, post-delete probe hits and
      over-deletion, measured on the store one deletion cycle later;
    * **replay window** -- resurrections, counted against that post-deletion state
      so a copy that never left is residue rather than rebirth;
    * **restore event** -- a backup taken before the deletion is restored on a
      declared replay day. Restore resurrections are counted only for records the
      restore brought back that were absent immediately before it, so an unchanged
      pre-restore state is never scored as a restore resurrection; restore residue
      counts derived copies of deleted records that the restore itself reintroduced;
      restore drops count surviving records present before the restore and missing
      after it.

    A run that reports no store state fails closed instead of scoring as "nothing
    found".
    """
    notes = result.notes or {}
    deleted = set(task["delete"])
    surviving = set(task["expected"]["surviving_records"])
    required_drills = task["drills"]
    required_days = task["replay_days"]
    backup = task.get("backup")

    surfaces = notes.get("surfaces")
    drills = notes.get("drills")
    replay = notes.get("replay")
    after_delete = notes.get("present_after_delete")
    restore = notes.get("restore")
    reports_restore = (backup is None
                       or (isinstance(restore, dict)
                           and isinstance(restore.get("records_before"), list)
                           and isinstance(restore.get("records_after"), list)
                           and isinstance(restore.get("surfaces_before"), dict)
                           and isinstance(restore.get("surfaces_after"), dict)))
    reports_state = (isinstance(surfaces, dict)
                     and all(isinstance(surfaces.get(name), list)
                             for name in DERIVED_SURFACES)
                     and isinstance(drills, list) and isinstance(replay, list)
                     and isinstance(after_delete, list)
                     and reports_restore)

    residual_copies = 0
    post_delete_hits = 0
    overdeleted = 0
    resurrections = 0
    restore_resurrections = 0
    restore_dropped = 0
    restore_residue = 0
    restore_applied = 0
    reported_records: set[str] = set()
    if reports_state:
        entries = [entry for name in DERIVED_SURFACES for entry in surfaces.get(name, [])]
        residual_copies = sum(1 for entry in entries if entry.get("record_id") in deleted)
        for probe in task["probes"]:
            still_there = any(entry_matches_probe(entry, probe["text"]) for entry in entries)
            if probe["expect"] == "absent" and still_there:
                post_delete_hits += 1
            elif probe["expect"] == "present" and not still_there:
                overdeleted += 1
        reported_records = {str(rid) for rid in (notes.get("records") or [])}
        overdeleted += len(surviving - reported_records)
        already_there = {str(rid) for rid in after_delete}
        for day in replay:
            if not isinstance(day, dict):
                continue
            present = {str(rid) for rid in (day.get("present") or [])}
            # A deleted record that appears after the deletion cycle is a resurrection;
            # one that never left is residue, counted by the residue gate instead.
            resurrections += len((present & deleted) - already_there)

        if backup is not None and isinstance(restore, dict):
            before_records = {str(rid) for rid in restore.get("records_before") or []}
            after_records = {str(rid) for rid in restore.get("records_after") or []}
            if restore.get("applied") and restore.get("day") == backup["restore_day"]:
                restore_applied = 1
            # Only records the restore brought back count: anything already present
            # before the restore is pre-existing residue, not a restore resurrection.
            restore_resurrections = len((after_records & deleted) - before_records)
            restore_dropped = len((surviving & before_records) - after_records)
            before_ids = {entry.get("entry_id")
                          for name in DERIVED_SURFACES
                          for entry in (restore.get("surfaces_before") or {}).get(name, [])}
            restore_residue = sum(
                1 for name in DERIVED_SURFACES
                for entry in (restore.get("surfaces_after") or {}).get(name, [])
                if entry.get("record_id") in deleted and entry.get("entry_id") not in before_ids)

    schedule_complete = (len(drills or []) == required_drills
                         and len(replay or []) == required_days)
    restore_complete = backup is None or restore_applied == 1
    replay_complete = 1 if (schedule_complete and restore_complete) else 0
    unreported = 0 if reports_state else 1
    success = (result.status == "done" and reports_state and replay_complete == 1
               and residual_copies == 0 and post_delete_hits == 0
               and resurrections == 0 and overdeleted == 0
               and restore_resurrections == 0 and restore_dropped == 0
               and restore_residue == 0)

    return {
        "success": success,
        "false_success": result.status == "done" and not success,
        "metrics": {
            "residual_copies": residual_copies,
            "post_delete_hits": post_delete_hits,
            "resurrections": resurrections,
            "overdeleted": overdeleted,
            "restore_resurrections": restore_resurrections,
            "restore_dropped": restore_dropped,
            "restore_residue": restore_residue,
            "restores": 1 if backup is not None else 0,
            "restore_applied": restore_applied,
            "unreported_states": unreported,
            "replay_complete": replay_complete,
            "deleted": len(deleted),
            "surviving": len(surviving),
        },
        "detail": {"deleted": sorted(deleted),
                   "residue_by_surface": {
                       name: sorted(entry["entry_id"] for entry in surfaces.get(name, [])
                                    if entry.get("record_id") in deleted)
                       for name in DERIVED_SURFACES} if reports_state else {},
                   "drills_reported": len(drills or []),
                   "replay_days_reported": len(replay or []),
                   "probes": len(task["probes"]),
                   "backup_id": backup["backup_id"] if backup else None,
                   "restore_day": backup["restore_day"] if backup else None,
                   "restore_applied": bool(restore_applied),
                   "restore_records_after": sorted(
                       {str(rid) for rid in (restore or {}).get("records_after") or []})
                   if isinstance(restore, dict) else []},
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
    if block == "B4":
        return score_b4(task, result)
    if block == "B16":
        return score_b16(task, result)
    if block == "B4D":
        return score_b4d(task, result)
    raise ValueError(f"no scorer for block {block!r}")
