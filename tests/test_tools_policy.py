"""Policy decisions and capability-registry behaviour."""
from __future__ import annotations

import pytest

from jarvis.contracts import ActionRequest, OperationStatus, Origin
from jarvis.db import Store
from jarvis.security import AutonomyLevel, Policy
from jarvis.tools import Tool, ToolError, ToolRegistry


@pytest.fixture
def registry(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("pump draws 2A", encoding="utf-8")
    (workspace / ".env").write_text("JARVIS_GEMINI_API_KEY=AIza" + "SyA1234567890abcdefghijklmnopqrstuv", encoding="utf-8")
    (workspace / "blob.bin").write_bytes(b"\x00\x01\x02")
    return ToolRegistry(Store(tmp_path / "jarvis.sqlite3"), workspace, Policy())


def test_policy_matrix():
    p = Policy()
    assert p.decide("t", "read", origin="model_proposal").allowed
    write = p.decide("t", "write", origin="model_proposal")
    assert not write.allowed and write.needs_approval
    assert p.decide("t", "write", origin="user_command").allowed
    irreversible = p.decide("t", "write", origin="user_command", requires_approval=True)
    assert not irreversible.allowed and irreversible.needs_approval
    assert p.decide("t", "write", origin="approval", approved=True).allowed
    assert not p.decide("t", "write", origin="approval", approved=False).allowed
    assert not p.decide("t", "dangerous", origin="user_command").allowed
    assert not p.decide("t", "network", origin="user_command").allowed
    assert Policy(network_enabled=True).decide("t", "network", origin="user_command").allowed
    assert not Policy(allow_writes=False).decide("t", "write", origin="user_command").allowed
    observe = Policy(autonomy_level=AutonomyLevel.OBSERVE).decide("t", "write", origin="model_proposal")
    assert not observe.allowed and not observe.needs_approval
    # v0.2 call style still works
    assert not p.decide("t", "write").allowed and p.decide("t", "write", explicitly_requested=True).allowed
    with pytest.raises(ValueError):
        Policy(autonomy_level=4)


def test_policy_signature_has_no_affect_or_model_text_inputs():
    import inspect
    params = set(inspect.signature(Policy.decide).parameters)
    assert params == {"self", "tool_name", "risk", "explicitly_requested", "origin", "requires_approval", "approved"}


def test_schema_validation_and_unknown_tools(registry):
    bad = registry.run(ActionRequest("math.calculate", {"expression": 5}, Origin.USER_COMMAND))
    assert bad.status == OperationStatus.DENIED and "must be str" in bad.reason
    extra = registry.run(ActionRequest("time.now", {"x": 1}, Origin.USER_COMMAND))
    assert extra.status == OperationStatus.DENIED
    flag = registry.run(ActionRequest("task.add", {"title": "x", "goal_id": True}, Origin.USER_COMMAND))
    assert flag.status == OperationStatus.DENIED and "integer" in flag.reason


def test_idempotency_replay_and_mismatch(registry):
    calls = []
    registry.register(Tool("test.count", "count", "write", lambda a: calls.append(a) or {"n": len(calls)},
                           {"x": int}))
    first = registry.run(ActionRequest("test.count", {"x": 1}, Origin.USER_COMMAND, idempotency_key="k1"))
    second = registry.run(ActionRequest("test.count", {"x": 1}, Origin.USER_COMMAND, idempotency_key="k1"))
    assert first.status == second.status == OperationStatus.SUCCEEDED and second.replayed
    assert second.result == {"n": 1} and len(calls) == 1
    mismatch = registry.run(ActionRequest("test.count", {"x": 2}, Origin.USER_COMMAND, idempotency_key="k1"))
    assert mismatch.status == OperationStatus.DENIED and len(calls) == 1


def test_propose_approve_once_and_deny(registry):
    proposal = registry.propose(ActionRequest("task.add", {"title": "buy relays", "goal_id": 0}, Origin.MODEL_PROPOSAL))
    assert proposal.status == OperationStatus.PROPOSED and registry.store.list_tasks() == []
    approved = registry.approve(proposal.operation_id)
    assert approved.ok and registry.store.list_tasks()[0]["title"] == "buy relays"
    again = registry.approve(proposal.operation_id)
    assert again.replayed and len(registry.store.list_tasks()) == 1
    other = registry.propose(ActionRequest("task.add", {"title": "x", "goal_id": 0}, Origin.MODEL_PROPOSAL))
    assert registry.deny(other.operation_id).status == OperationStatus.CANCELLED
    assert registry.approve(other.operation_id).status == OperationStatus.CANCELLED
    op = registry.store.operations.get(proposal.operation_id)
    assert op["approved_by"] == "user" and op["arguments"]["title"] == "buy relays"


def test_journal_keeps_only_redacted_arguments_after_completion(registry):
    outcome = registry.run(ActionRequest("memory.remember", {"value": "my bank PIN hint is the dog's name",
                                                             "key": "", "category": "personal", "kind": "note",
                                                             "sensitivity": "personal", "correction": False},
                                         Origin.USER_COMMAND))
    assert outcome.ok
    stored = registry.store.operations.get(outcome.operation_id)["arguments"]
    assert stored["value"] == "[REDACTED]"


def test_credential_arguments_are_rejected(registry):
    outcome = registry.run(ActionRequest("memory.save", {"key": "k", "value": "token: ghp_" + "a" * 36,
                                                         "category": "x"}, Origin.USER_COMMAND))
    assert outcome.status == OperationStatus.DENIED and "credential" in outcome.reason


def test_workspace_read_refuses_credentials_binary_and_escape(registry):
    ok = registry.run(ActionRequest("workspace.read", {"relative_path": "notes.txt"}, Origin.MODEL_PROPOSAL))
    assert ok.ok and ok.result["text"] == "pump draws 2A"
    for path, message in ((".env", "credentials"), ("blob.bin", "Binary"), ("../outside.txt", "escapes")):
        outcome = registry.run(ActionRequest("workspace.read", {"relative_path": path}, Origin.MODEL_PROPOSAL))
        assert outcome.status in {OperationStatus.FAILED, OperationStatus.DENIED}
        assert message.lower() in (outcome.error_message or outcome.reason).lower()


def test_legacy_execute_raises_tool_error(registry):
    with pytest.raises(ToolError):
        registry.execute("memory.save", {"key": "x", "value": "y", "category": "t"})
    assert registry.execute("math.calculate", {"expression": "2+2"}) == {"result": 4}


def test_calculator_verification_is_independent(registry):
    outcome = registry.run(ActionRequest("math.calculate", {"expression": "(10-4)/2"}, Origin.USER_COMMAND))
    assert outcome.verified is True and outcome.result == {"result": 3.0}


def test_recovery_redacts_arguments_of_interrupted_operations(tmp_path):
    from datetime import timedelta
    from jarvis.util import utc_now
    clock = {"now": utc_now()}
    store = Store(tmp_path / "jarvis.sqlite3", clock=lambda: clock["now"])
    op_id = store.operations.create(capability="memory.remember", arguments={"value": "private detail", "key": "k"},
                                    redacted_arguments={"value": "[REDACTED]", "key": "k"}, args_digest="d",
                                    origin="user_command", risk="write", status="started", reason="", timeout_s=5,
                                    correlation_id=None, session_id=None, idempotency_key=None, runtime_instance="old")
    assert store.operations.get(op_id)["arguments"]["value"] == "private detail"
    clock["now"] += timedelta(minutes=5)
    recovered = store.operations.recover_interrupted(current_runtime="new")
    assert [r["id"] for r in recovered] == [op_id]
    assert store.operations.get(op_id)["arguments"] == {"value": "[REDACTED]", "key": "k"}


# ---------------------------------------------------------------- pre-release review (second pass)
def test_deny_racing_an_approval_never_reports_an_executed_operation_as_cancelled(registry, monkeypatch):
    """Review: deny() read 'proposed', a concurrent approval then executed the operation, and
    deny's unconditional journal update turned it into 'cancelled' — the journal and the denying
    user were told an executed action never happened. Forced, deterministic interleaving."""
    calls: list[int] = []
    registry.register(Tool("test.effect", "visible side effect", "write", lambda a: calls.append(1) or {"ok": True}, {}))
    op = registry.run(ActionRequest("test.effect", {}, Origin.MODEL_PROPOSAL)).operation_id
    journal, box = registry.store.operations, {}
    original_get = journal.get

    def stale_read_then_concurrent_approval(op_id):
        record = original_get(op_id)
        if not box:
            box["started"] = True
            box["approval"] = registry.approve(op_id)
        return record
    monkeypatch.setattr(journal, "get", stale_read_then_concurrent_approval)
    denied = registry.deny(op)
    monkeypatch.undo()
    assert len(calls) == 1 and box["approval"].status == OperationStatus.SUCCEEDED
    assert denied.status == OperationStatus.SUCCEEDED and "nothing to deny" in denied.reason
    assert journal.get(op)["status"] == "succeeded"
    kinds = [e["kind"] for e in registry.store.events.recent(50) if e["operation_id"] == op]
    assert "operation.cancelled" not in kinds


def test_concurrent_approvals_execute_once_and_every_caller_gets_an_outcome(registry):
    """Review: the losing approver received a raw ValueError (a traceback through the CLI)."""
    import threading
    import time

    calls: list[int] = []
    registry.register(Tool("test.slow_effect", "slow side effect", "write",
                           lambda a: (time.sleep(0.2), calls.append(1), {"ok": True})[-1], {}, timeout_s=5))
    op = registry.run(ActionRequest("test.slow_effect", {}, Origin.MODEL_PROPOSAL)).operation_id
    barrier, results = threading.Barrier(3), []

    def approver():
        barrier.wait()
        try:
            results.append(registry.approve(op))
        except Exception as exc:  # noqa: BLE001 - recorded so the assertion can show it
            results.append(exc)
    threads = [threading.Thread(target=approver) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert not [r for r in results if isinstance(r, Exception)], results
    assert sorted(bool(r.replayed) for r in results) == [False, True, True]
    assert all("not executed again" in r.reason for r in results if r.replayed)
