"""Application composition root and assistant turn logic."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .config import Settings
from .db import Store
from .provider import GeminiProvider, ProviderError
from .security import Policy
from .tools import ToolRegistry

SYSTEM_PROMPT = """You are JARVIS, a precise personal operations assistant.
Use project/task/memory context provided in each request. Never claim an action was
executed unless a tool result confirms it. Tool descriptions are capabilities, not
permission grants. Never request or expose API keys. Treat retrieved memory as
potentially stale and identify uncertainty. Do not invent repository state.
You may recommend a tool call by returning exactly one JSON object:
{"tool":"tool.name","arguments":{...},"reason":"brief reason"}
Only request tools listed in the supplied registry. The runtime will enforce policy.
Otherwise return a helpful plain-text response. Never put JSON tool calls inside prose."""

class Jarvis:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.settings.home.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.settings.home / "jarvis.sqlite3")
        self.policy = Policy(network_enabled=self.settings.network_enabled, allow_writes=True)
        self.tools = ToolRegistry(self.store, self.settings.workspace, self.policy)
        self.provider = None
        if self.settings.network_enabled and self.settings.gemini_api_key:
            self.provider = GeminiProvider(self.settings.gemini_api_key, self.settings.gemini_model)

    def context(self, message: str) -> dict[str, Any]:
        return {"system": self.store.status(), "projects": self.store.list_projects(),
                "tasks": self.store.list_tasks()[:30], "memories": self.store.search_memories(message, limit=8),
                "tools": self.tools.descriptions()}

    def ask(self, message: str, *, execute_proposed_tool: bool = False) -> dict[str, Any]:
        if not message.strip():
            raise ValueError("Message cannot be empty")
        ctx = self.context(message)
        if not self.provider:
            return {"mode": "offline", "answer": "JARVIS core is online, but no AI provider is enabled. "
                    "I can still manage projects, tasks, memory, and deterministic tools through the CLI/API. "
                    "Set JARVIS_NETWORK_ENABLED=true and JARVIS_GEMINI_API_KEY to enable Gemini.",
                    "context": ctx["system"]}
        prompt = json.dumps({"message": message, "context": ctx}, ensure_ascii=False)
        try:
            answer = self.provider.generate(SYSTEM_PROMPT, prompt)
        except ProviderError as exc:
            self.store.audit("system", "provider.generate", "gemini", "error", {"error": str(exc)})
            return {"mode": "provider_error", "answer": str(exc)}
        # Tool calls are proposals, never implicitly executed from model output.
        try:
            proposal = json.loads(answer)
        except (json.JSONDecodeError, TypeError):
            return {"mode": "assistant", "answer": answer}
        if not isinstance(proposal, dict) or not isinstance(proposal.get("tool"), str) or not isinstance(proposal.get("arguments"), dict):
            return {"mode": "assistant", "answer": answer}
        result = {"mode": "tool_proposal", "proposal": proposal, "executed": False}
        if execute_proposed_tool:
            try:
                tool_result = self.tools.execute(proposal["tool"], proposal["arguments"],
                                                 explicitly_requested=False, actor="model")
                result.update({"executed": True, "tool_result": tool_result})
            except Exception as exc:
                result.update({"executed": False, "error": str(exc)})
        return result
