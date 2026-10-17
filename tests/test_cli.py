"""The real CLI in separate processes (each call is a fresh start of the application)."""
from __future__ import annotations

import json

from conftest import run_cli


def test_python_m_jarvis_entry_point_and_v02_commands(tmp_path):
    home = tmp_path / "home"
    assert json.loads(run_cli(home, "init").stdout)["initialized"] is True
    assert run_cli(home, "project", "add", "AXIOM", "paper trading").returncode == 0
    task = json.loads(run_cli(home, "task", "add", "Inspect failure", "--project", "AXIOM").stdout)
    done = json.loads(run_cli(home, "task", "status", str(task["id"]), "done").stdout)
    assert done["status"] == "done"
    assert json.loads(run_cli(home, "memory", "save", "broker_mode", "paper", "--category", "AXIOM").stdout)["value"] == "paper"
    hits = json.loads(run_cli(home, "memory", "search", "what is the AXIOM broker mode").stdout)
    assert hits and hits[0]["value"] == "paper"
    status = json.loads(run_cli(home, "status").stdout)
    assert status["projects"] == 1 and status["schema_version"] == 2


def test_chat_repl_and_trace(tmp_path):
    home = tmp_path / "home"
    chat = run_cli(home, "chat", input_text="idea: a voice interface via Termux\nwhere were we?\n/quit\n")
    assert chat.returncode == 0, chat.stderr
    assert "Captured idea #1" in chat.stdout and "Recent ideas: #1" in chat.stdout
    trace = json.loads(run_cli(home, "trace").stdout)
    assert trace["cycle"]["intent"] == "resume" and trace["events"]


def test_memory_forget_requires_yes(tmp_path):
    home = tmp_path / "home"
    run_cli(home, "memory", "save", "locker", "locker code hint: grandma's street")
    pending = json.loads(run_cli(home, "memory", "forget", "1").stdout)
    assert pending["status"] == "proposed"
    approved = json.loads(run_cli(home, "approve", str(pending["operation_id"])).stdout)
    assert approved["status"] == "succeeded" and approved["verified"] is True
    assert json.loads(run_cli(home, "memory", "search", "locker").stdout) == []


def test_doctor_capabilities_and_export(tmp_path):
    home = tmp_path / "home"
    doctor = json.loads(run_cli(home, "doctor").stdout)
    assert doctor["integrity"]["quick_check"] == "ok" and doctor["configuration"]["gemini_api_key"] == "unset"
    caps = json.loads(run_cli(home, "capabilities").stdout)
    assert caps["interfaces"]["voice"]["status"] == "unavailable" and "vision.images" in caps["not_implemented"]
    run_cli(home, "ask", "remember that exports work")
    out = tmp_path / "export.json"
    assert run_cli(home, "memory", "export", "--out", str(out)).returncode == 0
    data = json.loads(out.read_text())
    assert data["format"] == "jarvis-export-v1" and data["memories"][0]["value"] == "exports work"


def test_invalid_configuration_is_reported_without_values(tmp_path):
    proc = run_cli(tmp_path / "home", "status", env_extra={"JARVIS_AUTONOMY_LEVEL": "5"})
    assert proc.returncode != 0 and "not implemented" in proc.stderr
    proc = run_cli(tmp_path / "home", "status", env_extra={"JARVIS_PORT": "secret-ish-value"})
    assert proc.returncode != 0 and "JARVIS_PORT" in proc.stderr and "secret-ish-value" not in proc.stderr


def test_serve_refuses_non_loopback(tmp_path):
    proc = run_cli(tmp_path / "home", "serve", "--host", "0.0.0.0", "--port", "8799")
    assert proc.returncode != 0 and "Refusing non-loopback bind" in proc.stderr


def test_export_is_owner_only_and_doctor_reports_permissions(tmp_path):
    import os
    import stat

    import pytest
    if os.name != "posix":
        pytest.skip("POSIX permissions")
    home, out = tmp_path / "home", tmp_path / "export.json"
    run_cli(home, "memory", "save", "pump current", "2A at 12V")
    assert run_cli(home, "memory", "export", "--out", str(out)).returncode == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o600                     # an export is a full copy of personal data
    doctor = json.loads(run_cli(home, "doctor").stdout)
    assert doctor["permissions"]["database"] == "0o600" and doctor["permissions"]["home"]
