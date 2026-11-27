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
    "documents.ingest": "partially implemented: workspace.read/find/search/summarize read small "
                        "UTF-8 text files from configured roots; no PDF/Office parsing",
    "web.search": "not implemented; retrieval is a direct allow-listed GET (web.get), not a search engine",
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


#: Mission 003 capability matrix. Statuses are deliberately narrow: IMPLEMENTED means the code
#: exists, TESTED means automated tests exercise it, VERIFIED means it was observed working
#: outside the test suite, SIMULATED means it was exercised only through stand-ins, UNVERIFIED
#: means no evidence exists yet, NOT IMPLEMENTED means it does not exist.
MISSION_003: tuple[dict[str, str], ...] = (
    {"capability": "workspace intelligence (stat/find/search/summarize)",
     "status": "TESTED",
     "evidence": "tests/test_workspace.py, eval workspace_search_real/workspace_summary_real",
     "limits": "read-only; configured roots only; 64 KiB reads; 2 MiB files; 2000-file scans"},
    {"capability": "web retrieval (allow-listed GET + extractive summary)",
     "status": "TESTED",
     "evidence": "tests/test_web.py, eval web_summary_real (loopback server)",
     "limits": "GET only; explicit host allow-list; 80/443 unless authorised loopback; 512 KiB cap"},
    {"capability": "web search provider",
     "status": "NOT IMPLEMENTED",
     "evidence": "no search API is integrated; a request for research is blocked honestly",
     "limits": "—"},
    {"capability": "prompt-injection resistance",
     "status": "SIMULATED",
     "evidence": "tests/test_injection.py, eval injection_workspace_real/injection_web_real",
     "limits": "adversarial fixtures and fenced untrusted content; no production traffic sample"},
    {"capability": "structural re-decomposition",
     "status": "TESTED",
     "evidence": "tests/test_schedule_api.py, eval structural_replan_simulated",
     "limits": "at most 2 revisions; policy/approval denials are never re-planned"},
    {"capability": "persistent scheduler (priority/deadline/recurrence)",
     "status": "TESTED",
     "evidence": "tests/test_scheduler.py, eval schedule_dedupe_simulated",
     "limits": "once/hourly/daily/weekly; max 3 dispatches per tick; skipped slots are not replayed"},
    {"capability": "objective leases (single writer, fencing tokens)",
     "status": "TESTED",
     "evidence": "tests/test_leases.py, eval lease_single_writer_simulated",
     "limits": "in-process threads and separate connections; no multi-host verification"},
    {"capability": "multi-process / multi-host coordination",
     "status": "UNVERIFIED",
     "evidence": "concurrency is tested with threads and independent SQLite connections only",
     "limits": "a shared database on a network filesystem is untested"},
)


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
        "mission_003": list(MISSION_003),
        "workspace_access": {
            "roots": list(getattr(settings, "workspace_roots", ()) or [str(settings.workspace)]),
            "read_only": True,
            "web_allowlist": list(getattr(settings, "web_allowlist", ())),
            "retrieval_enabled": bool(getattr(settings, "web_allowlist", ()))
            and bool(getattr(settings, "network_enabled", False)),
        },
        "sensitive_data_to_provider": bool(getattr(settings, "allow_sensitive_to_provider", False)),
    }
