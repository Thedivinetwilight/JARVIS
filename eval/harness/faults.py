"""Evaluator-chosen restore interruptions.

The fixture does not say when a restore attempt stops. It declares a *set* of
candidate stop points, how many stops happen and how many retries follow; this
module picks the point each stop lands on from a digest of the task id, the
fixture's salt and the attempt number. The choice is:

* deterministic -- the same task always stops at the same point, so a run is
  reproducible and comparable to its baseline;
* hidden from the solver -- the harness removes ``restore_faults`` from the task
  a solver sees, and the chosen point travels to the executor, which aborts the
  attempt when the solver reaches it;
* independent of the gold state -- the fixture's expected outcome is the same for
  every candidate, so the fixture cannot encode the answer to the injection and
  the solver cannot look up where it will be stopped.

A solver discovers an interruption the way a real one would have to: the mediated
step fails, the attempt it was running is incomplete, and the retry must resume.

Two interruption kinds share that machinery:

``interrupt_after_records``
    the stop lands *between* two record writes, so every write the attempt
    started is finished and the retry simply has less left to do;
``interrupt_mid_write``
    the stop lands *inside* one record write, after some of its parts (the
    primary record, then one derived-surface entry per surface) are applied. The
    write is left ``partially_applied``, and the scenario's visible
    ``restore_policy`` says whether the engine under test resumes it or rolls it
    back before retrying. Both are legal engines; the scenario states which one
    it is testing, and the scorer checks the store, not the claim.

For mid-write interruptions the fixture's candidates are positions into
:func:`eval.harness.write_state.legal_points`, so a fixture bounds the set
without naming the record or the part. The enumeration is derived from the
backup, so it is the same for the loader, the executor and the scorer.
"""
from __future__ import annotations

import hashlib
from typing import Any

from . import write_state

#: A restore may not be retried forever: four attempts is the cap both the solver
#: and the loader use, so a fixture cannot ask for an unbounded retry chain.
RESTORE_ATTEMPT_LIMIT = 4


def choose_stops(task: dict[str, Any]) -> dict[int, int]:
    """Which candidate stop point each interrupted attempt lands on."""
    spec = task.get("restore_faults")
    if not spec:
        return {}
    salt = str(spec["salt"])
    candidates = list(spec["candidates"])
    chosen: dict[int, int] = {}
    for attempt in range(1, int(spec["stops"]) + 1):
        digest = hashlib.sha256(f"{task['id']}:{salt}:{attempt}".encode("utf-8")).hexdigest()
        chosen[attempt] = int(candidates[int(digest[:8], 16) % len(candidates)])
    return chosen


def write_points(task: dict[str, Any]) -> list[dict[str, Any]]:
    """Every legal mid-write interruption point for one task, in a fixed order.

    Only records the live store does not already hold are enumerated. A restore
    that rewrites a copy which is already there is idempotent: interrupting it
    leaves a store that looks identical, so there would be nothing to observe.
    A record the store lost before the restore is the case where a half-written
    write is visible as a record without its copies, and where a rollback either
    does or does not put the store back the way it found it.
    """
    backup = task.get("backup") or {}
    contents = backup.get("contents") or {}
    lost = set(task.get("pre_restore_missing") or [])
    held = [rid for rid in sorted(set(contents.get("records") or []) - set(task.get("delete") or []))
            if rid in lost]
    return write_state.legal_points(held, contents.get("surfaces") or {})


def recovery_policy(task: dict[str, Any]) -> str:
    """How the engine under test handles a partially applied record."""
    return str((task.get("restore_policy") or {}).get("recovery", "resume"))


def injection(task: dict[str, Any] | None) -> dict[str, Any] | None:
    """The evaluator's fault schedule for one task, or None when it is not injected."""
    if not task:
        return None
    spec = task.get("restore_faults")
    if not spec:
        return None
    kind = spec.get("kind", "interrupt_after_records")
    stops = choose_stops(task)
    if kind == "interrupt_mid_write":
        points = write_points(task)
        stops = {attempt: dict(points[index - 1], candidate=index)
                 for attempt, index in stops.items()}
    retries = int(spec.get("retries", 1))
    return {
        "kind": kind,
        "recovery": recovery_policy(task),
        "salt": str(spec["salt"]),
        "candidates": [int(value) for value in spec["candidates"]],
        "stops": dict(sorted(stops.items())),
        "retries": retries,
        "expected_attempts": len(stops) + retries,
        "expected_complete": [False] * len(stops) + [True] * retries,
        "attempt_limit": RESTORE_ATTEMPT_LIMIT,
    }


def summarise(injection_value: dict[str, Any] | None) -> dict[str, Any]:
    """The part of an injection a ledger may record after the run (never before it)."""
    if not injection_value:
        return {}
    return {"kind": injection_value.get("kind", "interrupt_after_records"),
            "recovery": injection_value.get("recovery"),
            "salt": injection_value["salt"],
            "candidates": list(injection_value["candidates"]),
            "stops": dict(injection_value["stops"]),
            "retries": injection_value["retries"],
            "expected_attempts": injection_value["expected_attempts"]}
