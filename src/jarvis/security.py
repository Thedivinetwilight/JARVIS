"""Centralized, fail-closed tool authorization."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

Risk = Literal["read", "write", "network", "dangerous"]

@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    risk: Risk

class Policy:
    """Policy defaults to local read-only. No model message can change this policy."""
    def __init__(self, network_enabled: bool = False, allow_writes: bool = True):
        self.network_enabled = network_enabled
        self.allow_writes = allow_writes

    def decide(self, tool_name: str, risk: Risk, *, explicitly_requested: bool = False) -> Decision:
        if risk == "dangerous":
            return Decision(False, "Dangerous operations are not available to the model", risk)
        if risk == "network" and not self.network_enabled:
            return Decision(False, "Network access is disabled by configuration", risk)
        if risk == "write" and not self.allow_writes:
            return Decision(False, "Write operations are disabled by policy", risk)
        if risk == "write" and not explicitly_requested:
            return Decision(False, "Write operation requires explicit user intent", risk)
        return Decision(True, "Allowed by local policy", risk)
