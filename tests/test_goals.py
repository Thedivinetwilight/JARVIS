"""Goals and tasks: state machine, dependencies, evidence and derived goal status."""
from __future__ import annotations

import pytest

from jarvis.db import Store
from jarvis.goals import DependencyBlocked, EvidenceRejected, GoalError, InvalidTransition


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "jarvis.sqlite3")


def test_goal_with_steps_and_progress(store):
    goal = store.goals.create_goal("ship v1", steps=["design", "build", "test"])
    progress = store.goals.progress(goal["id"])
    assert progress["total"] == 3 and progress["done"] == 0
    assert [t["title"] for t in progress["tasks"]] == ["design", "build", "test"]
    assert progress["next"][0]["title"] == "design"


def test_invalid_transitions_are_rejected(store):
    task = store.add_task("write report")
    store.goals.set_task_status(task["id"], "cancelled", actor="user")
    with pytest.raises(InvalidTransition):
        store.goals.set_task_status(task["id"], "done", actor="user")
    with pytest.raises(GoalError):
        store.goals.set_task_status(task["id"], "finished", actor="user")
    assert store.goals.set_task_status(task["id"], "cancelled", actor="user")["status"] == "cancelled"  # idempotent


def test_reopen_requires_reason_and_reopens_goal(store):
    goal = store.goals.create_goal("tiny goal", steps=["only step"])
    task = store.goals.progress(goal["id"])["tasks"][0]
    store.goals.set_task_status(task["id"], "done", actor="user")
    assert store.goals.get_goal(goal["id"])["status"] == "completed"
    with pytest.raises(InvalidTransition):
        store.goals.set_task_status(task["id"], "in_progress", actor="user")
    store.goals.set_task_status(task["id"], "in_progress", actor="user", reason="found a bug")
    assert store.goals.get_goal(goal["id"])["status"] == "active"
    kinds = [e["kind"] for e in store.events.recent(20)]
    assert "goal.completed" in kinds and "goal.reopened" in kinds


def test_dependencies_block_start_and_completion(store):
    goal = store.goals.create_goal("ordered", steps=["first", "second"])
    first, second = store.goals.progress(goal["id"])["tasks"]
    store.goals.add_dependency(second["id"], first["id"])
    with pytest.raises(DependencyBlocked):
        store.goals.set_task_status(second["id"], "in_progress", actor="user")
    with pytest.raises(GoalError):
        store.goals.add_dependency(first["id"], second["id"])  # would create a cycle
    progress = store.goals.progress(goal["id"])
    assert [t["id"] for t in progress["next"]] == [first["id"]]
    assert progress["waiting_on_dependencies"] == [{"task": second["id"], "depends_on": [first["id"]]}]
    store.goals.set_task_status(first["id"], "done", actor="user")
    store.goals.set_task_status(second["id"], "done", actor="user")
    assert store.goals.get_goal(goal["id"])["status"] == "completed"


def test_completion_evidence_rules(store):
    task = store.add_task("deploy")
    with pytest.raises(EvidenceRejected):
        store.goals.set_task_status(task["id"], "done", actor="jarvis")
    with pytest.raises(EvidenceRejected):
        store.goals.set_task_status(task["id"], "done", actor="model", evidence={"type": "user_confirmation"})
    with pytest.raises(EvidenceRejected):
        store.goals.set_task_status(task["id"], "done", actor="jarvis",
                                    evidence={"type": "operation_verified", "operation_id": 12345})
    done = store.goals.set_task_status(task["id"], "done", actor="user")
    assert '"user_confirmation"' in done["completion_evidence_json"]


def test_blocked_and_inferred_goals(store):
    task = store.add_task("order parts")
    blocked = store.goals.set_task_status(task["id"], "blocked", actor="user", blocker="supplier out of stock")
    assert blocked["blocker"] == "supplier out of stock"
    inferred = store.goals.create_goal("clean up old branches", origin="inferred")
    assert inferred["status"] == "proposed" and inferred["authorised"] == 0
    assert store.goals.authorise_goal(inferred["id"])["status"] == "active"
