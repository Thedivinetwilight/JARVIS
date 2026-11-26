"""Application composition root.

``Jarvis`` assembles one coherent runtime from the persistent store, identity,
policy, capability registry, optional model provider and the cognitive kernel.
On start-up it performs recovery: operations left ``started`` by a crashed process
become ``interrupted`` (never assumed successful), abandoned cycles are closed,
and expired sensitive records are purged according to retention settings.
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from . import __version__
from .api import read_api_token
from .audio_sessions import AudioSessionService
from .auth import AuthService, load_or_create_server_secret
from .charter import CHARTER_VERSION, system_prompt
from .config import Settings
from .db import Store
from .egress import GUARD
from .kernel import CognitiveKernel
from .logging import configure_logging, get_logger, info as _log_info, warning as _log_warning
from .objectives import ObjectiveService, ObjectiveStore
from .plans import PlanExecutor, PlanStore
from .provider import ModelProvider, build_provider
from .redact import register_secret
from .reminders import ReminderService
from .security import Policy
from .selfmodel import capability_report
from .tools import ToolRegistry
from .util import PRIVATE_DIR_MODE, Clock, to_iso

_log = get_logger("jarvis.core")

# Kept for v0.2 compatibility; the kernel builds its system prompt from the charter.
SYSTEM_PROMPT = system_prompt("Sivaram")

_UNSET: Any = object()


class Jarvis:
    def __init__(self, settings: Settings | None = None, *, provider: ModelProvider | None = _UNSET,
                 clock: Clock | None = None, use_fts5: bool | None = None):
        self.settings = settings or Settings.load()
        configure_logging(self.settings.log_level)
        self.settings.home.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)   # 0700 when created here
        register_secret(self.settings.gemini_api_key)
        register_secret(self.settings.openai_api_key)
        register_secret(read_api_token(self.settings.home))   # a pasted API token is redacted like any key
        self.store = Store(self.settings.home / "jarvis.sqlite3", clock=clock, use_fts5=use_fts5)
        self.policy = Policy(network_enabled=self.settings.network_enabled, allow_writes=True,
                             autonomy_level=self.settings.autonomy_level)
        self.identity = self.store.state.ensure_identity(principal=self.settings.principal_name,
                                                         charter_version=CHARTER_VERSION)
        self.runtime_id = self.store.state.register_runtime(version=__version__,
                                                            schema_version=self.store.schema_version, pid=os.getpid())
        self.reminders = ReminderService(self.store)
        self.tools = ToolRegistry(self.store, self.settings.workspace, self.policy,
                                  runtime_id=self.runtime_id, reminders=self.reminders)
        self.provider = build_provider(self.settings) if provider is _UNSET else provider
        # Consolidated subsystems (bbff branch): scoped auth, reminders, audio, plans
        _server_secret = load_or_create_server_secret(self.settings.home)
        register_secret(_server_secret)
        self.auth = AuthService(self.store, _server_secret)
        self.audio = AudioSessionService(self.store)
        self.plans = PlanStore(self.store)
        self.plan_executor = PlanExecutor(self.plans, self.tools)
        # Objective-driven autonomy (Mission 002): interpretation -> plan -> execution ->
        # evidence-based verification -> honest outcome. Steps still run through self.tools.
        self.objectives = ObjectiveService(self.store, self.tools, self.plans,
                                           executor=self.plan_executor, provider=self.provider,
                                           settings=self.settings)
        self.recovery = self._recover()
        self.kernel = CognitiveKernel(store=self.store, registry=self.tools, policy=self.policy,
                                      provider=self.provider, settings=self.settings, runtime_id=self.runtime_id,
                                      identity=self.identity)
        self._closed = False
        _log_info(_log, "JARVIS started", runtime_id=self.runtime_id, version=__version__,
                  schema_version=self.store.schema_version)

    # ------------------------------------------------------------------ lifecycle
    def _recover(self) -> dict[str, Any]:
        interrupted = self.store.operations.recover_interrupted(current_runtime=self.runtime_id)
        cutoff = to_iso(self.store.now() - timedelta(seconds=self.settings.provider_timeout + 120))
        with self.store.write() as db:
            stale = db.execute("UPDATE cycles SET status='interrupted', finished_at=?, error_type='Interrupted' "
                               "WHERE status='running' AND coalesce(runtime_instance,'') <> ? AND started_at < ?",
                               (to_iso(self.store.now()), self.runtime_id, cutoff)).rowcount
        purged_events = self.store.events.purge_expired()
        purged_memories = self.store.memory.purge_expired()
        interrupted_plans = self.plans.recover_interrupted()
        # Unfinished objectives become `interrupted` (never silently resumed or marked done);
        # the owner decides whether to continue them.
        objective_state = self.objectives.resume_after_restart()
        purged_audio = self.audio.purge_expired()
        report = {"interrupted_operations": [o["id"] for o in interrupted], "interrupted_cycles": stale,
                  "purged_expired_events": purged_events, "purged_expired_memories": purged_memories,
                  "interrupted_plans": interrupted_plans, "purged_audio_sessions": purged_audio,
                  "interrupted_objectives": objective_state["interrupted"],
                  "unfinished_objectives": [o["id"] for o in objective_state["unfinished"]]}
        if interrupted or stale or purged_events or purged_memories or interrupted_plans \
                or objective_state["interrupted"]:
            self.store.events.append("system.recovered", actor="system", target=self.runtime_id, payload=report)
        if purged_events:
            # Expired (e.g. affect) events were deleted; truncate the WAL so their page images
            # do not linger in the -wal file (secure_delete already zeroes the freed cells).
            report["wal_truncated"] = not self.store.checkpoint()["checkpoint_busy"]
        if interrupted or stale:
            _log_warning(_log, "Recovery: interrupted operations/cycles found",
                         interrupted_ops=len(interrupted), interrupted_cycles=stale)
        return report

    def close(self) -> None:
        if not self._closed:
            self.store.state.end_runtime(self.runtime_id, "shutdown")
            self._closed = True

    def __enter__(self) -> "Jarvis":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ interaction
    def ask(self, message: str, *, execute_proposed_tool: bool = False, channel: str = "cli",
            session_id: str | None = None) -> dict[str, Any]:
        """Run one cognitive cycle. ``execute_proposed_tool`` lets model-proposed *read-only*
        capabilities run immediately; writes proposed by a model always wait for approval."""
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Message cannot be empty")
        result = self.kernel.process(message, channel=channel, session_id=session_id,
                                     execute_model_reads=execute_proposed_tool)
        return result.to_dict()

    def context(self, message: str) -> dict[str, Any]:
        """v0.2 helper: a bounded snapshot of state relevant to ``message``."""
        return {"system": self.store.status(), "projects": self.store.list_projects(),
                "tasks": self.store.list_tasks()[:30], "memories": self.store.search_memories(message, limit=8),
                "tools": self.tools.descriptions()}

    def capabilities(self) -> dict[str, Any]:
        return capability_report(registry=self.tools, store=self.store, policy=self.policy, provider=self.provider,
                                 settings=self.settings)

    def state(self) -> dict[str, Any]:
        """Operational visibility: what JARVIS is doing and why (no private content)."""
        focus_id = self.store.state.get("focus.goal_id")
        last_id = self.store.last_cycle_id()
        last = self.store.cycle(last_id) if last_id else None
        return {
            "identity": {k: self.identity[k] for k in ("system_id", "name", "charter_version", "created_at")},
            "version": __version__, "runtime_id": self.runtime_id, "schema_version": self.store.schema_version,
            "focus_goal_id": focus_id,
            "goals": [{"id": g["id"], "title": g["title"], "status": g["status"]}
                      for g in self.store.goals.list_goals(("active", "proposed"))],
            "operations_awaiting_approval": [{"id": o["id"], "capability": o["capability"]}
                                             for o in self.store.operations.list(status="proposed", limit=20)],
            "operations_interrupted": [{"id": o["id"], "capability": o["capability"]}
                                       for o in self.store.operations.list(status="interrupted", limit=20)],
            "last_cycle": None if not last else {
                "id": last["id"], "status": last["status"], "intent": last["intent"], "mode": last["mode"],
                "stages": [{"name": s["name"], "status": s["status"], "duration_ms": s["duration_ms"]}
                           for s in (last["stages"] or [])],
                "memory_ids": last["memory_ids"], "operation_ids": last["operation_ids"],
                "provider": last["provider"], "provider_outcome": last["provider_outcome"],
                "error_stage": last["error_stage"], "error_type": last["error_type"]},
            "recovery_at_startup": self.recovery,
            "provider": None if self.provider is None else {"name": self.provider.name, "model": self.provider.model},
            "subsystems": {
                "clients": len(self.auth.list_clients()),
                "reminders_pending": len(self.reminders.list_due()),
                "plans": {status: len(self.plans.list_plans(status=status, limit=200))
                          for status in ("proposed", "active", "awaiting_approval", "completed", "failed", "cancelled")},
                "objectives": {outcome: len(self.objectives.list(outcome=outcome, limit=200))
                               for outcome in ("completed", "partially_completed", "failed", "blocked",
                                               "needs_approval", "cancelled", "awaiting_information")},
                "egress": {"default": "deny", "attempts": GUARD.attempts, "denials": GUARD.denials},
            },
            "integrity": self.store.integrity(),
        }
