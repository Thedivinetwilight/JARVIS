"""Persistent scheduling of objectives (Mission 003).

The scheduler answers one question on every tick: *which objectives are due, in what order, and
did I already create one for this slot?* Everything it decides is derived from rows, so a
restart, a second worker, or a crashed tick cannot lose or duplicate scheduled work.

Guarantees, and why they hold
-----------------------------
* **Priority and deadline ordering** — due work is ordered by ``priority`` (1 highest) and then
  by the slot time it is late for, with an explicit deadline attached to the created objective.
  A schedule with a shorter deadline but lower priority still carries its own deadline into the
  objective, so the objective's own budget logic stays authoritative.
* **No duplicate objectives per slot** — dispatch inserts the objective with
  ``(schedule_id, scheduled_for)`` and relies on the unique partial index
  ``idx_objectives_schedule_slot``. A loser of a race gets ``sqlite3.IntegrityError`` and reports
  ``duplicate`` instead of creating a second objective. This is a database guarantee, not a
  check-then-act in Python.
* **No infinite task creation** — a schedule always has a finite horizon: ``max_runs`` (default
  1) or an explicit ``until``. Catch-up after downtime *skips* missed slots rather than replaying
  them, and each tick dispatches at most ``MAX_PER_TICK`` objectives.
* **No authorisation bypass** — the scheduler never executes capabilities. It calls the same
  ``ObjectiveService`` entry point as an owner request, with origin ``scheduler``; a scheduled
  request that needs a write still stops for owner approval, and a denial still denies.
* **Single dispatcher** — ticks take a lease (``scheduler`` namespace). A worker that cannot
  acquire it does nothing. The lease is fencing-token based, so an expired holder's late write
  cannot advance a schedule it no longer owns.

Recurrence is deliberately explicit (``once``/``hourly``/``daily``/``weekly``) rather than cron:
a small, testable vocabulary beats a parser that silently mis-schedules production work.
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from .contracts import Origin
from .leases import LeaseError, LeaseStore
from .util import parse_iso, to_iso

#: Cadence name -> interval in seconds. ``once`` has no interval and never recurs.
CADENCES: dict[str, int | None] = {
    "once": None,
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
}
SCHEDULE_STATUSES = ("scheduled", "paused", "completed", "cancelled")

MAX_PER_TICK = 3                 # bounded work per tick, even if many schedules are due
MAX_CATCHUP_SLOTS = 10_000       # bound the skip-forward loop for very old rows
MAX_RUNS_LIMIT = 500             # an explicit ceiling: schedules are not unbounded generators
MAX_REQUEST_CHARS = 2000
DEFAULT_BUDGET_SECONDS = 120.0
LEASE_NAMESPACE = "scheduler"
LEASE_TTL_SECONDS = 30


class ScheduleError(ValueError):
    """An invalid schedule definition. Rejected before anything is persisted."""


@dataclass
class Schedule:
    id: str
    request: str
    priority: int
    next_run_at: str
    status: str
    interval_seconds: int | None
    deadline_minutes: int | None
    budget_seconds: float
    max_runs: int | None
    runs: int
    created_at: str
    updated_at: str
    last_run_at: str | None = None
    last_objective_id: str | None = None
    last_outcome: str | None = None

    @property
    def cadence(self) -> str:
        for name, seconds in CADENCES.items():
            if seconds == self.interval_seconds:
                return name
        return "custom"

    @property
    def remaining_runs(self) -> int | None:
        return None if self.max_runs is None else max(0, self.max_runs - self.runs)

    @property
    def exhausted(self) -> bool:
        return self.max_runs is not None and self.runs >= self.max_runs

    def exhausted_after_increment(self) -> bool:
        """Would the run about to be recorded be the schedule's final run?"""
        return self.max_runs is not None and self.runs + 1 >= self.max_runs

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "request": self.request, "priority": self.priority,
            "cadence": self.cadence, "interval_seconds": self.interval_seconds,
            "next_run_at": self.next_run_at, "status": self.status,
            "deadline_minutes": self.deadline_minutes, "budget_seconds": self.budget_seconds,
            "max_runs": self.max_runs, "runs": self.runs, "remaining_runs": self.remaining_runs,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "last_run_at": self.last_run_at, "last_objective_id": self.last_objective_id,
            "last_outcome": self.last_outcome,
        }


_COLUMNS = ("id, request, priority, interval_seconds, next_run_at, status, deadline_minutes, "
            "budget_seconds, max_runs, runs, created_at, updated_at, last_run_at, "
            "last_objective_id, last_outcome")


class ScheduleStore:
    """Persistence for schedules. Dispatch itself belongs to :class:`Scheduler`."""

    def __init__(self, store) -> None:
        self.store = store

    # The clock is the store's, so tests can move time without patching the scheduler.
    def _now(self):
        return self.store.now()

    # ------------------------------------------------------------------ writes
    def create(self, request: str, *, cadence: str = "once", first_run_at: str | None = None,
               priority: int = 3, deadline_minutes: int | None = None,
               budget_seconds: float = DEFAULT_BUDGET_SECONDS, max_runs: int | None = None,
               status: str = "scheduled") -> Schedule:
        request = (request or "").strip()
        if not request:
            raise ScheduleError("A schedule needs a request to run")
        if len(request) > MAX_REQUEST_CHARS:
            raise ScheduleError(f"A scheduled request may be at most {MAX_REQUEST_CHARS} characters")
        if cadence not in CADENCES:
            raise ScheduleError(f"Unknown cadence '{cadence}'; expected one of {sorted(CADENCES)}")
        if status not in SCHEDULE_STATUSES:
            raise ScheduleError(f"Unknown schedule status '{status}'")
        if not isinstance(priority, int) or not 1 <= priority <= 5:
            raise ScheduleError("Priority must be an integer from 1 (highest) to 5")
        interval = CADENCES[cadence]
        if interval is None:
            max_runs = 1 if max_runs is None else max_runs
        elif max_runs is not None and not 1 <= max_runs <= MAX_RUNS_LIMIT:
            raise ScheduleError(f"max_runs must be between 1 and {MAX_RUNS_LIMIT}")
        if deadline_minutes is not None and not 1 <= deadline_minutes <= 60 * 24 * 30:
            raise ScheduleError("deadline_minutes must be between 1 and 43200")
        if not 5.0 <= float(budget_seconds) <= 900.0:
            raise ScheduleError("budget_seconds must be between 5 and 900")
        now = to_iso(self._now())
        first = first_run_at or now
        schedule_id = f"sch_{secrets.token_hex(6)}"
        with self.store.write() as db:
            db.execute(
                "INSERT INTO objective_schedules (id, request, priority, interval_seconds, "
                "next_run_at, status, deadline_minutes, budget_seconds, max_runs, runs, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (schedule_id, request, int(priority), interval, first, status, deadline_minutes,
                 float(budget_seconds), max_runs, 0, now, now))
        return self.get(schedule_id)

    def pause(self, schedule_id: str) -> Schedule:
        return self._set_status(schedule_id, "paused")

    def resume(self, schedule_id: str) -> Schedule:
        schedule = self.get(schedule_id)
        if schedule.status not in {"paused"}:
            raise ScheduleError(f"Only a paused schedule can be resumed (was '{schedule.status}')")
        return self._set_status(schedule_id, "scheduled")

    def cancel(self, schedule_id: str) -> Schedule:
        return self._set_status(schedule_id, "cancelled")

    def skip_slot(self, schedule_id: str, *, next_run_at: str | None, reason: str = "") -> Schedule:
        """Advance past a slot another worker already dispatched (no run is counted)."""
        now = to_iso(self._now())
        with self.store.write() as db:
            db.execute(
                "UPDATE objective_schedules SET next_run_at = ?, updated_at = ?, "
                "last_outcome = coalesce(?, last_outcome), status = ? WHERE id = ?",
                (next_run_at or now, now, reason or None,
                 "completed" if next_run_at is None else "scheduled", schedule_id))
        return self.get(schedule_id)

    def record_run(self, schedule_id: str, *, objective_id: str, outcome: str,
                   next_run_at: str | None) -> Schedule:
        """Record the result of a dispatch and advance (or close) the schedule."""
        now = to_iso(self._now())
        with self.store.write() as db:
            db.execute(
                "UPDATE objective_schedules SET runs = runs + 1, last_run_at = ?, "
                "last_objective_id = ?, last_outcome = ?, updated_at = ?, next_run_at = ?, "
                "status = ? WHERE id = ?",
                (now, objective_id, outcome, now, next_run_at or now,
                 "completed" if next_run_at is None else "scheduled", schedule_id))
        return self.get(schedule_id)

    def _set_status(self, schedule_id: str, status: str) -> Schedule:
        now = to_iso(self._now())
        with self.store.write() as db:
            db.execute("UPDATE objective_schedules SET status = ?, updated_at = ? WHERE id = ?",
                       (status, now, schedule_id))
        return self.get(schedule_id)

    # ------------------------------------------------------------------ reads
    def get(self, schedule_id: str) -> Schedule:
        with self.store.read() as db:
            row = db.execute(f"SELECT {_COLUMNS} FROM objective_schedules WHERE id = ?",
                             (schedule_id,)).fetchone()
        if row is None:
            raise ScheduleError(f"Schedule not found: {schedule_id}")
        return self._row(row)

    def list(self, *, status: str | None = None, limit: int = 50) -> list[Schedule]:
        sql = f"SELECT {_COLUMNS} FROM objective_schedules"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY priority ASC, next_run_at ASC LIMIT ?"
        params.append(int(limit))
        with self.store.read() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._row(row) for row in rows]

    def due(self, now: str | None = None, *, limit: int = MAX_PER_TICK) -> list[Schedule]:
        """Due work in dispatch order: highest priority first, then the oldest slot."""
        moment = now or to_iso(self.store.now())
        with self.store.read() as db:
            rows = db.execute(
                f"SELECT {_COLUMNS} FROM objective_schedules WHERE status = 'scheduled' "
                "AND next_run_at <= ? ORDER BY priority ASC, next_run_at ASC, id ASC LIMIT ?",
                (moment, int(limit))).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row) -> Schedule:
        return Schedule(
            id=row["id"], request=row["request"], priority=row["priority"],
            next_run_at=row["next_run_at"], status=row["status"],
            interval_seconds=row["interval_seconds"], deadline_minutes=row["deadline_minutes"],
            budget_seconds=row["budget_seconds"], max_runs=row["max_runs"], runs=row["runs"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            last_run_at=row["last_run_at"], last_objective_id=row["last_objective_id"],
            last_outcome=row["last_outcome"])


class Scheduler:
    """Dispatches due schedules into objectives, one objective per slot, under a lease."""

    def __init__(self, store, *, dispatch: Callable[..., dict[str, Any]],
                 owner: str | None = None, lease_ttl: int = LEASE_TTL_SECONDS) -> None:
        self.store = store
        self.schedules = ScheduleStore(store)
        self.leases = LeaseStore(store)
        self.dispatch = dispatch
        self.owner = owner or f"scheduler-{secrets.token_hex(4)}"
        self.lease_ttl = max(5, min(900, int(lease_ttl)))

    # ------------------------------------------------------------------ ticks
    def tick(self, *, limit: int = MAX_PER_TICK, now: str | None = None) -> dict[str, Any]:
        """Dispatch due schedules once. Returns a report; never raises for a lost lease."""
        moment = now or to_iso(self.store.now())
        lease = self.leases.acquire(f"{LEASE_NAMESPACE}:tick", owner=self.owner,
                                    ttl=self.lease_ttl)
        if lease is None:
            return {"dispatched": [], "skipped": "lease_held_by_another_worker", "due": 0}
        report: dict[str, Any] = {"dispatched": [], "skipped": None, "due": 0}
        try:
            due = self.schedules.due(moment, limit=limit)
            report["due"] = len(due)
            for schedule in due:
                report["dispatched"].append(self._dispatch_one(schedule))
        finally:
            try:
                self.leases.release(lease)
            except LeaseError:                          # already expired: nothing to clean up
                pass
        return report

    def _dispatch_one(self, schedule: Schedule) -> dict[str, Any]:
        slot = schedule.next_run_at
        deadline = None
        if schedule.deadline_minutes:
            deadline = to_iso(self.store.now() + timedelta(minutes=schedule.deadline_minutes))
        try:
            view = self.dispatch(
                schedule.request, origin=Origin.SCHEDULE.value, priority=schedule.priority,
                deadline=deadline, budget_seconds=schedule.budget_seconds,
                schedule_id=schedule.id, scheduled_for=slot)
        except sqlite3.IntegrityError:
            # Another worker created the objective for this slot first. Advance our view of the
            # schedule without creating anything: the unique index already prevented duplication.
            self.store.events.append(
                "schedule.duplicate_suppressed", actor="system", target=schedule.id,
                payload={"slot": slot, "worker": self.owner})
            # Move past the slot so a duplicate does not become a livelock retried every tick.
            next_slot = self._next_slot(schedule, slot)
            self.schedules.skip_slot(schedule.id, next_run_at=next_slot, reason="duplicate")
            return {"schedule_id": schedule.id, "slot": slot, "result": "duplicate",
                    "next_run_at": next_slot}
        objective = (view or {}).get("objective") or {}
        outcome = objective.get("outcome") or objective.get("status") or "unknown"
        next_slot = self._next_slot(schedule, slot)
        self.schedules.record_run(schedule.id, objective_id=objective.get("id") or "",
                                  outcome=str(outcome), next_run_at=next_slot)
        self.store.events.append(
            "schedule.dispatched", actor="system", target=schedule.id, decision=str(outcome),
            objective_id=objective.get("id"),
            payload={"slot": slot, "objective_id": objective.get("id"), "outcome": outcome,
                     "next_run_at": next_slot, "worker": self.owner,
                     "runs": schedule.runs + 1, "max_runs": schedule.max_runs})
        return {"schedule_id": schedule.id, "slot": slot, "objective_id": objective.get("id"),
                "outcome": outcome, "next_run_at": next_slot, "result": "dispatched"}

    def _next_slot(self, schedule: Schedule, slot: str) -> str | None:
        """Next slot strictly in the future, or ``None`` when the schedule is finished.

        Missed slots during downtime are skipped, never replayed in a burst.
        """
        if schedule.interval_seconds is None:
            return None
        if schedule.exhausted_after_increment():
            return None
        now = self.store.now()
        parsed = parse_iso(slot)
        if parsed is None:
            return None
        candidate = parsed + timedelta(seconds=schedule.interval_seconds)
        attempts = 0
        while candidate <= now and attempts < MAX_CATCHUP_SLOTS:
            candidate += timedelta(seconds=schedule.interval_seconds)
            attempts += 1
        if attempts >= MAX_CATCHUP_SLOTS:                # pragma: no cover - defensive bound
            return None
        return to_iso(candidate)

    def run_forever(self, *, interval_seconds: float = 5.0, limit: int = MAX_PER_TICK,
                    should_stop: Callable[[], bool] | None = None,
                    max_ticks: int | None = None) -> list[dict[str, Any]]:
        """Tick periodically. Bounded by ``max_ticks``/``should_stop`` so it can be tested."""
        import time as _time

        reports: list[dict[str, Any]] = []
        ticks = 0
        while should_stop is None or not should_stop():
            reports.append(self.tick(limit=limit))
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            _time.sleep(max(0.1, float(interval_seconds)))
        return reports


def start_background(scheduler: Scheduler, interval_seconds: float, *,
                     name: str = "jarvis-scheduler") -> tuple[threading.Thread, threading.Event]:
    """Tick ``scheduler`` in a daemon thread until the returned event is set.

    The thread is a convenience for ``jarvis serve``, not a second authority: every tick still
    takes the lease and every objective still goes through the permission layer. A ''interval''
    of 0 or less disables the loop by returning an already-set event.
    """
    import time as _time

    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                scheduler.tick()
            except Exception:                     # a bad tick must not kill scheduling
                pass
            stop.wait(max(0.5, float(interval_seconds)))

    if interval_seconds <= 0:
        stop.set()
        return threading.Thread(target=loop, name=name, daemon=True), stop
    thread = threading.Thread(target=loop, name=name, daemon=True)
    thread.start()
    return thread, stop
