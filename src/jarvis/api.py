"""Loopback-only JSON HTTP API (standard library ``http.server``).

Security contract (single-user, local developer service; see docs/SECURITY.md):
- the server refuses to bind to non-loopback addresses (enforced in cli.py);
- every route except ``GET /health`` must carry ``Authorization: Bearer <token>`` (HEAD,
  PATCH, PUT, DELETE and OPTIONS are refused without returning data or acting). The
  token lives in ``<JARVIS_HOME>/api_token`` (mode 0600), so API access requires the same
  OS-level access as the database file itself. Loopback alone is not an access control:
  other local users and, on Android, other apps can connect to 127.0.0.1;
- ``Host`` must be a loopback name (defeats DNS-rebinding attacks);
- if an ``Origin`` header is present it must be a loopback origin (blocks
  cross-site requests from web pages the user happens to visit);
- POST bodies must be ``application/json`` (a cross-site "simple" request cannot
  send that content type without a CORS preflight, which this server never grants);
- request bodies are size-limited; responses are ``no-store``; request logging is
  disabled so URLs and bodies are not written to the console;
- model-proposed operations are never executed by ``/v1/ask``; they wait for
  ``POST /v1/operations/<id>/approve`` (which also requires the token).
There is no TLS: do not expose this service beyond the local machine.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__
from .contracts import MemoryQuery, to_dict
from .redact import redact_text
from .tools import ToolError, UnknownOperation

if TYPE_CHECKING:  # pragma: no cover
    from .core import Jarvis

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
MAX_BODY = 16384
TOKEN_FILE_NAME = "api_token"
MIN_TOKEN_CHARS = 32


def read_api_token(home: Path) -> str | None:
    """The existing local API token, if any (never creates one)."""
    try:
        token = (Path(home) / TOKEN_FILE_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token if len(token) >= MIN_TOKEN_CHARS else None


def load_or_create_token(home: Path) -> tuple[str, Path]:
    """Return ``(token, path)``, creating a random token readable only by this OS user."""
    path = Path(home) / TOKEN_FILE_NAME
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    if len(token) < MIN_TOKEN_CHARS:
        token = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
        os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)  # also tightens a pre-existing file (best effort on non-POSIX systems)
    except OSError:
        pass
    return token, path


def _host_only(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        return value[: value.find("]") + 1] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def make_handler(app: "Jarvis", *, token: str):
    if not isinstance(token, str) or len(token) < 16:
        raise ValueError("make_handler requires the local API token")
    expected = token.encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"JARVIS/{__version__}"

        def log_message(self, fmt, *args):  # noqa: D401 - avoid logging potentially sensitive URLs
            return

        def _send(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
            raw = json.dumps(payload, ensure_ascii=False, default=str).encode()
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def _origin_ok(self) -> bool:
            host = _host_only(self.headers.get("Host", ""))
            if host not in LOOPBACK_HOSTS:
                self._send(403, {"error": "forbidden_host"})
                return False
            origin = self.headers.get("Origin")
            if origin is not None:
                parsed = urllib.parse.urlparse(origin)
                if parsed.scheme not in {"http", "https"} or (parsed.hostname or "") not in LOOPBACK_HOSTS - {"[::1]"}:
                    self._send(403, {"error": "forbidden_origin"})
                    return False
            return True

        def _authorised(self, method: str, path: str) -> bool:
            """Host/Origin checks first (403), then the bearer token (401) for everything but /health."""
            if not self._origin_ok():
                return False
            if method == "GET" and path == "/health":
                return True
            scheme, _, supplied = self.headers.get("Authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip().encode(), expected):
                self._send(401, {"error": "unauthorized"}, {"WWW-Authenticate": 'Bearer realm="jarvis"'})
                return False
            return True

        def _read_json(self) -> Any:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._send(415, {"error": "content_type_must_be_application_json"})
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "invalid_content_length"})
                return None
            if not 0 < length <= MAX_BODY:
                self._send(413, {"error": "body_size_invalid"})
                return None
            try:
                return json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send(400, {"error": "invalid_json"})
                return None

        def do_GET(self):  # noqa: N802
            url = urllib.parse.urlparse(self.path)
            if not self._authorised("GET", url.path):
                return
            query = urllib.parse.parse_qs(url.query)
            path = url.path
            try:
                if path == "/health":
                    return self._send(200, {"ok": True, "version": __version__, "mode": "local"})
                if path == "/v1/status":
                    return self._send(200, app.store.status())
                if path == "/v1/projects":
                    return self._send(200, app.store.list_projects())
                if path == "/v1/tasks":
                    return self._send(200, app.store.list_tasks())
                if path == "/v1/state":
                    return self._send(200, app.state())
                if path == "/v1/goals":
                    return self._send(200, [app.store.goals.progress(g["id"])
                                            for g in app.store.goals.list_goals(("active", "proposed"))])
                if path == "/v1/capabilities":
                    return self._send(200, app.capabilities())
                if path == "/v1/operations":
                    status = (query.get("status") or [None])[0]
                    return self._send(200, app.store.operations.list(status=status, limit=100))
                if path == "/v1/events":
                    limit = int((query.get("limit") or ["50"])[0])
                    return self._send(200, app.store.events.recent(limit, include_sensitive=False))
                if path == "/v1/memory/search":
                    text = (query.get("q") or [""])[0]
                    if not text.strip():
                        return self._send(400, {"error": "q_required"})
                    return self._send(200, [to_dict(h) for h in app.store.memory.search(MemoryQuery(text=text[:500]))])
                match = re.fullmatch(r"/v1/cycles/([A-Za-z0-9_]{1,80})", path)
                if match:
                    cycle = app.store.cycle(match.group(1))
                    if not cycle:
                        return self._send(404, {"error": "not_found"})
                    return self._send(200, {"cycle": cycle, "events": [
                        {k: e[k] for k in ("id", "ts", "kind", "actor", "target", "decision", "operation_id",
                                           "memory_id", "task_id", "goal_id")}
                        for e in app.store.events.for_cycle(match.group(1))]})
                return self._send(404, {"error": "not_found"})
            except ValueError as exc:
                return self._send(400, {"error": "bad_request", "detail": redact_text(str(exc))[:200]})
            except Exception as exc:  # noqa: BLE001
                return self._send(500, {"error": "internal_error", "type": type(exc).__name__})

        def do_POST(self):  # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            if not self._authorised("POST", path):
                return
            approve = re.fullmatch(r"/v1/operations/(\d{1,12})/(approve|deny)", path)
            if path != "/v1/ask" and not approve:
                return self._send(404, {"error": "not_found"})
            data = self._read_json()
            if data is None:
                return
            try:
                if approve:
                    op_id = int(approve.group(1))
                    outcome = (app.tools.approve(op_id, approved_by="user:api") if approve.group(2) == "approve"
                               else app.tools.deny(op_id))
                    return self._send(200, to_dict(outcome))
                if not isinstance(data, dict) or not isinstance(data.get("message"), str) or not data["message"].strip():
                    return self._send(400, {"error": "message_required"})
                if len(data["message"]) > 8000:
                    return self._send(413, {"error": "message_too_long"})
                session_id = data.get("session_id") if isinstance(data.get("session_id"), str) else None
                # The API never auto-executes model-proposed tools.
                return self._send(200, app.ask(data["message"], execute_proposed_tool=False, channel="api",
                                               session_id=session_id))
            except UnknownOperation:
                return self._send(404, {"error": "operation_not_found"})
            except ToolError as exc:
                return self._send(400, {"error": "bad_request", "detail": redact_text(str(exc))[:200]})
            except ValueError as exc:
                return self._send(400, {"error": "bad_request", "detail": redact_text(str(exc))[:200]})
            except Exception as exc:  # noqa: BLE001
                return self._send(500, {"error": "internal_error", "type": type(exc).__name__})

        def do_PUT(self):  # noqa: N802
            return self._send(405, {"error": "method_not_allowed"})

        def do_DELETE(self):  # noqa: N802
            return self._send(405, {"error": "method_not_allowed"})

        def do_OPTIONS(self):  # noqa: N802 - never grant CORS preflights
            return self._send(405, {"error": "method_not_allowed"})

    return Handler
