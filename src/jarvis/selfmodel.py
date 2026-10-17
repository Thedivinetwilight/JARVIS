"""Self-model: what JARVIS can actually do, grounded in observable system state.

The capability report is built from the live registry, the configured provider,
the database (schema, FTS5 availability, recorded outcomes) and the policy. It
never reports a designed-but-unimplemented capability as available (Acceptance
test 15). ``check_claims`` detects responses that claim actions which did not
happen in the current cycle (Section 5.9 of the specification).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .db import Store
    from .security import Policy
    from .tools import ToolRegistry

# Designed or requested capabilities that are NOT implemented in this build.
NOT_IMPLEMENTED: dict[str, str] = {
    "vision.images": "not implemented",
    "documents.ingest": "not implemented (workspace.read can read small UTF-8 text files)",
    "web.search": "not implemented; no network tools are registered",
    "shell.exec": "deliberately excluded by the security policy",
    "calendar_email": "not implemented; external accounts require explicit scoped connectors",
    "semantic_embeddings": "not implemented; retrieval is lexical (see EXP-001)",
    "autonomy.level_4_workflows": "designed, not implemented",
    "autonomy.level_5_self_improvement": "designed, not implemented",
    "model_training": "not implemented",
}

_CLAIM = re.compile(
    r"\b(?:i(?:'ve| have)|i)\s+(?:just\s+|now\s+|already\s+|successfully\s+)?"
    r"(?:saved|stored|recorded|remembered|created|added|updated|deleted|removed|erased|forgot(?:ten)?|"
    r"searched|looked up|browsed|ran|executed|tested|completed|marked|scheduled|sent|emailed|booked|"
    r"installed|deployed|committed|pushed)\b", re.IGNORECASE)


def check_claims(text: str, *, executed_operations: int) -> list[str]:
    """Return claimed actions in ``text`` that are unsupported by executed operations."""
    if executed_operations > 0:
        return []
    return [m.group(0) for m in _CLAIM.finditer(text)]


def capability_report(*, registry: "ToolRegistry", store: "Store", policy: "Policy", provider: Any,
                      settings: Any) -> dict[str, Any]:
    stats = store.operations.stats()
    tools = []
    for desc in registry.descriptions():
        observed = stats.get(desc["name"], {})
        tools.append({**desc, "status": "available", "observed_outcomes": observed})
    if provider is None:
        provider_state = {"status": "unavailable",
                          "reason": "no provider configured (or remote provider without JARVIS_NETWORK_ENABLED=true)",
                          "effect": "deterministic responses only; free-form conversation is limited"}
    else:
        provider_state = {"status": "configured", "name": provider.name, "model": provider.model,
                          "remote": bool(getattr(provider, "is_remote", True)),
                          "note": "configured is not the same as reachable; see the last cycle's provider_outcome"}
    with store.read() as db:
        last = db.execute("SELECT provider, provider_outcome, started_at FROM cycles WHERE provider IS NOT NULL "
                          "ORDER BY started_at DESC LIMIT 1").fetchone()
    if last:
        provider_state["last_observed"] = dict(last)
    from .voice import probe as voice_probe
    voice = voice_probe()
    interfaces = {"text": {"status": "available", "channels": ["cli", "repl", "api"]},
                  "voice": {"status": "available" if voice.available else "unavailable", "adapter": "termux",
                            "reason": voice.reason,
                            "note": "verified with simulated Termux:API commands only, not on a real device"}}
    return {
        "interfaces": interfaces,
        "kernel": {"cycle": "observe→interpret→appraise→retrieve→assess→plan→authorise→act→validate→respond→record",
                   "interpreter": "rules-v1 (deterministic)", "appraisal": "affect-rules-v1 (deterministic)",
                   "retrieval": "fts5-bm25 + recency/status ranking" if store.fts5 else "token-like fallback",
                   "schema_version": store.schema_version},
        "policy": {"autonomy_level": int(policy.autonomy_level), "network_enabled": policy.network_enabled,
                   "writes_enabled": policy.allow_writes},
        "provider": provider_state,
        "tools": tools,
        "not_implemented": NOT_IMPLEMENTED,
        "sensitive_data_to_provider": bool(getattr(settings, "allow_sensitive_to_provider", False)),
    }
