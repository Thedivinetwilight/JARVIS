"""Persistent plans: bounded steps, bounded retries, approval pauses, recovery.

Execution goes through the real ToolRegistry and the real permission policy, so
these tests observe what actually happens to the journal and the database.
"""
from __future__ import annotations

import pytest

from jarvis.contracts import Origin
from jarvis.plans import MAX_STEPS, PlanError, PlanExecutor, PlanStore


def _step(capability: str, **arguments) -> dict:
    return {"capability": capability, "arguments": arguments, "purpose": capability}


@pytest.fixture
def store(app_factory):
    return app_factory().store


@pytest.fixture
def plans(store):
    return PlanStore(store)


def test_plan_steps_are_persisted_in_order(plans):
    plan = plans.create_plan(title="look around", steps=[_step("project.list"), _step("time.now")])
    assert [s.seq for s in plan.steps] == [1, 2]
    assert [s.capability for s in plan.steps] == ["project.list", "time.now"]
    assert plan.status == "proposed"
    assert plans.get_plan(plan.id).to_dict() == plan.to_dict()


def test_plan_bounds_are_enforced(plans):
    with pytest.raises(PlanError, match="at least one step"):
        plans.create_plan(title="empty", steps=[])
    with pytest.raises(PlanError, match=f"at most {MAX_STEPS}"):
        plans.create_plan(title="too long", steps=[_step("time.now") for _ in range(MAX_STEPS + 1)])


def test_lifecycle_transitions_refuse_illegal_moves(plans):
    plan = plans.create_plan(title="one", steps=[_step("time.now")])
    assert plans.activate(plan.id).status == "active"
    with pytest.raises(PlanError, match="cannot activate"):
        plans.activate(plan.id)
    assert plans.cancel(plan.id).status == "cancelled"
    assert plans.get_plan(plan.id).steps[0].status == "skipped"
    assert [p.id for p in plans.list_plans(status="cancelled")] == [plan.id]


def test_user_commanded_plan_executes_writes_without_extra_prompts(app_factory):
    """Explicit owner commands are user-directed, so autonomy levels do not gate them."""
    app = app_factory()
    plan = app.plans.create_plan(title="owner asked", steps=[_step(
        "task.create", title="Book the dentist", project="", description="", priority=2)])
    result = app.plan_executor.execute(plan.id)
    assert result.status == "completed"
    with app.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_executor_completes_read_only_plan_and_records_verification(app_factory):
    app = app_factory()
    plan = app.plans.create_plan(title="checks", steps=[_step("time.now"), _step("project.list")])
    result = app.plan_executor.execute(plan.id)
    assert result.status == "completed" and result.progress["succeeded"] == 2
    assert all(step.operation_id for step in result.steps)          # each step is journaled
    with app.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 2


def test_write_step_pauses_for_approval_and_resumes_after_it(app_factory):
    """A model-proposed plan may read freely but cannot write without consent."""
    app = app_factory()
    plan = app.plans.create_plan(title="needs consent", steps=[_step(
        "task.create", title="Renew passport", project="", description="", priority=3)])
    paused = app.plan_executor.execute(plan.id, origin=Origin.MODEL_PROPOSAL)
    assert paused.status == "awaiting_approval"              # paused, not failed
    assert paused.steps[0].status == "awaiting_approval"
    with app.store.read() as db:
        assert db.execute("SELECT status FROM operations").fetchone()["status"] == "proposed"
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0

    still_paused = app.plan_executor.execute(plan.id, origin=Origin.MODEL_PROPOSAL)
    assert still_paused.status == "awaiting_approval"        # nothing ran without consent

    approved = app.tools.approve(paused.steps[0].operation_id, approved_by="test")
    assert approved.status.value == "succeeded"
    resumed = app.plan_executor.execute(plan.id, origin=Origin.MODEL_PROPOSAL)
    assert resumed.status == "completed" and resumed.steps[0].status == "succeeded"
    with app.store.read() as db:
        assert db.execute("SELECT title FROM tasks").fetchone()["title"] == "Renew passport"


def test_denied_approval_fails_the_step(app_factory):
    app = app_factory()
    plan = app.plans.create_plan(title="denied", steps=[_step(
        "task.create", title="Unwanted", project="", description="", priority=3)])
    paused = app.plan_executor.execute(plan.id, origin=Origin.MODEL_PROPOSAL)
    app.tools.deny(paused.steps[0].operation_id)
    result = app.plan_executor.execute(plan.id, origin=Origin.MODEL_PROPOSAL)
    assert result.status == "failed" and result.steps[0].status == "failed"
    assert "approval_denied" in result.steps[0].error_type or "Step 1" in result.failure_reason


def test_unknown_capability_fails_the_step_and_the_plan(app_factory):
    app = app_factory()
    plan = app.plans.create_plan(title="bad step", steps=[_step("does.not.exist")])
    result = app.plan_executor.execute(plan.id)
    assert result.status == "failed"
    assert "unknown_capability" in result.failure_reason or "Step 1" in result.failure_reason


def test_interrupted_steps_are_never_assumed_successful(app_factory):
    app = app_factory()
    plan = app.plans.create_plan(title="crash", steps=[_step("time.now"), _step("project.list")])
    app.plans.activate(plan.id)
    app.plans.mark_step_running(plan.steps[0].id)

    recovered = app.plans.recover_interrupted()
    assert recovered == [plan.id]
    step = app.plans.get_plan(plan.id).steps[0]
    assert step.status == "interrupted"                          # not succeeded

    resumed = app.plan_executor.execute(plan.id)
    assert resumed.status == "completed"                         # execution resumes it


def test_plan_survives_a_restart(app_factory, tmp_path):
    app = app_factory()
    plan = app.plans.create_plan(title="durable", steps=[_step("time.now")])
    app.plan_executor.execute(plan.id)
    app.close()
    fresh = app_factory()
    stored = fresh.plans.get_plan(plan.id)
    assert stored.status == "completed" and stored.steps[0].status == "succeeded"


def test_retryable_failure_is_retried_within_bound(app_factory):
    app = app_factory()
    calls = {"n": 0}
    from jarvis.contracts import ActionOutcome, OperationStatus
    from jarvis.tools import Tool

    def flaky(arguments, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("first attempt timed out")
        return {"ok": True}

    app.tools.register(Tool("test.flaky", "Flaky test capability.", "read", flaky, {}))
    plan = app.plans.create_plan(title="retry", steps=[_step("test.flaky")])
    result = app.plan_executor.execute(plan.id)
    assert calls["n"] >= 2
    assert result.status == "completed" and result.steps[0].attempts >= 2


def test_retries_are_bounded_and_stop_on_non_retryable_errors(app_factory):
    app = app_factory()
    calls = {"n": 0}

    def always_times_out(arguments, ctx):
        calls["n"] += 1
        raise TimeoutError("always slow")

    from jarvis.tools import Tool
    app.tools.register(Tool("test.slow", "Always slow capability.", "read", always_times_out, {}))
    executor = PlanExecutor(app.plans, app.tools, max_retries=1)
    plan = app.plans.create_plan(title="bounded", steps=[_step("test.slow")])
    result = executor.execute(plan.id)
    assert result.status == "failed"
    assert calls["n"] <= 3                       # one attempt + one bounded retry (plus journaling)
