"""Centralized, fail-closed authorization for capability execution.

The policy is enforced in code at the capability registry. It is a pure function of
(capability risk, approval requirements, request origin, approval state, autonomy
level, configuration). It deliberately takes **no** model output and **no**
emotional appraisal as input, so neither can talk it into allowing an action
(Acceptance test 9).

Autonomy levels (Addendum H1). They govern what JARVIS may do on its own
initiative (model proposals and kernel inferences). Explicit user commands are
user-directed and are not "autonomous" actions.

  0 OBSERVE             read permitted information and report status
  1 RECOMMEND           suggest actions in text; nothing is journaled for execution
  2 PREPARE (default)   prepare operations as proposals that await user approval
  3 EXECUTE_REVERSIBLE  same as 2 in Genesis 0.1 (standing grants are designed, not implemented)
  4 MANAGE_WORKFLOWS    not implemented — rejected by configuration validation
  5 EXPERIMENTAL        not implemented — rejected by configuration validation

Irreversible capabilities (``requires_approval``) always need an approval record,
whatever the origin or level.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Literal

Risk = Literal["read", "write", "network", "dangerous"]


class AutonomyLevel(IntEnum):
    OBSERVE = 0
    RECOMMEND = 1
    PREPARE = 2
    EXECUTE_REVERSIBLE = 3
    MANAGE_WORKFLOWS = 4
    EXPERIMENTAL = 5


IMPLEMENTED_MAX_AUTONOMY = AutonomyLevel.EXECUTE_REVERSIBLE


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    risk: Risk
    needs_approval: bool = False


class Policy:
    """Policy defaults to local-only. No model message can change this policy."""

    def __init__(self, network_enabled: bool = False, allow_writes: bool = True,
                 autonomy_level: int = AutonomyLevel.PREPARE):
        level = AutonomyLevel(int(autonomy_level))
        if level > IMPLEMENTED_MAX_AUTONOMY:
            raise ValueError(f"Autonomy level {int(level)} ({level.name}) is not implemented in this build")
        self.network_enabled = network_enabled
        self.allow_writes = allow_writes
        self.autonomy_level = level

    def decide(self, tool_name: str, risk: Risk, *, explicitly_requested: bool = False,
               origin: str | None = None, requires_approval: bool = False, approved: bool = False) -> Decision:
        """Decide whether a capability invocation may run now.

        ``origin`` is one of contracts.Origin values. For v0.2 compatibility, when it is
        omitted, ``explicitly_requested=True`` means a user command and ``False`` means a
        request without explicit user intent.
        """
        if origin is None:
            origin = "user_command" if explicitly_requested else "unspecified"
        if risk == "dangerous":
            return Decision(False, "Dangerous operations are not available to the model", risk)
        if risk == "network" and not self.network_enabled:
            return Decision(False, "Network access is disabled by configuration", risk)
        if risk == "write" and not self.allow_writes:
            return Decision(False, "Write operations are disabled by policy", risk)
        if origin == "approval":
            if not approved:
                return Decision(False, "Approval origin without an approval record", risk)
            return Decision(True, "Approved by the user", risk)
        if requires_approval:
            if origin in {"model_proposal", "kernel"} and self.autonomy_level < AutonomyLevel.PREPARE:
                return Decision(False, f"Autonomy level {int(self.autonomy_level)} does not permit preparing "
                                       "actions", risk)
            return Decision(False, "Irreversible operation requires explicit approval", risk, needs_approval=True)
        if origin == "user_command":
            return Decision(True, "Allowed by local policy", risk)
        if origin in {"model_proposal", "kernel"}:
            if risk == "read":
                return Decision(True, "Read-only operation allowed by local policy", risk)
            if self.autonomy_level < AutonomyLevel.PREPARE:
                return Decision(False, f"Autonomy level {int(self.autonomy_level)} does not permit preparing "
                                       "actions", risk)
            return Decision(False, "Operations proposed by JARVIS or a model require user approval", risk,
                            needs_approval=True)
        # Unspecified origin (v0.2 behaviour): reads allowed, writes need explicit intent.
        if risk == "write":
            return Decision(False, "Write operation requires explicit user intent", risk)
        return Decision(True, "Allowed by local policy", risk)
