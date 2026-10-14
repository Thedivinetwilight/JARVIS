"""Validated deterministic tools; deliberately no arbitrary shell execution."""
from __future__ import annotations
import ast
import operator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from .db import Store
from .security import Policy, Risk

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    risk: Risk
    handler: Callable[[dict[str, Any]], Any]
    schema: dict[str, type]

class ToolError(RuntimeError):
    pass

class ToolRegistry:
    def __init__(self, store: Store, workspace: Path, policy: Policy):
        self.store, self.workspace, self.policy = store, workspace.resolve(), policy
        self._tools: dict[str, Tool] = {}
        self._register_defaults()

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def descriptions(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "risk": t.risk,
                 "arguments": {k: v.__name__ for k, v in t.schema.items()}} for t in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any], *, explicitly_requested: bool = False,
                actor: str = "user") -> Any:
        tool = self._tools.get(name)
        if not tool:
            raise ToolError(f"Unknown tool: {name}")
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
        decision = self.policy.decide(name, tool.risk, explicitly_requested=explicitly_requested)
        self.store.audit(actor, f"tool.{name}", name, "allowed" if decision.allowed else "denied",
                         {"risk": tool.risk, "reason": decision.reason, "arguments": _safe_args(arguments)})
        if not decision.allowed:
            raise ToolError(decision.reason)
        try:
            result = tool.handler(arguments)
            self.store.audit(actor, f"tool.{name}.result", name, "success")
            return result
        except Exception as exc:
            self.store.audit(actor, f"tool.{name}.result", name, "error", {"error_type": type(exc).__name__})
            raise

    def _register_defaults(self) -> None:
        self.register(Tool("time.now", "Return current UTC time.", "read",
            lambda _: {"utc": datetime.now(timezone.utc).isoformat()}, {}))
        self.register(Tool("math.calculate", "Evaluate basic arithmetic expressions safely.", "read",
            lambda a: {"result": safe_calculate(a["expression"])}, {"expression": str}))
        self.register(Tool("project.list", "List tracked projects.", "read",
            lambda _: self.store.list_projects(), {}))
        self.register(Tool("task.list", "List tasks, optionally by status.", "read",
            lambda a: self.store.list_tasks(status=a["status"] or None), {"status": str}))
        self.register(Tool("memory.search", "Search persistent memory.", "read",
            lambda a: self.store.search_memories(a["query"]), {"query": str}))
        self.register(Tool("workspace.list", "List files beneath the configured workspace.", "read",
            self._list_files, {"relative_path": str}))
        self.register(Tool("task.create", "Create a task after explicit user request.", "write",
            lambda a: self.store.add_task(a["title"], project=a["project"] or None, description=a["description"], priority=a["priority"]),
            {"title": str, "project": str, "description": str, "priority": int}))
        self.register(Tool("memory.save", "Save a memory after explicit user request.", "write",
            lambda a: self.store.remember(a["key"], a["value"], category=a["category"], source="tool"),
            {"key": str, "value": str, "category": str}))

    def _list_files(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        base = (self.workspace / args["relative_path"]).resolve()
        if not base.is_relative_to(self.workspace):
            raise ToolError("Path escapes the configured workspace")
        if not base.exists():
            raise ToolError("Path does not exist")
        if not base.is_dir():
            raise ToolError("Path must be a directory")
        results = []
        for path in sorted(base.iterdir(), key=lambda p: p.name.lower())[:200]:
            results.append({"name": path.name, "type": "directory" if path.is_dir() else "file"})
        return results

def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    # Audit metadata must not become a second store for secrets or private content.
    sensitive = {"value", "message", "api_key", "token", "password", "secret"}
    safe: dict[str, Any] = {}
    for key, value in args.items():
        if key.lower() in sensitive:
            safe[key] = "[REDACTED]"
        elif isinstance(value, str) and len(value) > 200:
            safe[key] = value[:200] + "…"
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
