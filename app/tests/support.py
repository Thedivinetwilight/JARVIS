"""Shared fixtures for the application tests.

Every test gets its own temporary data directory and its own SQLite file, so nothing is
written inside the repository and no test can see another test's state.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from typing import Any

from app.jarvis.app import App, Request
from app.jarvis.clock import FixedClock
from app.jarvis.config import Config, load_config
from app.jarvis.db import Store
from app.jarvis.egress import GUARD

SECRET = "unit-test-secret-0123456789abcdefghijklmnop"
ENV = {"JARVIS_SERVER_SECRET": SECRET}

CONFIG_TEMPLATE = """\
[server]
bind = "127.0.0.1"
port = {port}
request_timeout_s = {timeout}
max_request_bytes = {max_body}

[storage]
data_dir = "{data_dir}"
database = "jarvis.sqlite3"
audio_retention_days = {retention}
max_audio_bytes_per_session = {max_session_bytes}
max_audio_chunk_bytes = {max_chunk_bytes}
audio_session_ttl_minutes = {session_ttl}

[auth]
device_token_ttl_days = {device_ttl}
operator_token_ttl_days = {operator_ttl}
pairing_code_ttl_minutes = {pairing_ttl}
pairing_max_failed_attempts = {max_attempts}
secret_file = ""

[external_services]
{external}

[adapters]
speech_asr = "{asr}"
speech_tts = "{tts}"

[reminders]
display_timezone = "{reminders_tz}"
late_after_seconds = {reminders_late_after}
max_delivery_attempts = {reminders_max_attempts}
retry_interval_minutes = {reminders_retry}
delivery_window_minutes = {reminders_window}

[logging]
level = "warning"
"""

DEFAULT_EXTERNAL = """\
web_retrieval = false
telephony = false
device_vendor_cloud = false
update_downloads = false"""


def write_config(directory: pathlib.Path, *, name: str = "jarvis.toml", **overrides: Any) -> pathlib.Path:
    values = {
        "port": 7420,
        "timeout": 5,
        "max_body": 65536,
        "data_dir": str(directory / "data"),
        "retention": 7,
        "max_session_bytes": 100000,
        "max_chunk_bytes": 10000,
        "session_ttl": 15,
        "device_ttl": 90,
        "operator_ttl": 30,
        "pairing_ttl": 10,
        "max_attempts": 5,
        "external": DEFAULT_EXTERNAL,
        "asr": "unavailable",
        "tts": "unavailable",
        "reminders_tz": "UTC",
        "reminders_late_after": 10,
        "reminders_max_attempts": 3,
        "reminders_retry": 5,
        "reminders_window": 1440,
    }
    values.update(overrides)
    path = directory / name
    path.write_text(CONFIG_TEMPLATE.format(**values), encoding="utf-8")
    return path


def load(directory: pathlib.Path, **overrides: Any) -> Config:
    path = overrides.pop("config_path", None) or write_config(directory, **overrides)
    return load_config(path, environ=dict(ENV))


def build_app(
    directory: pathlib.Path,
    *,
    migrate: bool = True,
    clock: FixedClock | None = None,
    **overrides: Any,
) -> tuple[App, Config]:
    config = load(directory, **overrides)
    store = Store(config.storage.database_path)
    if migrate:
        store.migrate()
    app = App(config, store=store, clock=clock or FixedClock(), install_guard=False)
    return app, config


class ApiTestCase(unittest.TestCase):
    """Base class: a migrated server, an operator token and helpers to call routes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = pathlib.Path(self._tmp.name)
        self.app, self.config = build_app(self.directory)
        self.clock: FixedClock = self.app.clock  # type: ignore[assignment]
        self.operator, self.operator_token = self.app.auth.create_operator("test-operator")

    # ------------------------------------------------------------------ helpers
    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: bytes | str | dict | None = None,
        headers: dict[str, str] | None = None,
        content_type: str | None = None,
    ):
        request_headers = dict(headers or {})
        payload: bytes
        if isinstance(body, dict):
            payload = json.dumps(body).encode("utf-8")
            request_headers.setdefault("content-type", "application/json")
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        elif isinstance(body, bytes):
            payload = body
        else:
            payload = b""
        if token:
            request_headers["authorization"] = f"Bearer {token}"
        if content_type:
            request_headers["content-type"] = content_type
        response = self.app.request(method, path, request_headers, payload)
        parsed = None
        if response.body:
            try:
                parsed = json.loads(response.body)
            except json.JSONDecodeError:  # pragma: no cover - all routes return JSON
                parsed = response.body
        return response, parsed

    def pair_device(self, *, scopes: list[str] | None = None, label: str = "test device",
                    platform: str = "tablet") -> tuple[dict, str]:
        info = self.app.auth.create_pairing_code(
            label_hint=label, platform=platform, scopes=scopes
        )
        response, payload = self.call(
            "POST", "/v1/pairing",
            body={"code": info["pairing_code"], "label": label, "platform": platform},
        )
        self.assertEqual(response.status, 201, payload)
        return payload["client"], payload["token"]

    def audit_entries(self, limit: int = 200) -> list[dict]:
        return [entry.to_dict() for entry in self.app.audit.tail(limit)]


def raw_request(app: App, method: str, path: str, headers: dict[str, str] | None = None,
                body: bytes = b"") -> Any:  # pragma: no cover - convenience
    return app.request(method, path, headers, body)


__all__ = [
    "ApiTestCase",
    "DEFAULT_EXTERNAL",
    "ENV",
    "SECRET",
    "build_app",
    "load",
    "raw_request",
    "write_config",
]
