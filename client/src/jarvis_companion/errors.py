"""Failure vocabulary shared by every part of the companion.

Each class maps to one honest outcome the owner can act on. Nothing here is a silent fallback: a
failure a caller cannot handle is reported, not swallowed.
"""

from __future__ import annotations


class CompanionError(Exception):
    """Base class for everything this package raises deliberately."""

    #: Short, stable code used in logs and in ``status --json``.
    code = "companion_error"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = dict(detail or {})

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class NotConfigured(CompanionError):
    """No pairing exists yet, or the configuration is missing."""

    code = "not_configured"


class ConfigInvalid(CompanionError):
    """A configuration value is missing or outside its permitted range."""

    code = "config_invalid"


class InsecureTransport(CompanionError):
    """Plaintext HTTP to a non-loopback host would expose the device token on the network."""

    code = "insecure_transport"


class CredentialUnavailable(CompanionError):
    """The OS-protected credential store cannot be used on this platform."""

    code = "credential_store_unavailable"


class ServerUnreachable(CompanionError):
    """The local server did not answer. Transient: the companion backs off and retries."""

    code = "server_unreachable"


class AuthRejected(CompanionError):
    """The token is expired, revoked, or lacks the scope; or pairing was refused.

    Fatal for polling: retrying cannot fix it, so the companion stops and says why. ``kind`` is one of
    ``token_expired``, ``token_revoked``, ``scope_denied``, ``pairing_refused``, ``unauthenticated``.
    """

    code = "auth_rejected"

    def __init__(self, message: str, *, kind: str, detail: dict | None = None) -> None:
        super().__init__(message, detail=detail)
        self.kind = kind


class ProtocolError(CompanionError):
    """The server answered with something this client cannot interpret safely."""

    code = "protocol_error"


class DisplayZoneMissing(CompanionError):
    """The server did not publish a display time zone, so no reminder time can be rendered."""

    code = "display_zone_missing"


class NotificationUnavailable(CompanionError):
    """The OS notification path is missing or refused. Never treated as "shown"."""

    code = "notification_unavailable"


__all__ = [
    "AuthRejected",
    "CompanionError",
    "ConfigInvalid",
    "CredentialUnavailable",
    "DisplayZoneMissing",
    "InsecureTransport",
    "NotConfigured",
    "NotificationUnavailable",
    "ProtocolError",
    "ServerUnreachable",
]
