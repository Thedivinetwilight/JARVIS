"""Solver interface: the seam where a real model (or Inspect) plugs in later."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..tools import Executor, ToolError


@dataclass
class SolveResult:
    status: str = "failed"  # done | partial | failed | clarify | blocked
    message: str = ""
    claims: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    notes: dict[str, Any] = field(default_factory=dict)


class ToolContext:
    """What a solver may see and do. Tool calls are mediated by the executor."""

    def __init__(self, executor: Executor, policy: dict[str, Any],
                 mutations: tuple[str, ...], task_id: str) -> None:
        self.executor = executor
        self.policy = policy
        self.mutations = set(mutations)
        self.task_id = task_id
        self.approval_requests: list[str] = []
        self.tool_calls = 0

    def call(self, tool: str, args: dict[str, Any], *,
             idempotency_key: str | None = None,
             approval_granted: bool = False) -> dict[str, Any]:
        self.tool_calls += 1
        return self.executor.call(tool, args, idempotency_key=idempotency_key,
                                  approval_granted=approval_granted)

    def request_approval(self, summary: str) -> bool:
        """Scripted principal: approves unless the fixture declares auto_deny."""
        self.approval_requests.append(summary)
        return not bool(self.policy.get("auto_deny", False))

    def world_snapshot(self) -> dict[str, Any]:
        return self.executor.world.snapshot()

    def restore_step(self, attempt: int, processed: int) -> None:
        """Mediated progress check inside a restore attempt (see Executor.restore_step)."""
        self.executor.restore_step(attempt, processed)

    def restore_write_step(self, attempt: int, record: str, part_index: int,
                           total_parts: int) -> None:
        """Mediated progress check inside one record write.

        See :meth:`Executor.restore_write_step`. A solver that skips it cannot be
        interrupted mid-write, and a scenario expecting a mid-write interruption
        will report that its write state never reached ``partially_applied``.
        """
        self.executor.restore_write_step(attempt, record, part_index, total_parts)

    # -- durability (B4W) -------------------------------------------------
    def durability_open(self) -> dict[str, Any]:
        return self.executor.durability_open()

    def durability_write(self, index: int) -> dict[str, Any]:
        return self.executor.durability_write(index)

    def durability_recover(self, **knobs: Any) -> dict[str, Any]:
        return self.executor.durability_recover(**knobs)

    def durability_apply_uncommitted(self, record_id: str) -> dict[str, Any]:
        return self.executor.durability_apply_uncommitted(record_id)

    def durability_finish(self, report: dict[str, Any]) -> dict[str, Any]:
        return self.executor.durability_finish(report)

    def mutated(self, name: str) -> bool:
        return name in self.mutations


class Solver(Protocol):
    name: str

    def solve(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        ...


def call_with_retry(ctx: ToolContext, tool: str, args: dict[str, Any], *,
                    idempotency_key: str, approval_granted: bool) -> dict[str, Any]:
    """One idempotent retry after a simulated transport timeout."""
    try:
        return ctx.call(tool, args, idempotency_key=idempotency_key,
                        approval_granted=approval_granted)
    except ToolError as exc:
        if exc.__class__.__name__ != "ToolTimeout":
            raise
        key = idempotency_key
        if ctx.mutated("retry_without_idempotency"):
            key = f"{idempotency_key}-retry"
        return ctx.call(tool, args, idempotency_key=key,
                        approval_granted=approval_granted)
