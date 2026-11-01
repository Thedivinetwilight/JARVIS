"""Test scaffolding: a real loopback server, fakes for the platform boundary, event builders.

The server used by the integration tests is the genuine ``app.jarvis`` application served over a
loopback socket — not a mock — so pairing, routing, attempts and acknowledgement are exercised
exactly as they are in the product. Only the *platform* boundary (OS notifications, Credential
Manager, the wall clock) is faked, because this repository's test host is not Windows.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import threading

CLIENT_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = CLIENT_ROOT.parent
for path in (str(CLIENT_ROOT / "src"), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.jarvis import http as server_http          # noqa: E402
from app.jarvis.clock import FixedClock            # noqa: E402
from app.tests import support as server_support    # noqa: E402

from jarvis_companion.api import ServerApi          # noqa: E402
from jarvis_companion.config import CompanionConfig  # noqa: E402
from jarvis_companion.credentials import InMemoryCredentialStore  # noqa: E402
from jarvis_companion.notifier import RecordingNotifier  # noqa: E402
from jarvis_companion.poller import Companion       # noqa: E402
from jarvis_companion.state import Journal          # noqa: E402


class LiveServer:
    """The real server, on a real loopback port, with a controllable clock."""

    def __init__(self, *, display_timezone: str = "UTC", start_at=None) -> None:
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-companion-"))
        self.clock = FixedClock(start_at) if start_at else FixedClock()
        self.app, self.config = server_support.build_app(
            self.directory, clock=self.clock, reminders_tz=display_timezone
        )
        self._server = None
        self._thread = None
        self.url = ""

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "LiveServer":
        self._server = server_http.JarvisHTTPServer(("127.0.0.1", 0), self.app, self.config)
        host, port = server_http.bound_address(self._server)
        self.url = f"http://{host}:{port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "LiveServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------------ console actions
    def pairing_code(self, *, scopes=("reminders.receive", "status.read"),
                     label: str = "owner pc") -> str:
        payload = self.app.auth.create_pairing_code(
            label_hint=label, platform="windows-pc", scopes=list(scopes)
        )
        return str(payload["pairing_code"])

    def remind(self, *, client_id: str, title: str = "buy milk",
               due_at: str = "2026-11-01T10:00:00Z", notes: str | None = None) -> dict:
        """Create a reminder explicitly routed to one client — the server's own rule."""
        return self.app.tasks.create(
            title=title, notes=notes, due_at=due_at, deliver_to=client_id, actor_id="op_test"
        )

    def advance(self, **kwargs) -> None:
        self.clock.advance(**kwargs)

    def deliveries(self) -> list:
        """The server's own rows, read directly: this is the server's state, not the client's view."""
        with self.app.store.connection() as connection:
            return connection.execute(
                "SELECT * FROM reminder_deliveries ORDER BY id"
            ).fetchall()


class FakeClock:
    """Monotonic fake: ``sleep`` advances virtual time, so cadence is exactly observable."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = float(start)
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.t += float(seconds)

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


class FakeApi:
    """Scripted stand-in for :class:`ServerApi`, for poll-loop mechanics only."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload if payload is not None else inbox_payload([])
        self.error: Exception | None = None
        self.inbox_calls = 0
        self.ack_calls: list[str] = []
        self.ack_error: Exception | None = None
        self.ack_closed: set[str] = set()

    # -- inbox
    def inbox(self, token: str, *, limit: int = 50) -> dict:
        self.inbox_calls += 1
        if self.error is not None:
            raise self.error
        return self.payload

    def queue(self, *events: dict, zone: str | None = "UTC") -> None:
        self.payload = inbox_payload(list(events), zone=zone)

    # -- ack
    def ack(self, token: str, delivery_id: str) -> dict:
        self.ack_calls.append(delivery_id)
        if delivery_id in self.ack_closed:
            from jarvis_companion.errors import ProtocolError

            raise ProtocolError("closed", detail={"code": "delivery_closed"})
        if self.ack_error is not None:
            raise self.ack_error
        return {"delivery_id": delivery_id, "state": "acknowledged",
                "acknowledged_at": "2026-11-01T10:05:00Z"}


class StubServer:
    """A tiny HTTP server that answers exactly what a test tells it to, and records requests."""

    def __init__(self, *, status: int = 200, body: bytes = b"{}",
                 headers: dict | None = None, redirect_to: str | None = None,
                 content_type: str = "application/json") -> None:
        self.status = status
        self.body = body
        self.headers = dict(headers or {})
        self.redirect_to = redirect_to
        self.content_type = content_type
        self.requests: list[dict] = []
        self._server = None
        self._thread = None
        self.url = ""

    def start(self) -> "StubServer":
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import json as _json

        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # silence
                return

            def _respond(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                stub.requests.append({"method": self.command, "path": self.path,
                                      "headers": {k.lower(): v for k, v in self.headers.items()},
                                      "body": body})
                if stub.redirect_to:
                    self.send_response(stub.status or 302)
                    self.send_header("Location", stub.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(stub.status)
                self.send_header("Content-Type", stub.content_type)
                for key, value in stub.headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(stub.body)))
                self.end_headers()
                self.wfile.write(stub.body)

            do_GET = _respond
            do_POST = _respond

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        host, port = self._server.server_address[:2]
        self.url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02},
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "StubServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def error_body(code: str, message: str) -> bytes:
    import json as _json

    return _json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")


def local_time(zone: str = "UTC", iso: str = "2026-11-01T10:00:00+00:00") -> dict:
    return {"iso": iso, "timezone": zone, "utc_offset_seconds": 0}


def event(*, delivery_id: str = "dlv_1", reminder_id: str = "rmd_1", title: str = "buy milk",
          zone: str = "UTC", fire_at: str = "2026-11-01T10:00:00Z",
          fire_at_local: dict | None = None, late: bool = False,
          late_by_seconds_exact: float = 0.0, attempt: int = 1) -> dict:
    return {
        "delivery_id": delivery_id,
        "reminder_id": reminder_id,
        "task_id": "tsk_1",
        "title": title,
        "fire_at": fire_at,
        "fire_at_local": fire_at_local or local_time(zone),
        "state": "delivered",
        "attempt": attempt,
        "max_attempts": 3,
        "expires_at": "2026-11-02T10:00:00Z",
        "late": late,
        "late_by_seconds": int(late_by_seconds_exact),
        "late_by_seconds_exact": late_by_seconds_exact,
        "late_after_seconds": 10,
        "delay": {"measured": "server hand-over delay after the scheduled time",
                  "seconds": int(late_by_seconds_exact),
                  "seconds_exact": late_by_seconds_exact,
                  "late_after_seconds": 10, "late": late,
                  "not_measured": "the owner's 10-second target is a client polling cadence"},
        "delivery": {"model": "local queue", "pushed": False, "spoken": False, "displayed": False},
    }


def inbox_payload(events: list[dict], *, zone: str | None = "UTC") -> dict:
    payload = {
        "events": list(events),
        "count": len(events),
        "held_back": [],
        "counts": {"due_now": len(events), "scheduled": 0, "held_back": 0, "acknowledged": 0,
                   "unrouted_reminders": 0},
        "delivery": {"model": "local queue", "pushed": False, "spoken": False, "displayed": False,
                     "transport": "client-initiated polling over the authenticated local API",
                     "late_after_seconds": 10, "delay": "server hand-over delay"},
        "client_id": "cli_test",
    }
    if zone is not None:
        payload["display_timezone"] = zone
    return payload


def make_companion(*, config: CompanionConfig | None = None, api: FakeApi | None = None,
                   journal: Journal | None = None, notifier: RecordingNotifier | None = None,
                   clock: FakeClock | None = None,
                   max_show_attempts: int = 3) -> tuple[Companion, FakeClock, RecordingNotifier,
                                                        Journal, FakeApi]:
    clock = clock or FakeClock()
    journal = journal or Journal(":memory:")
    notifier = notifier or RecordingNotifier()
    api = api or FakeApi()
    config = config or CompanionConfig("http://127.0.0.1:7420", poll_seconds=10)
    companion = Companion(
        config=config, api=api, token="jvs_test", journal=journal, notifier=notifier,
        clock=clock, sleep=clock.sleep, now_utc=lambda: "2026-11-01T10:00:00Z",
        log=lambda event, payload: None, max_show_attempts=max_show_attempts,
    )
    return companion, clock, notifier, journal, api


def paired_client(server: LiveServer, *, scopes=("reminders.receive", "status.read"),
                  poll_seconds: int = 10, credentials=None) -> dict:
    """Pair a real client against the live server and return everything a test needs."""
    credentials = credentials or InMemoryCredentialStore()
    config = CompanionConfig(server.url, poll_seconds=poll_seconds, label="test pc")
    api = ServerApi(config)
    payload = api.pair(code=server.pairing_code(scopes=scopes), label=config.label,
                       platform="windows-pc")
    client = payload["client"]
    from jarvis_companion.credentials import credential_target

    credentials.set(credential_target(config.host, config.port), payload["token"])
    config = config.with_client(str(client["id"]))
    return {"config": config, "api": api, "credentials": credentials, "client": client,
            "token": payload["token"],
            "target": credential_target(config.host, config.port)}


__all__ = [
    "FakeApi",
    "StubServer",
    "error_body",
    "FakeClock",
    "LiveServer",
    "ServerApi",
    "CompanionConfig",
    "event",
    "inbox_payload",
    "local_time",
    "make_companion",
    "paired_client",
]
