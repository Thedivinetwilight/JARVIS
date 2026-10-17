"""Validated deterministic capabilities; deliberately no arbitrary shell execution.

Capability contract (every registered tool):
- ``name``, ``description``, ``risk`` (read | write | network | dangerous)
- ``schema``: exact argument names and types (extra or missing arguments are rejected)
- ``idempotent``: safe to retry/replay; ``reversible``; ``requires_approval`` (irreversible)
- ``timeout_s``: hard deadline. Handlers run in a daemon thread; on timeout the
  outcome is reported as *unknown* (Python cannot kill a thread — documented limit)
- ``verify``: optional postcondition check run after execution; its result is the
  difference between "the handler returned" and "the effect was verified"

Every invocation goes through :meth:`ToolRegistry.run`, which journals the request
(operations table) *before* execution and returns an :class:`ActionOutcome` that
separates requested / attempted / executed / observed / verified.

Journal privacy: the journal records *what was done*, not private content. Arguments
listed in ``sensitive_args`` are redacted once an operation finishes, and tools whose
results carry private content (memories, file text) declare ``journal_result`` so only
a content-free summary (ids, counts, status) is stored. The caller still receives the
full live result; an idempotent replay returns the stored summary.
"""
from __future__ import annotations

import ast
import inspect
import operator
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .contracts import ActionOutcome, ActionRequest, OperationStatus, Origin
from .db import Store
from .redact import contains_secret, redact_obj, redact_text
from .security import Policy, Risk
from .util import bounded, digest

MAX_READ_BYTES = 64 * 1024
# File names that must never be read through workspace tools (credentials/secrets).
_DENIED_NAMES = (".env", ".netrc", ".git-credentials", ".pgpass", ".npmrc", ".pypirc", "id_rsa", "id_dsa",
                 "id_ecdsa", "id_ed25519")
_DENIED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".sqlite3", ".db")


@dataclass(frozen=True)
class ToolContext:
    origin: str
    correlation_id: str | None = None
    session_id: str | None = None
    source_event_id: int | None = None
    operation_id: int | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    risk: Risk
    handler: Callable[..., Any]
    schema: dict[str, type]
    idempotent: bool | None = None          # None -> True for reads, False otherwise
    reversible: bool = True
    requires_approval: bool = False
    timeout_s: float = 10.0
    verify: Callable[[dict[str, Any], Any], tuple[bool, str]] | None = None
    sensitive_args: tuple[str, ...] = ()
    journal_result: Callable[[Any], Any] | None = None   # content-free summary stored in the journal
    _accepts_context: bool = field(default=False, compare=False, repr=False)

    @property
    def is_idempotent(self) -> bool:
        return self.risk == "read" if self.idempotent is None else self.idempotent


class ToolError(RuntimeError):
    pass


class UnknownOperation(ToolError):
    """Raised by approve/deny for an operation id that does not exist."""


class HandlerTimeout(Exception):
    pass


def _run_with_timeout(fn: Callable[[], Any], timeout: float) -> Any:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in caller thread
            box["error"] = exc

    worker = threading.Thread(target=target, name="jarvis-tool", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise HandlerTimeout()
    if "error" in box:
        raise box["error"]
    return box.get("result")


class ToolRegistry:
    def __init__(self, store: Store, workspace: Path, policy: Policy, *, runtime_id: str | None = None):
        self.store, self.workspace, self.policy = store, workspace.resolve(), policy
        self.runtime_id = runtime_id
        self._tools: dict[str, Tool] = {}
        self._register_defaults()

    # ------------------------------------------------------------ registration
    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        if tool.timeout_s <= 0 or tool.timeout_s > 300:
            raise ValueError("Tool timeout must be within (0, 300] seconds")
        try:
            params = [p for p in inspect.signature(tool.handler).parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            accepts_ctx = len(params) >= 2
        except (TypeError, ValueError):
            accepts_ctx = False
        object.__setattr__(tool, "_accepts_context", accepts_ctx)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def descriptions(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "risk": t.risk,
                 "arguments": {k: v.__name__ for k, v in t.schema.items()},
                 "idempotent": t.is_idempotent, "reversible": t.reversible,
                 "requires_approval": t.requires_approval, "timeout_s": t.timeout_s,
                 "verified_by_postcondition": t.verify is not None} for t in self._tools.values()]

    # ------------------------------------------------------------ validation
    def _validate(self, tool: Tool, arguments: Any) -> None:
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be an object")
        expected = set(tool.schema)
        if set(arguments) != expected:
            raise ToolError(f"Arguments must be exactly: {sorted(expected)}")
        for key, typ in tool.schema.items():
            # bool is a subclass of int; reject it where a number is expected.
            if typ is int and (not isinstance(arguments[key], int) or isinstance(arguments[key], bool)):
                raise ToolError(f"{key} must be an integer")
            if typ is not int and not isinstance(arguments[key], typ):
                raise ToolError(f"{key} must be {typ.__name__}")
            if isinstance(arguments[key], str) and len(arguments[key]) > 8000:
                raise ToolError(f"{key} is too long")

    def _redacted_args(self, tool: Tool | None, arguments: dict[str, Any]) -> dict[str, Any]:
        sensitive = set(tool.sensitive_args) if tool else set()
        return _safe_args(arguments, extra_sensitive=sensitive)

    # ------------------------------------------------------------ execution
    def run(self, request: ActionRequest, *, correlation_id: str | None = None, session_id: str | None = None,
            source_event_id: int | None = None) -> ActionOutcome:
        """Authorise, journal, execute, observe and verify one capability request.

        Never raises for expected failures; the outcome describes what happened.
        """
        origin = Origin(request.origin).value
        tool = self._tools.get(request.capability)
        if tool is None:
            self.store.events.append("capability.rejected", actor=origin, target=bounded(request.capability, 80),
                                     decision="denied", session_id=session_id, cycle_id=correlation_id,
                                     payload={"reason": "unknown capability"})
            return ActionOutcome(request.capability, OperationStatus.DENIED, f"Unknown tool: {request.capability}")
        try:
            self._validate(tool, request.arguments)
        except ToolError as exc:
            self.store.events.append("capability.rejected", actor=origin, target=tool.name, decision="denied",
                                     session_id=session_id, cycle_id=correlation_id, payload={"reason": str(exc)})
            return ActionOutcome(tool.name, OperationStatus.DENIED, str(exc))
        arguments = dict(request.arguments)
        redacted = self._redacted_args(tool, arguments)
        args_digest = digest({"capability": tool.name, "arguments": arguments})
        if any(isinstance(v, str) and contains_secret(v) for v in arguments.values()):
            op_id = self.store.operations.create(
                capability=tool.name, arguments=redacted, redacted_arguments=redacted, args_digest=args_digest,
                origin=origin, risk=tool.risk, status="denied",
                reason="Arguments contain credential-like content", timeout_s=tool.timeout_s,
                correlation_id=correlation_id, session_id=session_id, idempotency_key=None,
                runtime_instance=self.runtime_id)
            return ActionOutcome(tool.name, OperationStatus.DENIED, "Arguments contain credential-like content",
                                 operation_id=op_id)

        if request.idempotency_key:
            previous = self.store.operations.by_idempotency_key(request.idempotency_key)
            if previous is not None:
                if previous["args_digest"] != args_digest:
                    return ActionOutcome(tool.name, OperationStatus.DENIED,
                                         "Idempotency key was already used with different arguments",
                                         operation_id=previous["id"])
                return self._outcome_from_record(previous, replayed=True)

        decision = self.policy.decide(tool.name, tool.risk, origin=origin, requires_approval=tool.requires_approval)
        if not decision.allowed:
            status = "proposed" if decision.needs_approval else "denied"
            created = self._journal_or_replay(
                capability=tool.name, arguments=arguments, redacted_arguments=redacted,
                args_digest=args_digest, origin=origin, risk=tool.risk, status=status, reason=decision.reason,
                timeout_s=tool.timeout_s, correlation_id=correlation_id, session_id=session_id,
                idempotency_key=request.idempotency_key, runtime_instance=self.runtime_id)
            if isinstance(created, ActionOutcome):
                return created
            return ActionOutcome(tool.name, OperationStatus(status), decision.reason, operation_id=created)

        created = self._journal_or_replay(
            capability=tool.name, arguments=arguments, redacted_arguments=redacted,
            args_digest=args_digest, origin=origin, risk=tool.risk, status="started", reason=decision.reason,
            timeout_s=tool.timeout_s, correlation_id=correlation_id, session_id=session_id,
            idempotency_key=request.idempotency_key, runtime_instance=self.runtime_id)
        if isinstance(created, ActionOutcome):
            return created
        ctx = ToolContext(origin=origin, correlation_id=correlation_id, session_id=session_id,
                          source_event_id=source_event_id, operation_id=created)
        return self._execute(tool, arguments, redacted, created, ctx, decision.reason)

    def _journal_or_replay(self, **create: Any) -> int | ActionOutcome:
        """Write the journal row. If a concurrent caller committed the same idempotency key first
        (check-then-insert race), return that caller's recorded outcome instead of raising; the
        UNIQUE constraint guarantees the operation itself is never executed twice."""
        try:
            return self.store.operations.create(**create)
        except sqlite3.IntegrityError:
            key = create.get("idempotency_key")
            previous = self.store.operations.by_idempotency_key(key) if key else None
            if previous is None:
                raise
            if previous["args_digest"] != create["args_digest"]:
                return ActionOutcome(create["capability"], OperationStatus.DENIED,
                                     "Idempotency key was already used with different arguments",
                                     operation_id=previous["id"])
            return self._outcome_from_record(previous, replayed=True)

    def propose(self, request: ActionRequest, *, correlation_id: str | None = None, session_id: str | None = None,
                reason: str = "Recorded as a proposal; it runs only after user approval") -> ActionOutcome:
        """Journal a request as ``proposed`` without executing it (unless policy forbids it outright).

        Used for model-suggested operations: even read-only suggestions are not
        auto-executed by default (v0.2 safety rule preserved).
        """
        origin = Origin(request.origin).value
        tool = self._tools.get(request.capability)
        if tool is None:
            self.store.events.append("capability.rejected", actor=origin, target=bounded(request.capability, 80),
                                     decision="denied", session_id=session_id, cycle_id=correlation_id,
                                     payload={"reason": "unknown capability"})
            return ActionOutcome(request.capability, OperationStatus.DENIED, f"Unknown tool: {request.capability}")
        try:
            self._validate(tool, request.arguments)
        except ToolError as exc:
            self.store.events.append("capability.rejected", actor=origin, target=tool.name, decision="denied",
                                     session_id=session_id, cycle_id=correlation_id, payload={"reason": str(exc)})
            return ActionOutcome(tool.name, OperationStatus.DENIED, str(exc))
        arguments = dict(request.arguments)
        redacted = self._redacted_args(tool, arguments)
        args_digest = digest({"capability": tool.name, "arguments": arguments})
        decision = self.policy.decide(tool.name, tool.risk, origin=origin, requires_approval=tool.requires_approval)
        secret = any(isinstance(v, str) and contains_secret(v) for v in arguments.values())
        if secret or (not decision.allowed and not decision.needs_approval):
            why = "Arguments contain credential-like content" if secret else decision.reason
            op_id = self.store.operations.create(
                capability=tool.name, arguments=redacted, redacted_arguments=redacted, args_digest=args_digest,
                origin=origin, risk=tool.risk, status="denied", reason=why, timeout_s=tool.timeout_s,
                correlation_id=correlation_id, session_id=session_id, idempotency_key=None,
                runtime_instance=self.runtime_id)
            return ActionOutcome(tool.name, OperationStatus.DENIED, why, operation_id=op_id)
        why = decision.reason if decision.needs_approval else reason
        created = self._journal_or_replay(
            capability=tool.name, arguments=arguments, redacted_arguments=redacted,
            args_digest=args_digest, origin=origin, risk=tool.risk, status="proposed", reason=why,
            timeout_s=tool.timeout_s, correlation_id=correlation_id, session_id=session_id,
            idempotency_key=request.idempotency_key, runtime_instance=self.runtime_id)
        if isinstance(created, ActionOutcome):
            return created
        return ActionOutcome(tool.name, OperationStatus.PROPOSED, why, operation_id=created)

    def _execute(self, tool: Tool, arguments: dict[str, Any], redacted: dict[str, Any], op_id: int,
                 ctx: ToolContext, reason: str) -> ActionOutcome:
        outcome = ActionOutcome(tool.name, OperationStatus.STARTED, reason, operation_id=op_id, attempted=True)
        call = (lambda: tool.handler(arguments, ctx)) if tool._accepts_context else (lambda: tool.handler(arguments))
        try:
            result = _run_with_timeout(call, tool.timeout_s)
        except HandlerTimeout:
            outcome.status, outcome.executed = OperationStatus.TIMED_OUT, None
            outcome.error_type, outcome.error_message = "Timeout", f"No result within {tool.timeout_s:g}s; outcome unknown"
            outcome.observed = True
            self.store.operations.finish(op_id, status="timed_out", error_type="Timeout",
                                         error_message=outcome.error_message, redacted_arguments=redacted)
            return outcome
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            outcome.status, outcome.executed, outcome.observed = OperationStatus.FAILED, False, True
            outcome.error_type, outcome.error_message = type(exc).__name__, redact_text(str(exc))[:500]
            self.store.operations.finish(op_id, status="failed", error_type=outcome.error_type,
                                         error_message=outcome.error_message, redacted_arguments=redacted)
            return outcome
        outcome.status, outcome.executed, outcome.observed = OperationStatus.SUCCEEDED, True, True
        outcome.result = result
        if tool.verify is not None:
            try:
                ok, note = tool.verify(arguments, result)
            except Exception as exc:  # noqa: BLE001
                ok, note = False, f"verification raised {type(exc).__name__}"
            outcome.verified, outcome.verification_note = bool(ok), note
        else:
            outcome.verification_note = "no postcondition defined for this capability"
        try:
            stored = tool.journal_result(result) if tool.journal_result is not None else result
        except Exception:  # noqa: BLE001 - never fall back to storing raw content
            stored = {"journal": "result summary unavailable"}
        self.store.operations.finish(op_id, status="succeeded", result=redact_obj(_jsonable(stored)),
                                     verified=outcome.verified, verification_note=outcome.verification_note,
                                     redacted_arguments=redacted)
        return outcome

    @staticmethod
    def _outcome_from_record(record: dict[str, Any], *, replayed: bool = False) -> ActionOutcome:
        status = OperationStatus(record["status"])
        attempted = record["started_at"] is not None
        executed: bool | None = {"succeeded": True, "failed": False}.get(record["status"])
        if status in {OperationStatus.PROPOSED, OperationStatus.DENIED, OperationStatus.CANCELLED}:
            executed = False
        verified = None if record["verified"] is None else bool(record["verified"])
        return ActionOutcome(record["capability"], status, record["decision_reason"], operation_id=record["id"],
                             attempted=attempted, executed=executed,
                             observed=status in {OperationStatus.SUCCEEDED, OperationStatus.FAILED,
                                                 OperationStatus.TIMED_OUT},
                             verified=verified, verification_note=record["verification_note"] or "",
                             result=record["result"], error_type=record["error_type"],
                             error_message=record["error_message"], replayed=replayed)

    def approve(self, op_id: int, *, approved_by: str = "user") -> ActionOutcome:
        """Execute a proposed operation after explicit user approval (exactly once)."""
        record = self.store.operations.get(op_id)
        if record is None:
            raise UnknownOperation(f"Unknown operation {op_id}")
        if record["status"] != "proposed":
            outcome = self._outcome_from_record(record, replayed=True)
            outcome.reason = f"Operation {op_id} is {record['status']}; it was not executed again"
            return outcome
        tool = self._tools.get(record["capability"])
        if tool is None:
            if not self.store.operations.finish(op_id, status="cancelled", error_type="UnknownCapability",
                                                error_message="Capability is no longer registered",
                                                expect_status="proposed"):
                return self._already_decided(op_id, "it was not executed again")
            return ActionOutcome(record["capability"], OperationStatus.CANCELLED, "Capability is no longer registered",
                                 operation_id=op_id)
        arguments = record["arguments"]
        try:
            self._validate(tool, arguments)
        except ToolError as exc:
            if not self.store.operations.finish(op_id, status="cancelled", error_type="InvalidArguments",
                                                error_message=str(exc), expect_status="proposed"):
                return self._already_decided(op_id, "it was not executed again")
            return ActionOutcome(tool.name, OperationStatus.CANCELLED, str(exc), operation_id=op_id)
        decision = self.policy.decide(tool.name, tool.risk, origin=Origin.APPROVAL.value, approved=True)
        if not decision.allowed:
            if not self.store.operations.finish(op_id, status="denied", error_type="PolicyDenied",
                                                error_message=decision.reason, expect_status="proposed"):
                return self._already_decided(op_id, "it was not executed again")
            return ActionOutcome(tool.name, OperationStatus.DENIED, decision.reason, operation_id=op_id)
        try:
            self.store.operations.start(op_id, approved_by=approved_by, runtime_instance=self.runtime_id)
        except ValueError:      # a concurrent approval or denial won the conditional transition
            return self._already_decided(op_id, "it was not executed again")
        ctx = ToolContext(origin=record["origin"], correlation_id=record["correlation_id"],
                          session_id=record["session_id"], operation_id=op_id)
        return self._execute(tool, arguments, self._redacted_args(tool, arguments), op_id, ctx, decision.reason)

    def deny(self, op_id: int, *, status: str = "cancelled", reason: str = "Denied by the user") -> ActionOutcome:
        record = self.store.operations.get(op_id)
        if record is None:
            raise UnknownOperation(f"Unknown operation {op_id}")
        if record["status"] != "proposed":
            outcome = self._outcome_from_record(record)
            outcome.reason = f"Operation {op_id} is {record['status']}; nothing to deny"
            return outcome
        tool = self._tools.get(record["capability"])
        if not self.store.operations.finish(op_id, status=status, error_type="UserDenied", error_message=reason,
                                            redacted_arguments=self._redacted_args(tool, record["arguments"]),
                                            expect_status="proposed"):
            return self._already_decided(op_id, "nothing to deny")   # approved/denied concurrently: report the truth
        return ActionOutcome(record["capability"], OperationStatus(status), reason, operation_id=op_id)

    def _already_decided(self, op_id: int, consequence: str) -> ActionOutcome:
        """Outcome for a caller that lost a race to another approval/denial of the same operation."""
        record = self.store.operations.get(op_id)
        outcome = self._outcome_from_record(record, replayed=True)
        outcome.reason = f"Operation {op_id} is {record['status']}; {consequence}"
        return outcome

    def execute(self, name: str, arguments: dict[str, Any], *, explicitly_requested: bool = False,
                actor: str = "user", origin: str | None = None, correlation_id: str | None = None,
                session_id: str | None = None, idempotency_key: str | None = None) -> Any:
        """v0.2-compatible call: returns the handler result or raises ToolError."""
        if origin is None:
            if actor == "model":
                origin = Origin.MODEL_PROPOSAL.value
            elif explicitly_requested:
                origin = Origin.USER_COMMAND.value
            else:
                origin = "unspecified"
        if origin == "unspecified":
            tool = self._tools.get(name)
            if tool is None:
                raise ToolError(f"Unknown tool: {name}")
            self._validate(tool, arguments)
            decision = self.policy.decide(name, tool.risk, explicitly_requested=False,
                                          requires_approval=tool.requires_approval)
            if not decision.allowed:
                self.store.events.append(f"tool.{name}", actor=actor, target=name, decision="denied",
                                         payload={"risk": tool.risk, "reason": decision.reason,
                                                  "arguments": self._redacted_args(tool, arguments)})
                raise ToolError(decision.reason)
            origin = Origin.KERNEL.value if tool.risk == "read" else Origin.USER_COMMAND.value
        outcome = self.run(ActionRequest(name, arguments, Origin(origin), idempotency_key=idempotency_key),
                           correlation_id=correlation_id, session_id=session_id)
        if outcome.status == OperationStatus.SUCCEEDED:
            return outcome.result
        raise ToolError(outcome.error_message or outcome.reason)

    # ------------------------------------------------------------ defaults
    def _register_defaults(self) -> None:
        store = self.store
        self.register(Tool("time.now", "Return current UTC time.", "read",
                           lambda _: {"utc": datetime.now(timezone.utc).isoformat()}, {},
                           verify=lambda a, r: (isinstance(r, dict) and "utc" in r, "result has utc timestamp")))
        self.register(Tool("math.calculate", "Evaluate basic arithmetic expressions safely.", "read",
                           lambda a: {"result": safe_calculate(a["expression"])}, {"expression": str},
                           verify=_verify_calculation))
        self.register(Tool("project.list", "List tracked projects.", "read", lambda _: store.list_projects(), {}))
        self.register(Tool("task.list", "List tasks, optionally by status.", "read",
                           lambda a: store.list_tasks(status=a["status"] or None), {"status": str}))
        self.register(Tool("goal.list", "List active and proposed goals with progress.", "read",
                           lambda _: [store.goals.progress(g["id"]) for g in store.goals.list_goals()], {}))
        self.register(Tool("memory.search", "Search persistent memory.", "read",
                           lambda a: store.search_memories(a["query"]), {"query": str},
                           sensitive_args=("query",), journal_result=_hits_summary))
        self.register(Tool("workspace.list", "List files beneath the configured workspace.", "read",
                           self._list_files, {"relative_path": str}))
        self.register(Tool("workspace.read", "Read a UTF-8 text file (max 64 KiB) inside the workspace; "
                           "credential files are refused.", "read", self._read_file, {"relative_path": str},
                           journal_result=_file_read_summary))
        self.register(Tool("task.create", "Create a task after explicit user request.", "write",
                           lambda a: store.add_task(a["title"], project=a["project"] or None,
                                                    description=a["description"], priority=a["priority"]),
                           {"title": str, "project": str, "description": str, "priority": int},
                           verify=lambda a, r: _verify_row(store, "tasks", r, title=a["title"])))
        self.register(Tool("memory.save", "Save a memory after explicit user request (v0.2 interface).", "write",
                           lambda a: store.remember(a["key"], a["value"], category=a["category"], source="tool"),
                           {"key": str, "value": str, "category": str},
                           verify=lambda a, r: _verify_memory(store, r), sensitive_args=("value", "key"),
                           journal_result=_item_ref))
        self.register(Tool("memory.remember", "Store a fact, preference, idea or note with provenance.", "write",
                           self._remember, {"value": str, "key": str, "category": str, "kind": str,
                                            "sensitivity": str, "correction": bool},
                           verify=lambda a, r: _verify_memory(store, r["item"]), sensitive_args=("value", "key"),
                           journal_result=_memory_write_summary))
        self.register(Tool("memory.correct", "Supersede a memory with a corrected value (history kept).", "write",
                           self._correct, {"memory_id": int, "value": str, "reason": str},
                           verify=lambda a, r: _verify_correction(store, a["memory_id"], r),
                           sensitive_args=("value", "reason"), journal_result=_memory_write_summary))
        self.register(Tool("memory.forget", "Irreversibly erase a memory, its versions and its source text.",
                           "write", self._forget, {"memory_id": int}, reversible=False, requires_approval=True,
                           idempotent=True, verify=lambda a, r: _verify_forgotten(store, r),
                           # A rare, approved operation whose sweep grows with the history: allow time so a
                           # large database is not misreported as "timed out, outcome unknown".
                           timeout_s=120))
        self.register(Tool("goal.create", "Create a user-authorised goal with optional ordered steps.", "write",
                           self._create_goal, {"title": str, "steps": list, "project": str},
                           verify=lambda a, r: _verify_row(store, "goals", r, title=a["title"])))
        self.register(Tool("task.add", "Add a task, optionally to a goal (goal_id 0 = none).", "write",
                           self._add_task, {"title": str, "goal_id": int},
                           verify=lambda a, r: _verify_row(store, "tasks", r, title=a["title"])))
        self.register(Tool("task.set_status", "Change a task's status (completion needs user confirmation).",
                           "write", self._set_task_status, {"task_id": int, "status": str, "note": str},
                           verify=lambda a, r: _verify_row(store, "tasks", r, status=a["status"])))

    # ------------------------------------------------------------ handlers
    @staticmethod
    def _source_for(ctx: ToolContext) -> str:
        return {"user_command": "user", "model_proposal": "model", "kernel": "system"}.get(ctx.origin, "system")

    def _remember(self, a: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        result = self.store.memory.remember(
            a["value"], key=a["key"] or None, category=a["category"] or "general", kind=a["kind"] or "fact",
            sensitivity=a["sensitivity"] or "normal", source=self._source_for(ctx), correction=a["correction"],
            source_event_id=ctx.source_event_id, session_id=ctx.session_id, cycle_id=ctx.correlation_id)
        return {"action": result.action, "item": result.item, "previous_id": result.previous_id,
                "conflict_id": result.conflict_id, "conflicting_ids": list(result.conflicting_ids)}

    def _correct(self, a: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        result = self.store.memory.correct(a["memory_id"], a["value"], reason=a["reason"],
                                           source=self._source_for(ctx), source_event_id=ctx.source_event_id,
                                           session_id=ctx.session_id, cycle_id=ctx.correlation_id)
        return {"action": result.action, "item": result.item, "previous_id": result.previous_id}

    def _forget(self, a: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        item = self.store.memory.get(a["memory_id"])
        if item is None:
            raise ToolError(f"Unknown memory id: {a['memory_id']}")
        if item["status"] == "forgotten":
            return {"forgotten_ids": [item["id"]], "already_forgotten": True}
        return self.store.memory.forget(a["memory_id"], session_id=ctx.session_id, cycle_id=ctx.correlation_id)

    def _create_goal(self, a: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        steps = a["steps"]
        if not all(isinstance(s, str) for s in steps):
            raise ToolError("steps must be a list of strings")
        return self.store.goals.create_goal(a["title"], steps=steps, project=a["project"] or None,
                                            origin="user" if ctx.origin == "user_command" else "inferred",
                                            source_event_id=ctx.source_event_id, session_id=ctx.session_id,
                                            cycle_id=ctx.correlation_id)

    def _add_task(self, a: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return self.store.goals.add_task(a["title"], goal_id=a["goal_id"] or None, session_id=ctx.session_id,
                                         cycle_id=ctx.correlation_id, source_event_id=ctx.source_event_id)

    def _set_task_status(self, a: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        actor = "user" if ctx.origin == "user_command" else "jarvis"
        evidence = {"type": "user_confirmation", "event_id": ctx.source_event_id} if actor == "user" else None
        return self.store.goals.set_task_status(
            a["task_id"], a["status"], actor=actor, evidence=evidence, reason=a["note"],
            blocker=a["note"] if a["status"] == "blocked" else None, session_id=ctx.session_id,
            cycle_id=ctx.correlation_id, source_event_id=ctx.source_event_id)

    def _resolve_in_workspace(self, relative_path: str) -> Path:
        base = (self.workspace / relative_path).resolve()
        if not base.is_relative_to(self.workspace):
            raise ToolError("Path escapes the configured workspace")
        if not base.exists():
            raise ToolError("Path does not exist")
        return base

    def _list_files(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        base = self._resolve_in_workspace(args["relative_path"])
        if not base.is_dir():
            raise ToolError("Path must be a directory")
        results = []
        for path in sorted(base.iterdir(), key=lambda p: p.name.lower())[:200]:
            results.append({"name": path.name, "type": "directory" if path.is_dir() else "file"})
        return results

    def _read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_in_workspace(args["relative_path"])
        if not path.is_file():
            raise ToolError("Path must be a file")
        lowered = path.name.lower()
        rel_parts = {p.lower() for p in path.relative_to(self.workspace).parts}
        if (lowered in _DENIED_NAMES or lowered.startswith(".env") or lowered.endswith(_DENIED_SUFFIXES)
                or "credential" in lowered or "secret" in lowered or ".git" in rel_parts or ".ssh" in rel_parts):
            raise ToolError("Refusing to read a file that may contain credentials")
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            raise ToolError(f"File is larger than {MAX_READ_BYTES} bytes")
        data = path.read_bytes()
        if b"\x00" in data:
            raise ToolError("Binary files are not supported")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError("File is not valid UTF-8 text") from None
        return {"path": str(path.relative_to(self.workspace)), "bytes": size, "text": redact_text(text)}


# ---------------------------------------------------------------- verification
def _item_ref(item: Any) -> Any:
    """Identity and status of a memory item without its content (no key, no value)."""
    if not isinstance(item, dict):
        return None
    return {k: item.get(k) for k in ("id", "kind", "category", "status", "epistemic", "source") if k in item}


def _memory_write_summary(result: Any) -> Any:
    if not isinstance(result, dict):
        return None
    summary = {k: result[k] for k in ("action", "previous_id", "conflict_id", "conflicting_ids") if k in result}
    summary["item"] = _item_ref(result.get("item"))
    return summary


def _hits_summary(result: Any) -> Any:
    rows = result if isinstance(result, list) else []
    return {"count": len(rows), "hits": [{"id": h.get("id"), "score": h.get("score")}
                                         for h in rows if isinstance(h, dict)]}


def _file_read_summary(result: Any) -> Any:
    if not isinstance(result, dict):
        return None
    return {"path": result.get("path"), "bytes": result.get("bytes"), "content_journaled": False}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _verify_row(store: Store, table: str, result: Any, **expected: Any) -> tuple[bool, str]:
    if not isinstance(result, dict) or "id" not in result:
        return False, "result does not identify a stored row"
    with store.read() as db:
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (result["id"],)).fetchone()
    if row is None:
        return False, f"{table} row {result['id']} not found after execution"
    for key, value in expected.items():
        if key == "title":
            value = value.strip()
        if row[key] != value:
            return False, f"{table}.{key} is {row[key]!r}, expected {value!r}"
    return True, f"re-read {table} row {result['id']} from the database"


def _verify_memory(store: Store, item: Any) -> tuple[bool, str]:
    if not isinstance(item, dict) or "id" not in item:
        return False, "result does not identify a memory item"
    stored = store.memory.get(item["id"])
    if not stored or stored["status"] != "active" or stored["value"] != item["value"]:
        return False, f"memory {item.get('id')} is not stored as active with the expected value"
    return True, f"re-read memory {item['id']} (active) from the database"


def _verify_correction(store: Store, old_id: int, result: Any) -> tuple[bool, str]:
    old, new = store.memory.get(old_id), store.memory.get(result["item"]["id"])
    if result["action"] == "unchanged":
        return bool(old and old["status"] == "active"), "value was already current"
    ok = bool(old and new and old["status"] == "superseded" and old["superseded_by_id"] == new["id"]
              and new["status"] == "active")
    return ok, "old version superseded and new version active" if ok else "supersession chain not as expected"


def _verify_forgotten(store: Store, result: Any) -> tuple[bool, str]:
    """Re-read every version and the index independently; check the erasure post-condition."""
    ids = result.get("forgotten_ids") if isinstance(result, dict) else None
    if not ids:
        return False, "result does not identify the forgotten memory"
    for memory_id in ids:
        item = store.memory.get(memory_id)
        if not item or item["status"] != "forgotten" or item["value"] != "[forgotten]" or item["key"] is not None:
            return False, f"memory {memory_id}: content still present"
    if store.fts5:
        with store.read() as db:
            marks = ",".join("?" * len(ids))
            if db.execute(f"SELECT 1 FROM memory_fts WHERE rowid IN ({marks})", ids).fetchone():
                return False, "memory still present in the search index"
    if result.get("already_forgotten"):
        return True, "memory had already been forgotten; nothing left to erase"
    residue = (result.get("residue") or {}).get("derived")
    if residue != 0:
        return False, f"{residue} derived record(s) still contain the forgotten content"
    note = "re-read: all versions erased, absent from the search index, no derived copies left in the database"
    if not (result.get("storage") or {}).get("wal_truncated", False):
        note += "; the write-ahead log could not be truncated yet (another connection is open)"
    return True, note


def _verify_calculation(arguments: dict[str, Any], result: Any) -> tuple[bool, str]:
    """Independent recomputation through a second evaluation path (Python's own parser on the same AST)."""
    try:
        tree = ast.parse(arguments["expression"], mode="eval")
        recomputed = _independent_eval(tree.body)
    except Exception as exc:  # noqa: BLE001
        return False, f"independent recomputation failed: {type(exc).__name__}"
    value = result.get("result") if isinstance(result, dict) else None
    ok = isinstance(value, (int, float)) and abs(value - recomputed) <= 1e-9 * max(1.0, abs(recomputed))
    return ok, "independently recomputed" if ok else f"recomputation gave {recomputed!r}"


def _independent_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp):
        v = _independent_eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else +v
    if isinstance(node, ast.BinOp):
        left, right = _independent_eval(node.left), _independent_eval(node.right)
        ops = {ast.Add: lambda: left + right, ast.Sub: lambda: left - right, ast.Mult: lambda: left * right,
               ast.Div: lambda: left / right, ast.FloorDiv: lambda: left // right, ast.Mod: lambda: left % right,
               ast.Pow: lambda: left ** right}
        return ops[type(node.op)]()
    raise ValueError("unsupported node")


def _safe_args(args: dict[str, Any], extra_sensitive: set[str] | None = None) -> dict[str, Any]:
    # Audit metadata must not become a second store for secrets or private content.
    sensitive = {"value", "message", "api_key", "token", "password", "secret"} | (extra_sensitive or set())
    safe: dict[str, Any] = {}
    for key, value in args.items():
        if key.lower() in sensitive:
            safe[key] = "[REDACTED]"
        elif isinstance(value, str) and len(value) > 200:
            safe[key] = redact_text(value[:200]) + "…"
        elif isinstance(value, str):
            safe[key] = redact_text(value)
        else:
            safe[key] = value
    return safe


_ALLOWED_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
                ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
                ast.UAdd: operator.pos}


def safe_calculate(expression: str) -> float | int:
    if len(expression) > 160:
        raise ToolError("Expression is too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise ToolError("Invalid arithmetic expression") from None

    def visit(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if abs(node.value) > 10**100:
                raise ToolError("Number is too large")
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BIN:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ToolError("Exponent is too large")
            result = _ALLOWED_BIN[type(node.op)](left, right)
            if isinstance(result, (int, float)) and abs(result) > 10**100:
                raise ToolError("Result is too large")
            return result
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_BIN:
            return _ALLOWED_BIN[type(node.op)](visit(node.operand))
        raise ToolError("Only numeric constants and basic arithmetic are allowed")
    try:
        return visit(tree)
    except (ZeroDivisionError, OverflowError):
        raise ToolError("Invalid arithmetic operation") from None
