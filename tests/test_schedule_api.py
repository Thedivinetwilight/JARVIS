"""Scheduling over HTTP and the CLI, plus structural replanning through the objective engine.

Real HTTP against a real loopback server, real SQLite, real CLI subprocesses: the interfaces
the owner actually uses are the ones under test.
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

from jarvis import manifests
from jarvis.api import make_handler
from jarvis.contracts import Origin
from jarvis.objectives import MAX_PLAN_REVISIONS, PlanSpec, StepSpec, default_criteria

TOKEN = "schedule-operator-token-" + "z" * 32


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def server(app_factory):
    app = app_factory()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, token=TOKEN))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=data, headers=headers)
    response = conn.getresponse()
    payload = json.loads(response.read() or b"null")
    conn.close()
    return response.status, payload


# ---------------------------------------------------------------------------------------
# HTTP interface
# ---------------------------------------------------------------------------------------
def test_schedules_are_created_listed_and_shown(server):
    _, port = server
    status, created = call(port, "POST", "/v1/schedules",
                           {"request": "what time is it", "cadence": "daily", "max_runs": 2,
                            "priority": 2, "deadline_minutes": 15})
    assert status == 201, created
    assert created["cadence"] == "daily" and created["remaining_runs"] == 2
    listed = call(port, "GET", "/v1/schedules")
    assert listed[0] == 200 and [s["id"] for s in listed[1]] == [created["id"]]
    shown = call(port, "GET", f"/v1/schedules/{created['id']}")
    assert shown[0] == 200 and shown[1]["schedule"]["id"] == created["id"]


def test_an_invalid_schedule_is_a_client_error(server):
    _, port = server
    status, payload = call(port, "POST", "/v1/schedules", {"cadence": "daily"})
    assert status == 400 and payload["error"] == "request_required"
    status, payload = call(port, "POST", "/v1/schedules",
                           {"request": "what time is it", "cadence": "never"})
    assert status == 400 and payload["error"] == "invalid_schedule"


def test_ticking_returns_a_report(server):
    app, port = server
    call(port, "POST", "/v1/schedules", {"request": "what time is it"})
    status, report = call(port, "POST", "/v1/schedules/tick", {})
    assert status == 200 and report["due"] == 1
    assert report["dispatched"][0]["result"] == "dispatched"
    assert call(port, "POST", "/v1/schedules/tick", {})[1]["due"] == 0


def test_pause_resume_and_cancel(server):
    app, port = server
    schedule = call(port, "POST", "/v1/schedules", {"request": "what time is it", "cadence": "daily"})[1]
    assert call(port, "POST", f"/v1/schedules/{schedule['id']}/pause", {})[1]["status"] == "paused"
    assert call(port, "POST", "/v1/schedules/tick", {})[1]["due"] == 0
    assert call(port, "POST", f"/v1/schedules/{schedule['id']}/resume", {})[1]["status"] == "scheduled"
    assert call(port, "POST", "/v1/schedules/tick", {})[1]["due"] == 1
    assert call(port, "POST", f"/v1/schedules/{schedule['id']}/cancel", {})[1]["status"] == "cancelled"


def test_run_now_dispatches_a_future_schedule_once(server):
    app, port = server
    later = "2999-01-01T00:00:00+00:00"
    schedule = call(port, "POST", "/v1/schedules",
                    {"request": "what time is it", "cadence": "daily", "first_run_at": later})[1]
    assert call(port, "POST", "/v1/schedules/tick", {})[1]["due"] == 0
    status, report = call(port, "POST", f"/v1/schedules/{schedule['id']}/run-now", {})
    assert status == 200 and len(report["dispatched"]) == 1
    assert report["dispatched"][0]["result"] == "dispatched"


def test_unknown_schedule_ids_are_not_found(server):
    _, port = server
    assert call(port, "GET", "/v1/schedules/sch_missing")[0] == 404
    assert call(port, "POST", "/v1/schedules/sch_missing/pause", {})[0] == 404


def test_scheduling_requires_the_plan_scopes(server):
    _, port = server
    issued = call(port, "POST", "/v1/clients/pairing",
                  {"label": "reader", "platform": "android",
                   "scopes": [manifests.SCOPE_STATUS_READ, manifests.SCOPE_PLANS_READ]})[1]
    token = call(port, "POST", "/v1/pairing", {"code": issued["code"]}, token=None)[1]["token"]
    assert call(port, "GET", "/v1/schedules", token=token)[0] == 200
    status, payload = call(port, "POST", "/v1/schedules",
                           {"request": "what time is it"}, token=token)
    assert status == 403 and payload["error"] == "scope_denied"
    assert call(port, "POST", "/v1/schedules/tick", {}, token=token)[0] == 403


def test_an_unauthenticated_request_cannot_schedule(server):
    _, port = server
    assert call(port, "POST", "/v1/schedules", {"request": "what time is it"}, token=None)[0] == 401


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def _cli(home, *args):
    env = {**os.environ, "PYTHONPATH": str(manifests.__file__).split("src")[0] + "src",
           "JARVIS_HOME": str(home)}
    return subprocess.run([sys.executable, "-m", "jarvis", *args], capture_output=True,
                          text=True, env=env, timeout=120)


def test_cli_schedule_add_tick_list(tmp_path):
    home = tmp_path / "cli-home"
    workspace = tmp_path / "cli-workspace"
    workspace.mkdir(parents=True)
    env = {**os.environ, "PYTHONPATH": str(manifests.__file__).split("src")[0] + "src",
           "JARVIS_HOME": str(home), "JARVIS_WORKSPACE": str(workspace)}

    def run(*args):
        return subprocess.run([sys.executable, "-m", "jarvis", *args], capture_output=True,
                              text=True, env=env, timeout=120)

    added = run("schedule", "add", "what time is it", "--cadence", "daily", "--max-runs", "2")
    assert added.returncode == 0, added.stderr
    schedule = json.loads(added.stdout)
    assert schedule["cadence"] == "daily" and schedule["remaining_runs"] == 2

    ticked = run("schedule", "tick")
    assert ticked.returncode == 0, ticked.stderr
    assert json.loads(ticked.stdout)["dispatched"][0]["result"] == "dispatched"

    listed = run("schedule", "list")
    assert listed.returncode == 0
    stored = json.loads(listed.stdout)
    assert stored[0]["runs"] == 1 and stored[0]["last_outcome"] == "completed"

    paused = run("schedule", "pause", schedule["id"])
    assert json.loads(paused.stdout)["status"] == "paused"


# ---------------------------------------------------------------------------------------
# structural replanning
# ---------------------------------------------------------------------------------------
def _start_with_plan(app, request: str, steps: list[StepSpec], *, planner: str = "model") -> str:
    spec = PlanSpec(request, steps, criteria=default_criteria(steps))
    record = app.objectives.objectives.create(
        request=request, objective=request, desired_outcome="done", constraints=[],
        success_criteria=spec.criteria, memory_ids=[], status="planned", outcome=None,
        outcome_reason="planned, not yet executed", priority=3, deadline=None,
        budget={"max_seconds": 30, "used_seconds": 0.0, "replans": 3}, planner=planner,
        origin=Origin.MODEL_PROPOSAL.value)
    plan = app.plans.create_plan(title=request, steps=[step.to_dict() for step in steps],
                                 objective_id=record.id)
    app.objectives.objectives.set_status(record.id, "planned", plan_id=plan.id)
    return record.id


def test_an_unplannable_plan_is_revised_and_the_old_plan_is_kept_for_history(app):
    objective_id = _start_with_plan(
        app, "what time is it", [StepSpec("vector.search", {"q": "x"}, "search vectors")])
    first_plan_id = app.objectives.objectives.get(objective_id).plan_id
    view = app.objectives.run(objective_id)
    objective = view["objective"]
    assert objective["outcome"] == "completed"
    assert objective["revision"] == 2 and objective["replans"] == 1
    assert [s["capability"] for s in view["plan"]["steps"]] == ["time.now"]
    old = app.plans.get_plan(first_plan_id)
    assert old.status == "superseded" and old.superseded_by == objective["plan_id"]
    assert view["plan"]["revision"] == 2 and view["plan"]["replaces_plan_id"] == first_plan_id
    assert any(event["kind"] == "objective.replanned_structurally" for event in view["events"])


def test_verified_work_is_carried_over_exactly_once(app):
    (app.settings.workspace / "deploy.md").write_text("The deploy window is Friday.\n",
                                                     encoding="utf-8")
    request = "what time is it and search the workspace for deploy"
    objective_id = _start_with_plan(
        app, request, [StepSpec("time.now", {}, "read the clock"),
                       StepSpec("vector.search", {"q": "x"}, "a capability that does not exist")])
    first = app.objectives.run(objective_id)
    steps = first["plan"]["steps"]
    # The succeeded clock read is carried once; the impossible step is re-decomposed into
    # reachable work instead of being retried.
    assert [s["capability"] for s in steps] == ["time.now", "workspace.search"]
    assert sum(1 for s in steps if s["capability"] == "time.now") == 1
    assert steps[0]["status"] == "succeeded" and steps[0]["operation_id"] is not None
    assert first["objective"]["outcome"] == "completed"
    assert first["objective"]["revision"] == 2
    # The carry-over is evidence, not a re-execution: one clock operation in total.
    clock_ops = [o for o in app.store.operations.list(limit=20) if o["capability"] == "time.now"]
    assert len(clock_ops) == 1


def test_a_revision_must_change_what_runs(app):
    """Re-decomposing into the same steps is not a revision: it would be a retry in disguise."""
    objective_id = _start_with_plan(
        app, "read missing-file.md", [StepSpec("workspace.read", {"relative_path": "missing-file.md"},
                                               "read a file that is not there")])
    view = app.objectives.run(objective_id)
    assert view["objective"]["revision"] == 1
    assert view["objective"]["outcome"] == "failed"


def test_success_criteria_are_re_derived_and_audited(app):
    objective_id = _start_with_plan(
        app, "what time is it", [StepSpec("vector.search", {"q": "x"}, "search vectors")])
    view = app.objectives.run(objective_id)
    criteria = [item.get("capability") for item in view["objective"]["success_criteria"]]
    assert criteria == [None, "time.now"]
    assert any(event["kind"] == "objective.criteria_revised" for event in view["events"])


def test_a_denied_approval_is_never_replanned(app):
    record = app.objectives.create("remember that the vault code is in the drawer",
                                   origin=Origin.MODEL_PROPOSAL)
    app.objectives.run(record.id)
    proposed = app.store.operations.list(status="proposed", limit=5)
    assert proposed
    app.tools.deny(proposed[0]["id"])
    view = app.objectives.resume(record.id)
    assert view["objective"]["outcome"] == "failed"
    assert view["objective"]["revision"] == 1
    assert not any(event["kind"] == "objective.replanned_structurally" for event in view["events"])
    assert not app.store.operations.list(status="proposed", limit=5)


def test_revisions_are_capped(app):
    objective_id = _start_with_plan(
        app, "what time is it", [StepSpec("vector.search", {"q": "x"}, "search vectors")])
    app.objectives.run(objective_id)
    objective = app.objectives.objectives.get(objective_id)
    assert objective.revision <= MAX_PLAN_REVISIONS + 1
    plans = [p for p in app.plans.list_plans(limit=20) if p.objective_id == objective_id]
    assert len(plans) <= MAX_PLAN_REVISIONS + 1


def test_a_second_worker_is_refused_while_the_lease_is_held(app):
    objective_id = _start_with_plan(app, "what time is it", [StepSpec("time.now", {}, "clock")])
    squatter = app.objectives.leases.acquire(objective_id, owner="other-worker", ttl=60)
    assert squatter is not None
    view = app.objectives.run(objective_id)
    assert view["objective"]["outcome"] is None                  # untouched
    assert any(event["kind"] == "objective.run_refused" for event in view["events"])
    app.objectives.leases.release(squatter)
    assert app.objectives.run(objective_id)["objective"]["outcome"] == "completed"
