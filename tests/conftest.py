"""Shared fixtures. Integration tests use real SQLite files, real threads, real HTTP and
real subprocesses; only the language model is replaced by a local fake server or a
scripted provider (never a mock of JARVIS's own subsystems)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from jarvis.config import Settings
from jarvis.core import Jarvis

ROOT = Path(__file__).resolve().parents[1]
FAKE_GEMINI_KEY = "AIza" + "TestOnlyKey0123456789abcdefghijklmnop"[:35]


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    base = dict(home=tmp_path / "home", workspace=workspace, network_enabled=False, gemini_api_key="",
                gemini_model="gemini-test", host="127.0.0.1", port=8765, log_level="INFO")
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings_factory(tmp_path):
    return lambda **kw: make_settings(tmp_path, **kw)


@pytest.fixture
def app_factory(tmp_path):
    created: list[Jarvis] = []

    def factory(**kw: Any) -> Jarvis:
        provider = kw.pop("model_provider", None)
        clock = kw.pop("clock", None)
        use_fts5 = kw.pop("use_fts5", None)
        app = Jarvis(make_settings(tmp_path, **kw), provider=provider, clock=clock, use_fts5=use_fts5)
        created.append(app)
        return app

    yield factory
    for app in created:
        app.close()


class FakeModelServer:
    """A local HTTP server that imitates Gemini and OpenAI-compatible endpoints."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.reply = "Hello from the fake model."
        self.delay = 0.0
        self.requests: list[dict[str, Any]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode()
                server.requests.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(body)})
                if server.delay:
                    time.sleep(server.delay)
                if server.mode == "error500":
                    self._raw(500, b'{"error":{"message":"internal"}}')
                    return
                if server.mode == "malformed":
                    self._raw(200, b"<html>not json</html>")
                    return
                if server.mode == "blocked":
                    self._json({"promptFeedback": {"blockReason": "SAFETY"}})
                    return
                if "chat/completions" in self.path:
                    self._json({"choices": [{"message": {"role": "assistant", "content": server.reply},
                                             "finish_reason": "stop"}], "usage": {"total_tokens": 5}})
                else:
                    self._json({"candidates": [{"content": {"parts": [{"text": server.reply}]},
                                                "finishReason": "STOP"}]})

            def _json(self, payload: dict[str, Any]) -> None:
                self._raw(200, json.dumps(payload).encode())

            def _raw(self, status: int, raw: bytes) -> None:
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def gemini_base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1beta"

    @property
    def openai_base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake_model():
    server = FakeModelServer()
    yield server
    server.close()


def run_cli(home: Path, *args: str, env_extra: dict[str, str] | None = None, input_text: str | None = None,
            timeout: float = 60) -> subprocess.CompletedProcess:
    """Run the real CLI in a separate process (a real restart for each call)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_")}
    env.update({"JARVIS_HOME": str(home), "JARVIS_WORKSPACE": str(home), "PYTHONPATH": str(ROOT / "src")})
    env.update(env_extra or {})
    home.mkdir(parents=True, exist_ok=True)
    return subprocess.run([sys.executable, "-m", "jarvis", *args], cwd=home, env=env, input=input_text,
                          capture_output=True, text=True, timeout=timeout)


def db_bytes(home: Path) -> bytes:
    """Raw bytes of the database and its WAL/SHM files (for secret/erasure scanning)."""
    data = b""
    for path in sorted(home.glob("jarvis.sqlite3*")):
        data += path.read_bytes()
    return data
