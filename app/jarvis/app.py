"""The request pipeline: routing, authentication, authorisation, audit and the handlers.

``App.request`` is a pure function of (method, path, headers, body). The HTTP server is a
thin adapter over it, which means every security rule is testable without a socket.

Order of operations for every non-public route:

1. authenticate the bearer token (401 / 401-expired / 401-revoked);
2. check the route's scope against the token's grant (403);
3. check the route's manifest is servable (503 when the capability does not exist yet);
4. parse and bound the body (400 / 413 / 415);
5. run the handler;
6. write one audit record with the outcome.

There is no text-conversation route, and there is no route that forwards anything off the
machine: the only outbound path is the egress guard, which denies by default.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from . import manifests, reporting, speech
from .audio import AudioSessionService
from .audit import AuditLog
from .auth import AuthService, Client
from .capabilities import SPEC_VERSION
from .clock import Clock, SystemClock, to_iso
from .config import Config
from .db import Store
from .egress import GUARD, EgressGuard
from .errors import (
    BadRequest,
    JarvisError,
    MethodNotAllowed,
    NotFound,
    PayloadTooLarge,
    ScopeDenied,
    Unavailable,
    UnsupportedMediaType,
)
from .tasks import TaskService, summarise as summarise_tasks

LOGGER = logging.getLogger("jarvis.api")

ID_PATTERN = r"[A-Za-z0-9_]{3,64}"


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, status: int, payload, request_id: str | None = None) -> "Response":
        # Nothing this server returns may be cached: responses include tokens, task lists and
        # personal audio metadata. "no-store" also covers the pairing response.
        # Header names are lower case on purpose: HTTP header names are case-insensitive and
        # every in-process caller compares them lower case.
        headers = {"cache-control": "no-store", "x-content-type-options": "nosniff"}
        if request_id:
            headers["x-request-id"] = request_id
        return cls(status=status, body=json.dumps(payload, sort_keys=True).encode("utf-8"),
                   content_type="application/json", headers=headers)


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern
    name: str
    handler: str
    scope: str | None = None
    manifest: str | None = None
    public: bool = False
    body: str = "none"


@dataclass
class Context:
    request: Request
    request_id: str
    params: dict[str, str]
    query: dict[str, str]
    body: bytes
    payload: dict
    client: Client | None = None


def _route(
    method: str,
    pattern: str,
    name: str,
    handler: str,
    *,
    scope: str | None = None,
    manifest: str | None = None,
    public: bool = False,
    body: str = "none",
) -> Route:
    return Route(
        method=method,
        pattern=re.compile(pattern),
        name=name,
        handler=handler,
        scope=scope,
        manifest=manifest,
        public=public,
        body=body,
    )


ROUTES: tuple[Route, ...] = (
    # ------------------------------------------------------------------ public
    _route("GET", r"^/healthz$", "healthz", "_h_health", public=True),
    _route("GET", r"^/readyz$", "readyz", "_h_ready", public=True),
    _route("POST", r"^/v1/pairing$", "pairing.create", "_h_pair", public=True, body="json"),
    # ------------------------------------------------------------------ status
    _route("GET", r"^/v1/status$", "status", "_h_status", scope=manifests.SCOPE_STATUS_READ),
    _route("GET", r"^/v1/limitations$", "limitations", "_h_limitations",
           scope=manifests.SCOPE_STATUS_READ),
    _route("GET", r"^/v1/config$", "config", "_h_config", scope=manifests.SCOPE_STATUS_READ),
    _route("GET", r"^/v1/manifests$", "manifests", "_h_manifests",
           scope=manifests.SCOPE_MANIFESTS_READ),
    _route("GET", r"^/v1/egress/policy$", "egress.policy", "_h_egress",
           scope=manifests.SCOPE_STATUS_READ),
    _route("GET", r"^/v1/surfaces$", "surfaces", "_h_surfaces",
           scope=manifests.SCOPE_STATUS_READ),
    # ------------------------------------------------------------------ audio
    _route("GET", r"^/v1/audio/capabilities$", "audio.capabilities", "_h_audio_capabilities",
           scope=manifests.SCOPE_STATUS_READ),
    _route("GET", r"^/v1/audio/output$", "audio.output", "_h_audio_output",
           scope=manifests.SCOPE_SPEECH_OUTPUT, manifest="speech.tts"),
    _route("POST", r"^/v1/audio/sessions$", "audio.session.start", "_h_audio_start",
           scope=manifests.SCOPE_AUDIO_CAPTURE, manifest="audio.session"),
    _route("POST", rf"^/v1/audio/sessions/(?P<session_id>{ID_PATTERN})/audio$",
           "audio.session.audio", "_h_audio_append",
           scope=manifests.SCOPE_AUDIO_CAPTURE, manifest="audio.session", body="raw"),
    _route("POST", rf"^/v1/audio/sessions/(?P<session_id>{ID_PATTERN})/end$",
           "audio.session.end", "_h_audio_end",
           scope=manifests.SCOPE_AUDIO_CAPTURE, manifest="audio.session"),
    # ------------------------------------------------------------------ tasks
    _route("GET", r"^/v1/tasks$", "tasks.list", "_h_tasks_list",
           scope=manifests.SCOPE_TASKS_READ, manifest="tasks"),
    _route("POST", r"^/v1/tasks$", "tasks.create", "_h_tasks_create",
           scope=manifests.SCOPE_TASKS_WRITE, manifest="tasks", body="json"),
    _route("POST", rf"^/v1/tasks/(?P<task_id>{ID_PATTERN})/complete$", "tasks.complete",
           "_h_tasks_complete", scope=manifests.SCOPE_TASKS_WRITE, manifest="tasks"),
    _route("DELETE", rf"^/v1/tasks/(?P<task_id>{ID_PATTERN})$", "tasks.delete",
           "_h_tasks_delete", scope=manifests.SCOPE_TASKS_WRITE, manifest="tasks"),
    # ------------------------------------------------------------------ reminders
    _route("GET", r"^/v1/reminders/due$", "reminders.due", "_h_reminders_due",
           scope=manifests.SCOPE_REMINDERS_READ, manifest="reminders"),
    _route("POST", rf"^/v1/reminders/(?P<reminder_id>{ID_PATTERN})/ack$", "reminders.ack",
           "_h_reminder_ack", scope=manifests.SCOPE_REMINDERS_WRITE, manifest="reminders"),
    # ------------------------------------------------------------------ clients
    _route("GET", r"^/v1/clients$", "clients.list", "_h_clients_list",
           scope=manifests.SCOPE_CLIENTS_READ),
    _route("GET", r"^/v1/clients/me$", "clients.me", "_h_client_me",
           scope=manifests.SCOPE_STATUS_READ),
    _route("POST", rf"^/v1/clients/(?P<client_id>{ID_PATTERN})/revoke$", "clients.revoke",
           "_h_client_revoke"),
    # ------------------------------------------------------------------ audit
    _route("GET", r"^/v1/audit$", "audit.list", "_h_audit", scope=manifests.SCOPE_AUDIT_READ),
)


class App:
    """Wires the services together and serves requests without a socket."""

    version = "0.1.0"

    def __init__(
        self,
        config: Config,
        *,
        store: Store | None = None,
        clock: Clock | None = None,
        guard: EgressGuard | None = None,
        install_guard: bool = True,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self.store = store or Store(config.storage.database_path)
        self.audit = AuditLog(self.store, self.clock)
        self.auth = AuthService(self.store, config, self.clock, self.audit)
        self.tasks = TaskService(self.store, self.clock, self.audit)
        self.audio = AudioSessionService(self.store, config, self.clock, self.audit)
        self.speech = speech.build_registry(config.adapters)
        self.guard = guard or GUARD
        self.guard_installed_here = self.guard.install() if install_guard else False
        self.started_at = self.clock.now()

    # ------------------------------------------------------------------ helpers
    @property
    def started_at_iso(self) -> str:
        return to_iso(self.started_at)

    def uptime_s(self) -> float:
        return round((self.clock.now() - self.started_at).total_seconds(), 3)

    def data_dir_writable(self) -> bool:
        probe = self.config.storage.data_dir / ".ready-probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:  # pragma: no cover - exercised via monkeypatched failure
            return False

    # ------------------------------------------------------------------ pipeline
    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> Response:
        method = (method or "GET").upper()
        headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
        request_id = self._request_id(headers)
        req = Request(method=method, path=path, headers=headers, body=body or b"")
        try:
            return self._dispatch(req, request_id)
        except JarvisError as exc:
            self._audit_failure(req, request_id, exc)
            return Response.json(exc.status, {"error": exc.to_dict(), "request_id": request_id},
                                 request_id)
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("unhandled error while serving %s %s", method, req.path.split("?")[0])
            self.audit.record(
                actor_kind="server", actor_id=None, action=f"{method} {req.path.split('?')[0]}",
                outcome="error", detail={"reason": "unhandled"}, request_id=request_id,
            )
            return Response.json(
                500,
                {"error": {"code": "internal_error", "message": "internal error"}, "request_id": request_id},
                request_id,
            )

    def _request_id(self, headers: dict[str, str]) -> str:
        supplied = headers.get("x-request-id", "")
        if supplied and re.fullmatch(r"[A-Za-z0-9._-]{8,64}", supplied):
            return supplied
        return f"req_{uuid.uuid4().hex[:16]}"

    def _dispatch(self, req: Request, request_id: str) -> Response:
        parts = urlsplit(req.path)
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        if path != "/" and path.endswith("/"):
            path = path[:-1]
        if ".." in path.split("/"):
            raise BadRequest("invalid path", code="invalid_path")
        query = {key: values[-1] for key, values in parse_qs(parts.query).items()}

        route = None
        allowed_methods: set[str] = set()
        for candidate in ROUTES:
            if candidate.pattern.match(path):
                allowed_methods.add(candidate.method)
                if candidate.method == req.method:
                    route = candidate
                    break
        if route is None:
            if allowed_methods:
                raise MethodNotAllowed(
                    f"method {req.method} is not allowed here; allowed: "
                    f"{', '.join(sorted(allowed_methods))}",
                    code="method_not_allowed",
                    detail={"allowed": sorted(allowed_methods)},
                )
            raise NotFound(f"no route for {req.method} {path}", code="route_not_found")

        if len(req.body) > self.config.server.max_request_bytes:
            raise PayloadTooLarge(
                f"request body exceeds {self.config.server.max_request_bytes} bytes",
                code="body_too_large",
            )

        client: Client | None = None
        if not route.public:
            client = self._authenticate(req, request_id)
            if route.scope:
                self.auth.require_scope(client, route.scope)
            if route.manifest:
                manifests.require_servable(route.manifest)
        elif req.body and route.body == "none":
            raise BadRequest("this endpoint takes no request body", code="body_not_allowed")

        payload: dict = {}
        if route.body == "json":
            payload = self._parse_json(req)
        elif route.body == "none" and req.body:
            raise BadRequest("this endpoint takes no request body", code="body_not_allowed")

        match = route.pattern.match(path)
        params = match.groupdict() if match else {}
        context = Context(
            request=req,
            request_id=request_id,
            params=params,
            query=query,
            body=req.body,
            payload=payload,
            client=client,
        )
        handler: Callable[[Context], tuple[int, dict]] = getattr(self, route.handler)
        status, data = handler(context)
        self.audit.record(
            actor_kind="client" if client else "anonymous",
            actor_id=client.id if client else None,
            action=f"{req.method} {route.name}",
            target=self._audit_target(context),
            outcome="allowed" if status < 400 else "denied",
            request_id=request_id,
        )
        return Response.json(status, data, request_id)

    def _audit_target(self, context: Context) -> str | None:
        for key in ("client_id", "task_id", "session_id", "reminder_id"):
            if key in context.params:
                return context.params[key]
        return None

    def _authenticate(self, req: Request, request_id: str) -> Client:
        header = req.header("authorization")
        token = ""
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
        return self.auth.authenticate(token or None, request_id=request_id)

    def _parse_json(self, req: Request) -> dict:
        content_type = req.header("content-type").split(";")[0].strip().lower()
        if not req.body:
            return {}
        if content_type and content_type != "application/json":
            raise UnsupportedMediaType(
                "this endpoint expects application/json", code="unsupported_media_type"
            )
        try:
            parsed = json.loads(req.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequest("body is not valid JSON", code="invalid_json") from exc
        if not isinstance(parsed, dict):
            raise BadRequest("body must be a JSON object", code="invalid_json")
        return parsed

    def _audit_failure(self, req: Request, request_id: str, exc: JarvisError) -> None:
        self.audit.record(
            actor_kind="anonymous",
            actor_id=None,
            action=f"{req.method} {req.path.split('?')[0][:120]}",
            outcome="denied" if exc.status in (401, 403, 405, 410, 415, 429) else "error",
            detail={"code": exc.code, "status": exc.status},
            request_id=request_id,
        )

    # ------------------------------------------------------------------ public handlers
    def _h_health(self, context: Context) -> tuple[int, dict]:
        return 200, reporting.health_payload(self)

    def _h_ready(self, context: Context) -> tuple[int, dict]:
        ready, payload = reporting.readiness_payload(self)
        return (200 if ready else 503), payload

    def _h_pair(self, context: Context) -> tuple[int, dict]:
        payload = context.payload
        code = payload.get("code") or payload.get("pairing_code") or ""
        client, token = self.auth.pair(
            code=str(code),
            label=str(payload.get("label") or ""),
            platform=str(payload.get("platform") or ""),
            request_id=context.request_id,
        )
        return 201, {
            "client": client.to_dict(),
            "token": token,
            "token_note": "shown once; store it on the device, the server keeps only a digest",
        }

    # ------------------------------------------------------------------ status
    def _h_status(self, context: Context) -> tuple[int, dict]:
        return 200, reporting.status_payload(self)

    def _h_limitations(self, context: Context) -> tuple[int, dict]:
        return 200, reporting.limitations_payload(self)

    def _h_config(self, context: Context) -> tuple[int, dict]:
        return 200, {
            "config": self.config.public_dict(),
            "spec_version": SPEC_VERSION,
            "secret": "not returned, not logged, not stored in the repository",
        }

    def _h_manifests(self, context: Context) -> tuple[int, dict]:
        client = context.client
        return 200, {
            "manifests": manifests.describe(),
            "known_scopes": sorted(manifests.ALL_SCOPES),
            "granted_scopes": list(client.scopes) if client else [],
            "rules": [
                "a manifest whose status is not 'available' cannot be served",
                "a connector becomes usable only when its external service is enabled and its "
                "adapter exists",
                "grants expire with the token and are revoked with the client",
            ],
        }

    def _h_egress(self, context: Context) -> tuple[int, dict]:
        return 200, self.guard.policy()

    def _h_surfaces(self, context: Context) -> tuple[int, dict]:
        surfaces = [item.to_dict() for item in manifests.MANIFESTS if item.kind == "surface"]
        return 200, {
            "surfaces": surfaces,
            "notes": [
                "the home holographic interface is a room-scale display in the home; it is not VR",
                "it is not Ray-Ban glasses and it is not EDITH Glass",
                "EDITH Glass is a separate future project with no defined requirements (Q25) and "
                "is not a target of this server",
                "AETHERION is a separate, postponed hardware research effort and is out of scope",
                "no optics, rendering or display hardware exists in this codebase",
            ],
            "verified": False,
        }

    # ------------------------------------------------------------------ audio
    def _h_audio_capabilities(self, context: Context) -> tuple[int, dict]:
        payload = speech.status(self.config.adapters)
        return 200, {
            **payload,
            "sessions": {
                "endpoint": "POST /v1/audio/sessions",
                "chunk_endpoint": "POST /v1/audio/sessions/{id}/audio",
                "content_types": ["audio/opus", "audio/wav", "audio/webm", "audio/ogg"],
                "max_chunk_bytes": self.config.storage.max_audio_chunk_bytes,
                "max_session_bytes": self.config.storage.max_audio_bytes_per_session,
            },
        }

    def _h_audio_output(self, context: Context) -> tuple[int, dict]:  # pragma: no cover - raises
        self.audio.output()
        raise Unavailable("spoken output unavailable", code="speech_tts_unavailable")

    def _h_audio_start(self, context: Context) -> tuple[int, dict]:
        assert context.client is not None
        return 201, self.audio.start(client_id=context.client.id, request_id=context.request_id)

    def _h_audio_append(self, context: Context) -> tuple[int, dict]:
        assert context.client is not None
        return 202, self.audio.append_audio(
            context.params["session_id"],
            client_id=context.client.id,
            content_type=context.request.header("content-type"),
            data=context.body,
            request_id=context.request_id,
        )

    def _h_audio_end(self, context: Context) -> tuple[int, dict]:
        assert context.client is not None
        return 200, self.audio.end(
            context.params["session_id"], client_id=context.client.id,
            request_id=context.request_id,
        )

    # ------------------------------------------------------------------ tasks
    def _h_tasks_list(self, context: Context) -> tuple[int, dict]:
        limit = int(context.query.get("limit", 100))
        status = context.query.get("status", "open")
        items = self.tasks.list(status=status, limit=limit)
        return 200, {
            "tasks": items,
            "count": len(items),
            "filter": status,
            "summary": summarise_tasks(items),
        }

    def _h_tasks_create(self, context: Context) -> tuple[int, dict]:
        assert context.client is not None
        task = self.tasks.create(
            title=context.payload.get("title", ""),
            notes=context.payload.get("notes"),
            due_at=context.payload.get("due_at"),
            source=str(context.payload.get("source", "structured")),
            actor_id=context.client.id,
            request_id=context.request_id,
        )
        return 201, {"task": task}

    def _h_tasks_complete(self, context: Context) -> tuple[int, dict]:
        assert context.client is not None
        task = self.tasks.complete(
            context.params["task_id"], actor_id=context.client.id, request_id=context.request_id
        )
        return 200, {"task": task}

    def _h_tasks_delete(self, context: Context) -> tuple[int, dict]:
        assert context.client is not None
        return 200, self.tasks.delete(
            context.params["task_id"], actor_id=context.client.id, request_id=context.request_id
        )

    # ------------------------------------------------------------------ reminders
    def _h_reminders_due(self, context: Context) -> tuple[int, dict]:
        limit = int(context.query.get("limit", 100))
        items = self.tasks.due_reminders(now=context.query.get("now"), limit=limit)
        return 200, {"reminders": items, "count": len(items)}

    def _h_reminder_ack(self, context: Context) -> tuple[int, dict]:
        assert context.client is not None
        return 200, {
            "reminder": self.tasks.acknowledge(
                context.params["reminder_id"], actor_id=context.client.id,
                request_id=context.request_id,
            )
        }

    # ------------------------------------------------------------------ clients
    def _h_clients_list(self, context: Context) -> tuple[int, dict]:
        return 200, {
            "clients": [client.to_dict() for client in self.auth.list_clients()],
            "note": "token digests are never returned; only the token prefix is shown",
        }

    def _h_client_me(self, context: Context) -> tuple[int, dict]:
        assert context.client is not None
        return 200, {
            "client": context.client.to_dict(),
            "grants": self.auth.grants_for(context.client.id),
        }

    def _h_client_revoke(self, context: Context) -> tuple[int, dict]:
        assert context.client is not None
        target = context.params["client_id"]
        self_revoke = target == context.client.id
        if not self_revoke and not context.client.has_scope(manifests.SCOPE_CLIENTS_REVOKE):
            raise ScopeDenied(
                "revoking another client needs the clients.revoke scope",
                code="scope_denied",
                detail={"required_scope": manifests.SCOPE_CLIENTS_REVOKE},
            )
        revoked = self.auth.revoke(
            target,
            reason="self-revoked by client" if self_revoke else "revoked by operator",
            actor_kind="client" if self_revoke else "operator",
            actor_id=context.client.id,
            request_id=context.request_id,
        )
        deleted = self.audio.purge_client(target)
        return 200, {
            "client": revoked.to_dict(),
            "audio_files_deleted": deleted,
            "effect": "the token stops working immediately; cached audio is removed",
        }

    # ------------------------------------------------------------------ audit
    def _h_audit(self, context: Context) -> tuple[int, dict]:
        limit = max(1, min(int(context.query.get("limit", 50)), 500))
        after = context.query.get("after_id")
        entries = self.audit.tail(limit, after_id=int(after) if after else None)
        return 200, {"entries": [entry.to_dict() for entry in entries], "count": len(entries)}
