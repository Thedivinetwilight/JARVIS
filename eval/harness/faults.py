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
"""
from __future__ import annotations

import hashlib
from typing import Any

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


def injection(task: dict[str, Any] | None) -> dict[str, Any] | None:
    """The evaluator's fault schedule for one task, or None when it is not injected."""
    if not task:
        return None
    spec = task.get("restore_faults")
    if not spec:
        return None
    stops = choose_stops(task)
    retries = int(spec.get("retries", 1))
    return {
        "kind": spec.get("kind", "interrupt_after_records"),
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
    return {"salt": injection_value["salt"],
            "candidates": list(injection_value["candidates"]),
            "stops": dict(injection_value["stops"]),
            "retries": injection_value["retries"],
            "expected_attempts": injection_value["expected_attempts"]}
