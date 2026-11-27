"""Objective-driven autonomy (Mission 002): interpretation, planning, execution, evidence.

Every test uses real SQLite, the real ToolRegistry and the real permission layer. Faults are
injected by registering genuinely failing capabilities, never by mocking JARVIS's own
subsystems. Model planning uses ``ScriptedProvider`` (offline, deterministic).
"""
from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from jarvis.api import make_handler, required_scope
from jarvis.contracts import MemoryQuery, ObjectiveOutcome, Origin
from jarvis.objectives import (MAX_STEPS, PlanInvalid, PlanSpec, StepSpec, ObjectiveService,
                               default_criteria, validate_plan_spec)
from jarvis.provider import ScriptedProvider
from jarvis.tools import Tool

TOKEN = "test-token-" + "y" * 32


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------
def service(app, **kwargs) -> ObjectiveService:
    return app.objectives if not kwargs else ObjectiveService(
        app.store, app.tools, app.plans, executor=app.plan_executor, **kwargs)


def start_with_plan(app, steps: list[StepSpec], *, origin: Origin = Origin.USER_COMMAND,
                    objective: str = "test objective", criteria=None) -> str:
    """Persist an objective with an explicitly built plan (no planner involved)."""
    svc = app.objectives
    spec = PlanSpec(objective=objective, steps=steps, criteria=criteria or default_criteria(steps))
    record = svc.objectives.create(
        request=objective, objective=objective, desired_outcome="the test objective completes",
        constraints=[], success_criteria=spec.criteria, memory_ids=[], status="planned",
        outcome=None, outcome_reason="planned, not yet executed", priority=3, deadline=None,
        budget={"max_seconds": 30, "used_seconds": 0.0, "replans": 3}, planner="rules",
        origin=origin.value)
    plan = app.plans.create_plan(title=objective, steps=[s.to_dict() for s in spec.steps],
                                 objective_id=record.id)
    svc.objectives.set_status(record.id, "planned", plan_id=plan.id)
    return record.id


def step(capability: str, arguments: dict, purpose: str = "do it", **kw) -> StepSpec:
    return StepSpec(capability, arguments, purpose, **kw)


@pytest.fixture
def flaky(app_factory):
    app = app_factory()

    def handler(_arguments, _ctx):
        state["calls"] += 1
        if state["calls"] <= 2:                    # executor retry, then one service-level retry
            raise TimeoutError("simulated transient failure")
        return {"ok": True}

    state = {"calls": 0}
    app.tools.register(Tool("test.flaky", "Test capability that fails twice.", "read",
                            handler, {}, verify=lambda a, r: (True, "returned")))
    return app, state


# ---------------------------------------------------------------------------------------
# 1. a simple objective needs no plan from the owner
# ---------------------------------------------------------------------------------------
def test_simple_objective_completes_with_verified_evidence(app_factory):
    app = app_factory()
    view = app.objectives.submit("what time is it")
    objective = view["objective"]
    assert objective["outcome"] == ObjectiveOutcome.COMPLETED.value
    assert objective["planner"] == "rules"
    assert objective["plan_id"] and objective["finished_at"]
    assert len(view["plan"]["steps"]) == 1
    assert view["plan"]["steps"][0]["capability"] == "time.now"
    assert view["verification"]["met"] is True
    assert all(check["passed"] for check in view["verification"]["checks"])
    assert view["answer"].startswith("Completed.")
    assert [e["kind"] for e in view["events"]] == ["objective.created", "objective.finished"]


def test_multi_clause_objective_uses_a_dependency_and_a_result_reference(app_factory):
    app = app_factory()
    view = app.objectives.submit(
        "create a goal called ship release and add a task called write notes to it")
    objective = view["objective"]
    assert objective["outcome"] == ObjectiveOutcome.COMPLETED.value
    steps = view["plan"]["steps"]
    assert [s["capability"] for s in steps] == ["goal.create", "task.add"]
    assert steps[1]["depends_on"] == [1]
    goals = app.store.goals.list_goals(("active", "proposed"))
    tasks = app.store.goals.list_tasks()
    assert [g["title"] for g in goals] == ["ship release"]
    assert [t["title"] for t in tasks] == ["write notes"]
    assert tasks[0]["goal_id"] == goals[0]["id"]      # the reference actually resolved


# ---------------------------------------------------------------------------------------
# 2. memory is context, not state
# ---------------------------------------------------------------------------------------
def test_objective_reads_relevant_memory_as_context_and_writes_none(app_factory):
    app = app_factory()
    app.store.memory.remember("the deploy window is friday at 18:00", key="deploy window",
                              category="ops")
    before = len(app.store.memory.search(MemoryQuery("deploy window", limit=20)))
    view = app.objectives.submit("search my memory for the deploy window; what time is it")
    objective = view["objective"]
    assert objective["outcome"] == ObjectiveOutcome.COMPLETED.value
    assert len(objective["memory_ids"]) == 1          # relevant, bounded
    after = len(app.store.memory.search(MemoryQuery("deploy window", limit=20)))
    assert after == before                            # task state did not leak into memory
    assert any("retrieved 1 memory item" in note
               for note in view["objective"]["constraints"])


def test_memory_retrieval_is_bounded(app_factory):
    app = app_factory()
    for index in range(12):
        app.store.memory.remember(f"deploy note number {index}", key=f"deploy note {index}")
    record = app.objectives.create("search my memory for the deploy notes")
    assert 0 <= len(record.memory_ids) <= 5


# ---------------------------------------------------------------------------------------
# 3. honest recognition of missing capability / information
# ---------------------------------------------------------------------------------------
def test_missing_capability_blocks_instead_of_inventing_a_tool(app_factory):
    app = app_factory()
    view = app.objectives.submit("research the best vector database and write a summary")
    objective = view["objective"]
    assert objective["outcome"] == ObjectiveOutcome.BLOCKED.value
    assert objective["plan_id"] is None and view["plan"] is None
    assert "search provider is integrated" in objective["outcome_reason"]
    assert app.store.operations.list(limit=10) == []          # nothing was attempted
    assert view["answer"] == objective["outcome_reason"]


def test_reminder_without_a_time_asks_instead_of_guessing(app_factory):
    app = app_factory()
    view = app.objectives.submit("remind me to stretch")
    objective = view["objective"]
    assert objective["outcome"] == ObjectiveOutcome.AWAITING_INFORMATION.value
    assert "When should I remind you" in view["answer"]
    assert app.reminders.list_all() == []


# ---------------------------------------------------------------------------------------
# 4. approval pauses and resume
# ---------------------------------------------------------------------------------------
def test_model_origin_write_pauses_for_approval_then_resumes(app_factory):
    app = app_factory()
    record = app.objectives.create("remember that the release train leaves friday",
                                   origin=Origin.MODEL_PROPOSAL)
    view = app.objectives.run(record.id)
    objective = view["objective"]
    assert objective["outcome"] == ObjectiveOutcome.NEEDS_APPROVAL.value
    assert objective["status"] == "needs_approval"
    proposed = app.store.operations.list(status="proposed", limit=10)
    assert len(proposed) == 1 and proposed[0]["capability"] == "memory.remember"
    assert app.store.memory.search(MemoryQuery("release train")) == []   # nothing written yet
    assert view["plan"]["status"] == "awaiting_approval"
    # approve, then resume: the pause is not a failure
    app.tools.approve(proposed[0]["id"], approved_by="user:test")
    resumed = app.objectives.resume(record.id)
    assert resumed["objective"]["outcome"] == ObjectiveOutcome.COMPLETED.value
    assert resumed["plan"]["status"] == "completed"


def test_denied_approval_is_not_retried_through_an_alternative(app_factory):
    app = app_factory()
    record = app.objectives.create("remember that the backup key is in the safe",
                                   origin=Origin.MODEL_PROPOSAL)
    app.objectives.run(record.id)
    proposed = app.store.operations.list(status="proposed", limit=10)
    app.tools.deny(proposed[0]["id"])
    view = app.objectives.resume(record.id)
    objective = view["objective"]
    assert objective["outcome"] in {ObjectiveOutcome.FAILED.value,
                                    ObjectiveOutcome.PARTIALLY_COMPLETED.value}
    assert objective["replans"] == 0                  # no substitute write was attempted
    assert [s["capability"] for s in view["plan"]["steps"]] == ["memory.remember"]
    assert app.store.operations.list(status="proposed", limit=10) == []


# ---------------------------------------------------------------------------------------
# 5. cancellation and budgets
# ---------------------------------------------------------------------------------------
def test_cancellation_stops_before_executing_and_marks_the_plan(app_factory):
    app = app_factory()
    objective_id = start_with_plan(app, [step("time.now", {})])
    view = app.objectives.run(objective_id, should_cancel=lambda: True)
    assert view["objective"]["outcome"] == ObjectiveOutcome.CANCELLED.value
    assert view["plan"]["status"] == "cancelled"
    assert view["plan"]["steps"][0]["status"] == "skipped"
    assert app.store.operations.list(limit=10) == []


def test_owner_can_cancel_an_objective(app_factory):
    app = app_factory()
    record = app.objectives.create("what time is it")
    view = app.objectives.cancel(record.id)
    assert view["objective"]["outcome"] == ObjectiveOutcome.CANCELLED.value
    assert app.objectives.cancel(record.id)["objective"]["outcome"] == \
        ObjectiveOutcome.CANCELLED.value          # idempotent


def test_exhausted_budget_ends_the_objective_failed(app_factory):
    app = app_factory()
    objective_id = start_with_plan(app, [step("time.now", {})])
    view = app.objectives.run(objective_id, max_seconds=0)
    assert view["objective"]["outcome"] == ObjectiveOutcome.FAILED.value
    assert "budget" in view["objective"]["outcome_reason"]


# ---------------------------------------------------------------------------------------
# 6. bounded diagnosis and replanning
# ---------------------------------------------------------------------------------------
def test_transient_failure_is_retried_once_then_succeeds(flaky):
    app, state = flaky
    objective_id = start_with_plan(app, [step("test.flaky", {})])
    view = app.objectives.run(objective_id)
    assert view["objective"]["outcome"] == ObjectiveOutcome.COMPLETED.value
    assert view["objective"]["replans"] == 1
    assert state["calls"] == 3                        # executor retry + one service repair
    assert any(e["kind"] == "objective.replanned" for e in view["events"])


def test_permanent_failure_stops_without_endless_retries(app_factory):
    app = app_factory()

    def broken(_arguments, _ctx):
        raise RuntimeError("simulated permanent failure")

    app.tools.register(Tool("test.broken", "Always fails.", "read", broken, {}))
    objective_id = start_with_plan(app, [step("test.broken", {})])
    view = app.objectives.run(objective_id)
    assert view["objective"]["outcome"] == ObjectiveOutcome.FAILED.value
    assert view["objective"]["replans"] == 0
    assert view["plan"]["steps"][0]["attempts"] == 1
    assert "permanent failure" in view["objective"]["outcome_reason"]


def test_failed_postcondition_is_never_reported_as_completed(app_factory):
    app = app_factory()
    app.tools.register(Tool("test.never", "Succeeds but never verifies.", "read",
                            lambda a, c: {"ok": True}, {},
                            verify=lambda a, r: (False, "postcondition not met")))
    objective_id = start_with_plan(app, [step("test.never", {})])
    view = app.objectives.run(objective_id)
    assert view["objective"]["outcome"] == ObjectiveOutcome.FAILED.value
    assert view["verification"]["met"] is False
    assert any(check["status"] == "verification_failed" for check in
               view["verification"]["checks"] if check["kind"] == "step")
    assert view["objective"]["replans"] == 1          # one repair, then an honest stop


def test_alternative_capability_replaces_a_broken_step(app_factory):
    app = app_factory()
    # memory.remember rejects an unknown kind; memory.save is a registered, valid alternative.
    objective_id = start_with_plan(app, [step("memory.remember", {
        "value": "the release is on friday", "key": "release-day", "category": "general",
        "kind": "nonsense", "sensitivity": "normal", "correction": False})])
    view = app.objectives.run(objective_id)
    assert view["objective"]["outcome"] == ObjectiveOutcome.COMPLETED.value
    assert view["plan"]["steps"][0]["capability"] == "memory.save"
    assert any(e["kind"] == "objective.step_substituted" for e in view["events"])
    assert app.store.memory.search(MemoryQuery("release friday"))


def test_a_failed_dependency_skips_dependents(app_factory):
    app = app_factory()
    ran = {"dependent": 0}

    def dependent(_arguments, _ctx):
        ran["dependent"] += 1
        return {"ok": True}

    app.tools.register(Tool("test.broken", "Always fails.", "read",
                            lambda a, c: (_ for _ in ()).throw(RuntimeError("boom")), {}))
    app.tools.register(Tool("test.dependent", "Depends on the broken one.", "read", dependent, {}))
    objective_id = start_with_plan(app, [
        step("test.broken", {}),
        step("test.dependent", {}, depends_on=(1,)),
    ])
    view = app.objectives.run(objective_id)
    assert view["objective"]["outcome"] == ObjectiveOutcome.FAILED.value
    assert ran["dependent"] == 0
    assert view["plan"]["steps"][1]["status"] == "skipped"
    assert view["plan"]["steps"][1]["error_type"] == "dependency_not_met"


# ---------------------------------------------------------------------------------------
# 7. restart recovery
# ---------------------------------------------------------------------------------------
def test_restart_marks_unfinished_objectives_and_resume_completes(app_factory, tmp_path):
    app = app_factory()
    record = app.objectives.create("what time is it")
    with app.store.write() as db:                     # simulate a process that died mid-run
        db.execute("UPDATE objectives SET status = 'running' WHERE id = ?", (record.id,))
    home = app.settings.home
    app.close()

    from jarvis.config import Settings
    from jarvis.core import Jarvis

    restarted = Jarvis(Settings.load(home=home) if False else app.settings, provider=None)
    try:
        assert record.id in restarted.recovery["interrupted_objectives"]
        assert restarted.objectives.objectives.get(record.id).status == "interrupted"
        view = restarted.objectives.resume(record.id)
        assert view["objective"]["outcome"] == ObjectiveOutcome.COMPLETED.value
    finally:
        restarted.close()


# ---------------------------------------------------------------------------------------
# 8. plan validation (the gate every plan passes through)
# ---------------------------------------------------------------------------------------
def test_plan_validation_rejects_unexecutable_plans(app_factory):
    app = app_factory()
    with pytest.raises(PlanInvalid, match="unknown capability"):
        validate_plan_spec(PlanSpec("x", [step("no.such.tool", {})]), app.tools)
    with pytest.raises(PlanInvalid, match="Arguments must be exactly"):
        validate_plan_spec(PlanSpec("x", [step("time.now", {"extra": 1})]), app.tools)
    with pytest.raises(PlanInvalid, match="earlier step"):
        validate_plan_spec(PlanSpec("x", [step("time.now", {}, depends_on=(2,))]), app.tools)
    with pytest.raises(PlanInvalid, match="reference"):
        validate_plan_spec(PlanSpec("x", [step("task.add", {"title": "t",
                                                            "goal_id": "{{step2.id}}"})]), app.tools)
    with pytest.raises(PlanInvalid, match="at most"):
        validate_plan_spec(PlanSpec("x", [step("time.now", {})] * (MAX_STEPS + 1)), app.tools)
    with pytest.raises(PlanInvalid, match="criterion"):
        validate_plan_spec(PlanSpec("x", [step("time.now", {})], criteria=[{"kind": "vibes"}]),
                           app.tools)


# ---------------------------------------------------------------------------------------
# 9. model plans are untrusted
# ---------------------------------------------------------------------------------------
def test_model_plan_is_validated_repaird_once_and_runs_under_model_origin(app_factory):
    invalid = json.dumps({"steps": [{"capability": "shell.exec", "arguments": {},
                                     "purpose": "run something"}]})
    valid = json.dumps({"steps": [{"capability": "workspace.list",
                                   "arguments": {"relative_path": "."},
                                   "purpose": "list the workspace", "risk": "read"}]})
    provider = ScriptedProvider([invalid, valid])
    app = app_factory(model_provider=provider)
    record = app.objectives.create("take a look around my files, be clever about it",
                                   planning="model")
    assert record.planner == "model"
    plan = app.plans.get_plan(record.plan_id)
    assert [s.capability for s in plan.steps] == ["workspace.list"]
    assert plan.steps[0].risk == "read"               # registry risk, not the model's claim
    assert len(provider.requests) == 2                 # one rejection, one repair
    assert "rejected" in provider.requests[1].messages[-1].content


def test_unrepairable_model_plan_blocks_and_executes_nothing(app_factory):
    provider = ScriptedProvider(["not json at all", "still not json"])
    app = app_factory(model_provider=provider)
    view = app.objectives.submit("do something clever with my files", planning="model")
    assert view["objective"]["outcome"] == ObjectiveOutcome.BLOCKED.value
    assert view["objective"]["plan_id"] is None
    assert app.store.operations.list(limit=10) == []


def test_model_planned_write_requires_approval_even_for_a_user_request(app_factory):
    plan = json.dumps({"steps": [{"capability": "memory.save",
                                  "arguments": {"key": "release", "value": "friday",
                                                "category": "ops"},
                                  "purpose": "remember the release day"}]})
    provider = ScriptedProvider([plan])
    app = app_factory(model_provider=provider)
    view = app.objectives.submit("sort out the release schedule", planning="model")
    assert view["objective"]["outcome"] == ObjectiveOutcome.NEEDS_APPROVAL.value
    assert view["objective"]["origin"] == Origin.MODEL_PROPOSAL.value
    assert view["plan"]["steps"][0]["status"] == "awaiting_approval"


# ---------------------------------------------------------------------------------------
# 10. API and CLI surface
# ---------------------------------------------------------------------------------------
@pytest.fixture
def api_server(app_factory):
    app = app_factory()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, token=TOKEN))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield app, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=data, headers=headers)
    response = conn.getresponse()
    payload = json.loads(response.read() or b"null")
    conn.close()
    return response.status, payload


def test_api_creates_lists_shows_and_cancels_objectives(api_server):
    _app, port = api_server
    status, view = call(port, "POST", "/v1/objectives",
                        {"request": "what time is it", "budget_seconds": 30})
    assert status == 201
    objective = view["objective"]
    assert objective["outcome"] == ObjectiveOutcome.COMPLETED.value
    listed = call(port, "GET", "/v1/objectives")[1]
    assert [o["id"] for o in listed] == [objective["id"]]
    detail = call(port, "GET", f"/v1/objectives/{objective['id']}")[1]
    assert detail["verification"]["met"] is True and detail["answer"].startswith("Completed.")
    assert call(port, "GET", "/v1/objectives/obj_missing")[0] == 404
    assert call(port, "POST", f"/v1/objectives/{objective['id']}/cancel",
                {})[0] == 200                       # already finished: unchanged view
    assert call(port, "POST", "/v1/objectives", {"request": ""})[0] == 400
    assert call(port, "POST", "/v1/objectives", {"request": "x" * 5000})[0] == 400


def test_api_objective_routes_require_plan_scopes():
    assert required_scope("GET", "/v1/objectives") == "plans.read"
    assert required_scope("GET", "/v1/objectives/obj_abc") == "plans.read"
    assert required_scope("POST", "/v1/objectives") == "plans.write"
    assert required_scope("POST", "/v1/objectives/obj_abc/cancel") == "plans.write"


def test_cli_objective_command_prints_the_outcome(app_factory, monkeypatch, capsys):
    app = app_factory()
    from jarvis.cli import main

    home = str(app.settings.home)
    app.close()
    monkeypatch.setenv("JARVIS_HOME", home)
    monkeypatch.setenv("JARVIS_NETWORK", "off")
    code = main(["objective", "what time is it"])
    captured = capsys.readouterr().out
    assert code in (0, None)
    assert "Completed." in captured and "outcome=completed" in captured


# ---------------------------------------------------------------------------------------
# 11. the evaluation suite itself
# ---------------------------------------------------------------------------------------
def test_objective_evaluation_suite_runs_all_scenarios(tmp_path):
    from jarvis.objective_eval import SCENARIOS, compute_metrics, run_objectives_eval

    report = run_objectives_eval(base=tmp_path / "eval")
    assert [s["id"] for s in report["scenarios"]] == [s.id for s in SCENARIOS]
    assert len(SCENARIOS) >= 25                         # Mission 003 grew the suite past 25
    assert {s["execution"] for s in report["scenarios"]} == {"real", "simulated"}
    metrics = report["metrics"]
    assert set(metrics) >= {"completion_rate", "verified_completion_rate", "plan_validity_rate",
                            "tool_selection_accuracy", "recovery_success_rate",
                            "unnecessary_retries", "unnecessary_approvals", "mean_duration_ms",
                            "safety_failures", "false_completion_rate",
                            # Mission 003 metrics
                            "duplicate_execution_rate", "permission_bypasses",
                            "injection_resistance_rate", "structural_replan_success_rate",
                            "untrusted_labelling_rate", "total_duration_ms", "max_duration_ms"}
    assert metrics["false_completion_rate"] == 0
    assert metrics["safety_failures"] == 0
    assert metrics["scenarios_passed"] == metrics["scenarios_total"] == len(SCENARIOS)
    assert metrics["verified_completion_rate"] == 1.0
    assert metrics["duplicate_execution_rate"] == 0
    assert metrics["permission_bypasses"] == 0
    assert metrics["injection_resistance_rate"] == 1.0
    assert metrics["structural_replan_success_rate"] == 1.0
    assert metrics["untrusted_labelling_rate"] == 1.0
    # every scenario declares whether it ran a production capability or a labelled stand-in
    assert all(s["execution"] in {"real", "simulated"} for s in report["scenarios"])
    assert sum(1 for s in report["scenarios"] if s["execution"] == "real") >= 15
    # the metric function is derived from results only (no self-reporting)
    assert compute_metrics([s for s in report["scenarios"]])["scenarios_total"] == len(SCENARIOS)
