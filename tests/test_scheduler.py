"""Persistent scheduling (Mission 003).

Real SQLite, real objectives, real threads. The scheduler is judged on the properties the
owner asked for: priority/deadline ordering, recurrence, no duplicate objective per slot, no
unbounded creation, and no authorisation bypass.
"""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from jarvis.contracts import ObjectiveOutcome, Origin
from jarvis.scheduler import (CADENCES, MAX_PER_TICK, MAX_RUNS_LIMIT, ScheduleError, Scheduler,
                              ScheduleStore, start_background)
from jarvis.util import to_iso


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def schedules(app):
    return app.scheduler.schedules


# ---------------------------------------------------------------------------------------
# definition rules
# ---------------------------------------------------------------------------------------
def test_a_schedule_needs_a_request(schedules):
    with pytest.raises(ScheduleError):
        schedules.create("   ")


def test_unknown_cadence_is_rejected(schedules):
    with pytest.raises(ScheduleError):
        schedules.create("what time is it", cadence="every-fortnight-at-noon")


@pytest.mark.parametrize("priority", [0, 6, -1])
def test_priority_is_bounded(schedules, priority):
    with pytest.raises(ScheduleError):
        schedules.create("what time is it", priority=priority)


@pytest.mark.parametrize("max_runs", [0, -2, MAX_RUNS_LIMIT + 1])
def test_max_runs_is_bounded(schedules, max_runs):
    with pytest.raises(ScheduleError):
        schedules.create("what time is it", cadence="daily", max_runs=max_runs)


def test_a_once_schedule_runs_exactly_once_by_default(schedules):
    schedule = schedules.create("what time is it")
    assert schedule.max_runs == 1 and schedule.cadence == "once"
    assert schedule.interval_seconds is None


def test_recurring_schedules_have_an_interval(schedules):
    daily = schedules.create("what time is it", cadence="daily")
    assert daily.interval_seconds == CADENCES["daily"] and daily.cadence == "daily"


def test_budget_bounds_are_enforced(schedules):
    with pytest.raises(ScheduleError):
        schedules.create("what time is it", budget_seconds=1)


def test_empty_request_after_strip_is_refused(schedules):
    with pytest.raises(ScheduleError):
        schedules.create("\n\t ")


def test_unknown_schedule_id_raises(schedules):
    with pytest.raises(ScheduleError):
        schedules.get("sch_doesnotexist")


# ---------------------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------------------
def test_a_due_schedule_creates_one_objective_with_scheduled_origin(app):
    schedule = app.scheduler.schedules.create("what time is it", cadence="daily", max_runs=2)
    report = app.scheduler.tick()
    assert report["due"] == 1 and report["skipped"] is None
    entry = report["dispatched"][0]
    assert entry["result"] == "dispatched"
    objective = app.objectives.objectives.get(entry["objective_id"])
    assert objective.origin == Origin.SCHEDULE.value
    assert objective.schedule_id == schedule.id
    assert objective.scheduled_for == schedule.next_run_at
    assert objective.outcome == ObjectiveOutcome.COMPLETED.value
    stored = app.scheduler.schedules.get(schedule.id)
    assert stored.runs == 1 and stored.last_objective_id == objective.id


def test_a_due_schedule_is_not_dispatched_twice(app):
    app.scheduler.schedules.create("what time is it", cadence="daily")
    assert app.scheduler.tick()["due"] == 1
    second = app.scheduler.tick()
    assert second["due"] == 0 and second["dispatched"] == []


def test_priority_orders_due_work_and_deadlines_are_attached(app):
    low = app.scheduler.schedules.create("what time is it", priority=5, deadline_minutes=30)
    high = app.scheduler.schedules.create("what time is it", priority=1)
    report = app.scheduler.tick()
    order = [entry["schedule_id"] for entry in report["dispatched"]]
    assert order == [high.id, low.id]
    for entry in report["dispatched"]:
        objective = app.objectives.objectives.get(entry["objective_id"])
        if entry["schedule_id"] == low.id:
            assert objective.priority == 5 and objective.deadline is not None
        else:
            assert objective.priority == 1 and objective.deadline is None


def test_only_due_work_runs_and_future_work_waits(app):
    later = to_iso(app.store.now() + timedelta(hours=2))
    app.scheduler.schedules.create("what time is it", first_run_at=later)
    assert app.scheduler.tick()["due"] == 0


def test_a_once_schedule_completes_after_one_run(app):
    schedule = app.scheduler.schedules.create("what time is it")
    app.scheduler.tick()
    stored = app.scheduler.schedules.get(schedule.id)
    assert stored.status == "completed" and stored.runs == 1


def test_a_recurring_schedule_advances_into_the_future(app):
    schedule = app.scheduler.schedules.create("what time is it", cadence="hourly", max_runs=3)
    app.scheduler.tick()
    stored = app.scheduler.schedules.get(schedule.id)
    assert stored.status == "scheduled" and stored.runs == 1
    assert stored.next_run_at > to_iso(app.store.now())
    assert stored.remaining_runs == 2


def test_max_runs_closes_the_schedule(app):
    schedule = app.scheduler.schedules.create("what time is it", cadence="daily", max_runs=1)
    app.scheduler.tick()
    stored = app.scheduler.schedules.get(schedule.id)
    assert stored.status == "completed" and stored.exhausted


def test_missed_slots_are_skipped_not_replayed(app):
    """A machine that was off for three days must not fire three objectives in a burst."""
    old = to_iso(app.store.now() - timedelta(days=3))
    schedule = app.scheduler.schedules.create("what time is it", cadence="daily", first_run_at=old,
                                             max_runs=50)
    report = app.scheduler.tick()
    assert len(report["dispatched"]) == 1
    stored = app.scheduler.schedules.get(schedule.id)
    assert stored.runs == 1 and stored.next_run_at > to_iso(app.store.now())


def test_paused_and_cancelled_schedules_do_not_run(app):
    paused = app.scheduler.schedules.create("what time is it")
    app.scheduler.schedules.pause(paused.id)
    cancelled = app.scheduler.schedules.create("what time is it")
    app.scheduler.schedules.cancel(cancelled.id)
    assert app.scheduler.tick()["due"] == 0
    assert app.scheduler.schedules.resume(paused.id).status == "scheduled"
    assert app.scheduler.tick()["due"] == 1


def test_a_tick_dispatches_at_most_the_limit(app):
    for _ in range(MAX_PER_TICK + 2):
        app.scheduler.schedules.create("what time is it")
    report = app.scheduler.tick()
    assert len(report["dispatched"]) == MAX_PER_TICK
    assert app.scheduler.tick()["due"] == 2


# ---------------------------------------------------------------------------------------
# no duplicate objectives, no authorisation bypass
# ---------------------------------------------------------------------------------------
def test_replaying_a_slot_is_suppressed_by_the_database(app):
    schedule = app.scheduler.schedules.create("what time is it", cadence="daily", max_runs=5)
    app.scheduler.tick()
    with app.store.write() as db:                      # replay the same slot
        db.execute("UPDATE objective_schedules SET status='scheduled', next_run_at=? WHERE id=?",
                   (schedule.next_run_at, schedule.id))
    report = app.scheduler.tick()
    assert report["dispatched"][0]["result"] == "duplicate"
    created = [o for o in app.objectives.list(limit=50) if o.get("schedule_id") == schedule.id]
    assert len(created) == 1
    stored = app.scheduler.schedules.get(schedule.id)
    assert stored.runs == 1                            # a duplicate is not counted as a run
    assert any(e["kind"] == "schedule.duplicate_suppressed"
               for e in app.store.events.recent(30, include_sensitive=False))


def test_concurrent_ticks_create_exactly_one_objective(app):
    schedule = app.scheduler.schedules.create("what time is it", cadence="daily", max_runs=5)
    reports: list[dict] = []
    barrier = threading.Barrier(4)

    def tick(name: str) -> None:
        scheduler = Scheduler(app.store, dispatch=app.objectives.submit, owner=name)
        barrier.wait()
        reports.append(scheduler.tick())

    threads = [threading.Thread(target=tick, args=(f"worker{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    created = [o for o in app.objectives.list(limit=50) if o.get("schedule_id") == schedule.id]
    assert len(created) == 1, f"workers created {len(created)} objectives for one slot"
    assert sum(len(r["dispatched"]) for r in reports) >= 1


def test_a_scheduled_write_still_needs_owner_approval(app):
    schedule = app.scheduler.schedules.create("remember that the vault code rotates on friday")
    report = app.scheduler.tick()
    objective = app.objectives.objectives.get(report["dispatched"][0]["objective_id"])
    assert objective.outcome == ObjectiveOutcome.NEEDS_APPROVAL.value
    succeeded = [o for o in app.store.operations.list(limit=20)
                 if o["capability"] == "memory.remember" and o["status"] == "succeeded"]
    assert succeeded == []
    assert app.scheduler.schedules.get(schedule.id).last_outcome == "needs_approval"


def test_a_scheduled_request_cannot_bypass_a_denial(app):
    app.scheduler.schedules.create("remember that the vault code rotates on friday")
    app.scheduler.tick()
    proposed = app.store.operations.list(status="proposed", limit=5)
    assert proposed, "the write should have been proposed, not executed"
    app.tools.deny(proposed[0]["id"])
    schedule = app.scheduler.schedules.list()[0]
    view = app.objectives.resume(schedule.last_objective_id)
    assert view["objective"]["outcome"] == ObjectiveOutcome.FAILED.value
    assert not [o for o in app.store.operations.list(limit=20) if o["status"] == "succeeded"]


def test_an_invalid_scheduled_request_fails_without_creating_work(app):
    schedule = app.scheduler.schedules.create("research the best vector database")
    report = app.scheduler.tick()
    objective = app.objectives.objectives.get(report["dispatched"][0]["objective_id"])
    assert objective.outcome == ObjectiveOutcome.BLOCKED.value
    assert report["dispatched"][0]["next_run_at"] is None
    assert app.scheduler.schedules.get(schedule.id).status == "completed"


def test_scheduler_does_not_execute_capabilities_itself(app):
    """The only path to execution is the objective pipeline, which enforces policy."""
    calls: list[str] = []
    original = app.objectives.submit

    def spy(request: str, **kwargs):
        calls.append(request)
        return original(request, **kwargs)

    app.scheduler.dispatch = spy
    app.scheduler.schedules.create("what time is it")
    app.scheduler.tick()
    assert calls == ["what time is it"]


def test_tick_reports_a_lost_lease_instead_of_dispatching(app):
    squatter = app.scheduler.leases.acquire("scheduler:tick", owner="other", ttl=60)
    assert squatter is not None
    schedule = app.scheduler.schedules.create("what time is it")
    report = app.scheduler.tick()
    assert report["dispatched"] == [] and report["skipped"] == "lease_held_by_another_worker"
    assert app.scheduler.schedules.get(schedule.id).runs == 0


def test_run_forever_is_bounded(app):
    app.scheduler.schedules.create("what time is it")
    reports = app.scheduler.run_forever(interval_seconds=0.1, max_ticks=2)
    assert len(reports) == 2 and reports[0]["due"] == 1 and reports[1]["due"] == 0


def test_store_lists_schedules_by_priority(app):
    low = app.scheduler.schedules.create("what time is it", priority=5)
    high = app.scheduler.schedules.create("what time is it", priority=1)
    listed = [s.id for s in app.scheduler.schedules.list()]
    assert listed.index(high.id) < listed.index(low.id)


def test_the_background_loop_ticks_until_stopped(app):
    import time as _time

    schedule = app.scheduler.schedules.create("what time is it", cadence="daily", max_runs=2)
    thread, stop = start_background(app.scheduler, 0.2)
    deadline = _time.monotonic() + 10
    while _time.monotonic() < deadline and app.scheduler.schedules.get(schedule.id).runs == 0:
        _time.sleep(0.05)
    stop.set()
    thread.join(timeout=10)
    assert app.scheduler.schedules.get(schedule.id).runs == 1
    assert not thread.is_alive()


def test_the_background_loop_is_disabled_by_a_zero_interval(app):
    thread, stop = start_background(app.scheduler, 0)
    assert stop.is_set() and not thread.is_alive()


def test_schedule_store_can_be_used_directly(app):
    store = ScheduleStore(app.store)
    schedule = store.create("what time is it", cadence="weekly", max_runs=2)
    assert store.due(limit=5) and store.due(limit=5)[0].id == schedule.id
    assert store.cancel(schedule.id).status == "cancelled"
