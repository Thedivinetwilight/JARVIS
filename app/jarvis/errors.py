"""Typed errors that the HTTP layer can turn into stable, non-leaking responses.

Every error carries a machine-readable ``code``. The same code appears in audit records
and in the limitation report, so a client can distinguish "you are not allowed" from "the
capability does not exist yet" without reading prose.
"""

from __future__ import annotations


class JarvisError(Exception):
    """Base error with an HTTP status and a stable code."""

    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, detail=None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.detail = detail

    def to_dict(self) -> dict:
        payload = {"code": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class ConfigError(JarvisError):
    """Configuration is missing, malformed or refused by a startup rule."""

    status = 500
    code = "config_invalid"


class StartupRefused(JarvisError):
    """The server refuses to start because a safety rule is not satisfied."""

    status = 500
    code = "startup_refused"


class MigrationError(JarvisError):
    status = 500
    code = "migration_failed"


class BadRequest(JarvisError):
    status = 400
    code = "bad_request"


class Unauthenticated(JarvisError):
    status = 401
    code = "unauthenticated"


class TokenExpired(Unauthenticated):
    code = "token_expired"


class TokenRevoked(Unauthenticated):
    code = "token_revoked"


class ScopeDenied(JarvisError):
    """Authenticated, but the token's granted scopes do not cover the route."""

    status = 403
    code = "scope_denied"


class NotFound(JarvisError):
    status = 404
    code = "not_found"


class Conflict(JarvisError):
    status = 409
    code = "conflict"


class PayloadTooLarge(JarvisError):
    status = 413
    code = "payload_too_large"


class MethodNotAllowed(JarvisError):
    """The path exists but not for this method."""

    status = 405
    code = "method_not_allowed"


class UnsupportedMediaType(JarvisError):
    status = 415
    code = "unsupported_media_type"


class Expired(JarvisError):
    status = 410
    code = "expired"


class Unavailable(JarvisError):
    """A declared capability cannot be served yet (no model, no adapter, no approval)."""

    status = 503
    code = "unavailable"


class EgressDenied(OSError):
    """An outbound network attempt was refused by the egress guard.

    It subclasses ``OSError`` on purpose: helper code such as ``socket.create_connection``
    only closes its half-open socket for ``OSError``, and a leaked descriptor on every
    blocked attempt would be its own small denial of service.
    """

    def __init__(self, host: str, port: int, reason: str = "no declared external-service adapter") -> None:
        super().__init__(f"egress denied to {host}:{port}: {reason}")
        self.host = host
        self.port = port
        self.reason = reason
