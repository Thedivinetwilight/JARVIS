from pathlib import Path
import pytest
from jarvis.config import Settings
from jarvis.core import Jarvis
from jarvis.db import Store
from jarvis.security import Policy
from jarvis.tools import ToolError, ToolRegistry, safe_calculate

def settings(tmp_path):
    return Settings(home=tmp_path / "home", workspace=tmp_path / "workspace",
                    network_enabled=False, gemini_api_key="", gemini_model="gemini-2.5-flash",
                    host="127.0.0.1", port=8765, log_level="INFO")

def test_project_task_memory_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.add_project("AXIOM", "paper trading system")
    task = store.add_task("Verify sandbox", project="AXIOM", priority=1)
    assert store.list_tasks()[0]["title"] == "Verify sandbox"
    assert store.set_task_status(task["id"], "done")["status"] == "done"
    store.remember("broker_mode", "paper", category="AXIOM")
    assert store.search_memories("paper")[0]["value"] == "paper"
    assert store.status()["projects"] == 1

def test_duplicate_project_rejected(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.add_project("AXIOM")
    with pytest.raises(Exception):
        store.add_project("axiom")

@pytest.mark.parametrize(("expr", "expected"), [("2+3*4",14), ("(10-4)/2",3), ("2**3",8), ("-5+2",-3)])
def test_safe_calculator(expr, expected):
    assert safe_calculate(expr) == expected

@pytest.mark.parametrize("expr", ["__import__('os')", "2**1000", "1/0", "[1,2]", ""])
def test_calculator_rejects_unsafe_or_invalid(expr):
    with pytest.raises((ToolError, ValueError)):
        safe_calculate(expr)

def test_write_tool_requires_explicit_intent(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    registry = ToolRegistry(store, tmp_path, Policy(network_enabled=False))
    with pytest.raises(ToolError):
        registry.execute("memory.save", {"key":"x","value":"y","category":"test"})
    result = registry.execute("memory.save", {"key":"x","value":"y","category":"test"}, explicitly_requested=True)
    assert result["value"] == "y"

def test_path_traversal_rejected(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry(store, workspace, Policy())
    with pytest.raises(ToolError):
        registry.execute("workspace.list", {"relative_path":"../../"}, explicitly_requested=True)

def test_offline_core_works(tmp_path):
    app = Jarvis(settings(tmp_path))
    assert app.ask("hello")["mode"] == "offline"
    assert app.provider is None
