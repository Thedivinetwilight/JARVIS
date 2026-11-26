"""Persistent multi-step plans with bounded execution and replanning.

Mission §7.5 / Roadmap R4: plans that survive failure. A plan is a persisted
record of steps (capability, arguments, preconditions, expected outcomes),
executed through the ToolRegistry with bounded retry and replanning.

Plan lifecycle:
    proposed -> active -> (step executing) -> completed | failed | cancelled
    failed steps can be retried (bounded) or trigger replanning
    interrupted plans resume at the first unverified step

Bounds:
- MAX_STEPS = 12 steps per plan
- MAX_RETRIES_PER_STEP = 2 (bounded, no endless retry)
- MAX_REPLANS = 3 per plan
- Each step has a timeout (from the tool's timeout_s)
- Plans are persisted; a crash leaves the plan in 'active' with the
  current step marked 'interrupted' (never assumed successful)

This module is the execution runtime's planning layer. The kernel creates
plans for multi-step intents; single-step intents continue to use the
direct registry path.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .contracts import ActionOutcome, ActionRequest, OperationStatus, Origin
from .util import to_iso

MAX_STEPS = 12
MAX_RETRIES_PER_STEP = 2
MAX_REPLANS = 3


@dataclass
class PlanStepRecord:
    id: int
    plan_id: str
    seq: int
    capability: str
    arguments: dict[str, Any]
    purpose: str
    status: str  # pending | running | awaiting_approval | succeeded | failed | skipped | interrupted
    attempts: int = 0
    operation_id: int | None = None
    result_summary: str = ""
    error_type: str = ""
    error_message: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "plan_id": self.plan_id, "seq": self.seq,
            "capability": self.capability, "arguments": self.arguments,
            "purpose": self.purpose, "status": self.status,
            "attempts": self.attempts, "operation_id": self.operation_id,
            "result_summary": self.result_summary,
            "error_type": self.error_type, "error_message": self.error_message,
        }


@dataclass
class PlanRecord:
    id: str
    goal_id: int | None
    title: str
    status: str  # proposed | active | completed | failed | cancelled
    steps: list[PlanStepRecord] = field(default_factory=list)
    replans: int = 0
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    failure_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "goal_id": self.goal_id, "title": self.title,
            "status": self.status, "replans": self.replans,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "completed_at": self.completed_at, "failure_reason": self.failure_reason,
            "progress": self.progress,
            "steps": [s.to_dict() for s in self.steps],
        }

    @property
    def next_step(self) -> PlanStepRecord | None:
        for step in self.steps:
            if step.status in ("pending", "interrupted"):
                return step
        return None

    @property
    def progress(self) -> dict[str, int]:
        total = len(self.steps)
        done = sum(1 for s in self.steps if s.status == "succeeded")
        failed = sum(1 for s in self.steps if s.status == "failed")
        awaiting = sum(1 for s in self.steps if s.status == "awaiting_approval")
        return {"total": total, "succeeded": done, "failed": failed,
                "awaiting_approval": awaiting,
                "pending": total - done - failed - awaiting}


class PlanError(RuntimeError):
    pass


class PlanStore:
    """Persistence for plans and their steps."""

    def __init__(self, store):
        self.store = store

    # ------------------------------------------------------------ creation
    def create_plan(self, *, title: str, steps: list[dict[str, Any]],
                    goal_id: int | None = None) -> PlanRecord:
        """Create a plan with steps. Each step: {capability, arguments, purpose}."""
        if not steps:
            raise PlanError("A plan needs at least one step")
        if len(steps) > MAX_STEPS:
            raise PlanError(f"A plan may have at most {MAX_STEPS} steps")
        import secrets
        plan_id = f"plan_{secrets.token_hex(6)}"
        now = to_iso(self.store.now())
        with self.store.write() as db:
            db.execute(
                "INSERT INTO plans (id, goal_id, title, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (plan_id, goal_id, title, "proposed", now, now))
            for seq, step in enumerate(steps, start=1):
                db.execute(
                    "INSERT INTO plan_steps (plan_id, seq, capability, arguments, purpose) "
                    "VALUES (?,?,?,?,?)",
                    (plan_id, seq, step["capability"],
                     json.dumps(step.get("arguments", {})), step.get("purpose", "")))
        return self.get_plan(plan_id)

    def get_plan(self, plan_id: str) -> PlanRecord:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise PlanError(f"Plan not found: {plan_id}")
            steps = db.execute(
                "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY seq",
                (plan_id,)).fetchall()
        return self._row_to_plan(row, steps)

    def list_plans(self, *, status: str | None = None, limit: int = 50) -> list[PlanRecord]:
        with self.store.read() as db:
            if status:
                rows = db.execute(
                    "SELECT * FROM plans WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit)).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM plans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            plans = []
            for row in rows:
                steps = db.execute(
                    "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY seq",
                    (row["id"],)).fetchall()
                plans.append(self._row_to_plan(row, steps))
        return plans

    # ------------------------------------------------------------ lifecycle
    def activate(self, plan_id: str) -> PlanRecord:
        now = to_iso(self.store.now())
        with self.store.write() as db:
            row = db.execute("SELECT status FROM plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise PlanError(f"Plan not found: {plan_id}")
            if row["status"] != "proposed":
                raise PlanError(f"Plan is {row['status']}, cannot activate")
            db.execute(
                "UPDATE plans SET status = 'active', updated_at = ? WHERE id = ?",
                (now, plan_id))
        return self.get_plan(plan_id)

    def complete(self, plan_id: str) -> PlanRecord:
        now = to_iso(self.store.now())
        with self.store.write() as db:
            db.execute(
                "UPDATE plans SET status = 'completed', completed_at = ?, updated_at = ? "
                "WHERE id = ?", (now, now, plan_id))
        return self.get_plan(plan_id)

    def fail(self, plan_id: str, reason: str) -> PlanRecord:
        now = to_iso(self.store.now())
        with self.store.write() as db:
            db.execute(
                "UPDATE plans SET status = 'failed', failure_reason = ?, updated_at = ? "
                "WHERE id = ?", (reason, now, plan_id))
        return self.get_plan(plan_id)

    def cancel(self, plan_id: str) -> PlanRecord:
        now = to_iso(self.store.now())
        with self.store.write() as db:
            db.execute(
                "UPDATE plans SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (now, plan_id))
            db.execute(
                "UPDATE plan_steps SET status = 'skipped' "
                "WHERE plan_id = ? AND status = 'pending'", (plan_id,))
        return self.get_plan(plan_id)

    # ------------------------------------------------------------ step updates
    def mark_step_running(self, step_id: int) -> None:
        with self.store.write() as db:
            db.execute(
                "UPDATE plan_steps SET status = 'running', attempts = attempts + 1 "
                "WHERE id = ?", (step_id,))

    def mark_step_succeeded(self, step_id: int, *, operation_id: int | None,
                            result_summary: str = "") -> None:
        with self.store.write() as db:
            db.execute(
                "UPDATE plan_steps SET status = 'succeeded', operation_id = ?, "
                "result_summary = ? WHERE id = ?",
                (operation_id, result_summary, step_id))

    def mark_step_failed(self, step_id: int, *, error_type: str, error_message: str) -> None:
        with self.store.write() as db:
            db.execute(
                "UPDATE plan_steps SET status = 'failed', error_type = ?, error_message = ? "
                "WHERE id = ?", (error_type, error_message, step_id))

    def mark_step_awaiting_approval(self, step_id: int, *, operation_id: int | None,
                                    reason: str = "") -> None:
        """The step produced a proposed operation: the plan pauses, it has not failed."""
        with self.store.write() as db:
            db.execute(
                "UPDATE plan_steps SET status = 'awaiting_approval', operation_id = ?, "
                "error_type = 'needs_approval', error_message = ? WHERE id = ?",
                (operation_id, reason, step_id))

    def mark_awaiting_approval(self, plan_id: str) -> PlanRecord:
        with self.store.write() as db:
            db.execute("UPDATE plans SET status = 'awaiting_approval', updated_at = ? WHERE id = ?",
                       (to_iso(self.store.now()), plan_id))
        return self.get_plan(plan_id)

    def mark_step_interrupted(self, step_id: int) -> None:
        with self.store.write() as db:
            db.execute(
                "UPDATE plan_steps SET status = 'interrupted' WHERE id = ?", (step_id,))

    def reset_step_for_retry(self, step_id: int) -> None:
        with self.store.write() as db:
            db.execute(
                "UPDATE plan_steps SET status = 'pending', error_type = '', error_message = '' "
                "WHERE id = ?", (step_id,))

    def increment_replans(self, plan_id: str) -> int:
        with self.store.write() as db:
            row = db.execute("SELECT replans FROM plans WHERE id = ?", (plan_id,)).fetchone()
            new_count = row["replans"] + 1
            db.execute("UPDATE plans SET replans = ?, updated_at = ? WHERE id = ?",
                       (new_count, to_iso(self.store.now()), plan_id))
            return new_count

    def recover_interrupted(self) -> list[str]:
        """Mark running steps of active plans as interrupted. Returns plan ids."""
        with self.store.write() as db:
            rows = db.execute(
                "SELECT DISTINCT plan_id FROM plan_steps WHERE status = 'running'").fetchall()
            plan_ids = [r["plan_id"] for r in rows]
            db.execute(
                "UPDATE plan_steps SET status = 'interrupted' WHERE status = 'running'")
            return plan_ids

    @staticmethod
    def _row_to_plan(row: sqlite3.Row, steps: list[sqlite3.Row]) -> PlanRecord:
        return PlanRecord(
            id=row["id"], goal_id=row["goal_id"], title=row["title"],
            status=row["status"], replans=row["replans"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            completed_at=row["completed_at"], failure_reason=row["failure_reason"],
            steps=[PlanStore._row_to_step(s) for s in steps])

    @staticmethod
    def _row_to_step(row: sqlite3.Row) -> PlanStepRecord:
        return PlanStepRecord(
            id=row["id"], plan_id=row["plan_id"], seq=row["seq"],
            capability=row["capability"], arguments=json.loads(row["arguments"]),
            purpose=row["purpose"], status=row["status"], attempts=row["attempts"],
            operation_id=row["operation_id"], result_summary=row["result_summary"],
            error_type=row["error_type"], error_message=row["error_message"])


class PlanExecutor:
    """Executes plan steps through the ToolRegistry with bounded retry.

    Retry policy: a failed step is retried up to MAX_RETRIES_PER_STEP times
    if the failure is retryable (timeout, unavailable). Non-retryable
    failures (denied, validation) fail the step immediately. After retries
    are exhausted, the plan is marked failed with the reason recorded.
    """

    # Transient failure kinds. Status TIMED_OUT is always transient; handler exceptions
    # surface as their own type names, so both spellings are listed.
    RETRYABLE_ERRORS = frozenset({"timeout", "unavailable", "http_error", "Timeout", "TimeoutError",
                                  "ConnectionError", "ConnectionResetError", "ConnectionAbortedError",
                                  "OSError", "ProviderTimeout", "ProviderUnavailable", "HandlerTimeout"})

    def __init__(self, plan_store: PlanStore, registry, *, max_retries: int = MAX_RETRIES_PER_STEP):
        self.plans = plan_store
        self.registry = registry
        self.max_retries = max_retries

    def execute(self, plan_id: str, *, correlation_id: str | None = None,
                session_id: str | None = None,
                origin: Origin = Origin.USER_COMMAND) -> PlanRecord:
        """Execute pending steps, resuming after approval when it was granted.

        ``origin`` is the authority the plan runs under: a plan the owner asked for is
        a user command; a plan a model proposed executes reads and pauses on writes.

        A step that needs approval pauses the plan in ``awaiting_approval`` with the
        proposed operation id. Calling ``execute`` again re-reads that operation: still
        proposed -> stay paused; approved and succeeded -> the step is recorded as
        succeeded and the plan continues; denied/cancelled/failed -> the step fails.
        """
        plan = self.plans.get_plan(plan_id)
        if plan.status == "proposed":
            plan = self.plans.activate(plan_id)
        if plan.status not in {"active", "awaiting_approval"}:
            return plan

        for step in plan.steps:
            if step.status == "succeeded":
                continue
            if step.status == "failed":
                break                                    # already failed; do not continue past it
            if step.status == "awaiting_approval":
                record = (self.plans.store.operations.get(step.operation_id)
                          if step.operation_id is not None else None)
                if record is None:
                    self.plans.mark_step_failed(step.id, error_type="operation_missing",
                                                error_message="the proposed operation no longer exists")
                    break
                if record["status"] == "proposed":
                    return self.plans.mark_awaiting_approval(plan_id)     # still waiting
                if record["status"] == "succeeded":
                    self.plans.mark_step_succeeded(
                        step.id, operation_id=step.operation_id,
                        result_summary=record.get("verification_note") or "approved and executed")
                    continue
                self.plans.mark_step_failed(step.id, error_type=f"approval_{record['status']}",
                                            error_message=record.get("error_message") or record["status"])
                break
            if step.status not in ("pending", "interrupted"):
                continue

            outcome = self._execute_step(step, correlation_id=correlation_id,
                                         session_id=session_id, origin=origin)
            if outcome.status == OperationStatus.SUCCEEDED:
                self.plans.mark_step_succeeded(
                    step.id, operation_id=outcome.operation_id,
                    result_summary=self._summarise(outcome))
            elif outcome.status == OperationStatus.PROPOSED:
                self.plans.mark_step_awaiting_approval(
                    step.id, operation_id=outcome.operation_id, reason=outcome.reason)
                return self.plans.mark_awaiting_approval(plan_id)
            else:
                error_kind = self._error_kind(outcome)
                if self._retryable(outcome) and step.attempts < self.max_retries:
                    self.plans.reset_step_for_retry(step.id)
                    outcome = self._execute_step(step, correlation_id=correlation_id,
                                                 session_id=session_id, origin=origin)
                    if outcome.status == OperationStatus.SUCCEEDED:
                        self.plans.mark_step_succeeded(
                            step.id, operation_id=outcome.operation_id,
                            result_summary=self._summarise(outcome))
                        continue
                self.plans.mark_step_failed(
                    step.id, error_type=outcome.error_type or error_kind,
                    error_message=outcome.error_message or outcome.reason)
                break

        plan = self.plans.get_plan(plan_id)
        if all(s.status == "succeeded" for s in plan.steps):
            return self.plans.complete(plan_id)
        if any(s.status == "failed" for s in plan.steps):
            failed_step = next(s for s in plan.steps if s.status == "failed")
            return self.plans.fail(
                plan_id,
                f"Step {failed_step.seq} ({failed_step.capability}) failed: "
                f"{failed_step.error_message or failed_step.error_type}")
        if any(s.status == "awaiting_approval" for s in plan.steps):
            return self.plans.mark_awaiting_approval(plan_id)
        return plan

    def _execute_step(self, step: PlanStepRecord, *, correlation_id, session_id,
                      origin: Origin = Origin.USER_COMMAND) -> ActionOutcome:
        self.plans.mark_step_running(step.id)
        request = ActionRequest(
            step.capability, step.arguments, origin,
            reason=step.purpose)
        return self.registry.run(
            request, correlation_id=correlation_id, session_id=session_id)

    @classmethod
    def _retryable(cls, outcome: ActionOutcome) -> bool:
        """A timeout is transient by definition; a denial or validation error is not."""
        if outcome.status == OperationStatus.TIMED_OUT:
            return True
        if outcome.status != OperationStatus.FAILED:
            return False
        kind = outcome.error_type or ""
        if kind in cls.RETRYABLE_ERRORS:
            return True
        # Network errors arrive as urllib/OS exceptions with a status-less type name.
        return kind.endswith(("Timeout", "Unavailable")) or kind.startswith("HTTPError")

    @staticmethod
    def _error_kind(outcome: ActionOutcome) -> str:
        if outcome.error_type:
            return outcome.error_type
        if outcome.status == OperationStatus.TIMED_OUT:
            return "timeout"
        return "failed"

    @staticmethod
    def _summarise(outcome: ActionOutcome) -> str:
        if outcome.verification_note:
            return outcome.verification_note
        return f"{outcome.capability}: {outcome.status.value}"
