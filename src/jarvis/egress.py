"""Default-deny outbound network policy (socket-layer enforcement).

Ported from bbff (app/jarvis/egress.py). A connection to anything other than
loopback is refused unless the call is made inside an explicit, narrow
declaration for a declared external service. Loopback is not egress.

There is no "allow all" switch and no environment variable that widens the
policy. The only way out is ``EgressGuard.declare``.
"""
from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "ip6-localhost"}


@dataclass(frozen=True)
class DeclaredService:
    service: str
    adapter: str
    enabled: bool
    note: str
    spec_reference: str = ""


DECLARED_SERVICES: tuple[DeclaredService, ...] = (
    DeclaredService(
        service="telephone_network", adapter="not_implemented", enabled=False,
        note="Calls need telephony scope and owner decision; none exists yet."),
    DeclaredService(
        service="public_web", adapter="not_implemented", enabled=False,
        note="Retrieval would leave the server; stays off until scoped per request."),
    DeclaredService(
        service="device_vendor_cloud", adapter="not_implemented", enabled=False,
        note="Only for a device with no local path; no device inventory exists."),
    DeclaredService(
        service="update_downloads", adapter="not_implemented", enabled=False,
        note="Software and model updates are not automated."),
    DeclaredService(
        service="web_research", adapter="implemented", enabled=False,
        note="Read-only GET through an explicit host allow-list (JARVIS_ALLOWLIST). Every "
             "connection is declared to this guard by validated IP, ports 80/443 only, and "
             "the capability is refused unless JARVIS_NETWORK_ENABLED=true."),
    DeclaredService(
        service="model_provider", adapter="configured", enabled=False,
        note="Model providers (Gemini, OpenAI-compatible) are the only declared "
             "egress. Enabled only when JARVIS_NETWORK_ENABLED=true and a provider "
             "is configured. Loopback providers are not egress."),
)


@dataclass
class EgressEvent:
    host: str
    port: int
    allowed: bool
    service: str | None
    reason: str


class EgressDenied(RuntimeError):
    pass


class EgressGuard:
    """Refuses outbound connections unless a declaration is active."""

    def __init__(self, *, allow_loopback: bool = True) -> None:
        self.allow_loopback = allow_loopback
        self._local = threading.local()
        self._lock = threading.Lock()
        self._installed = False
        self._originals: dict[str, object] = {}
        self.attempts = 0
        self.denials = 0
        self.events: list[EgressEvent] = []
        self._enabled: dict[str, bool] = {}

    def _declarations(self) -> list[tuple[str, str, int]]:
        stack = getattr(self._local, "stack", None)
        return list(stack) if stack else []

    @contextmanager
    def declare(self, service: str, host: str, port: int) -> Iterator[None]:
        """Allow connections to (host, port) for the duration of the block."""
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        stack.append((service, host, port))
        try:
            yield
        finally:
            stack.pop()

    def _is_allowed(self, host: str, port: int) -> tuple[bool, str | None, str]:
        if self.allow_loopback and host in LOOPBACK_HOSTS:
            return True, None, "loopback"
        for service, decl_host, decl_port in self._declarations():
            if host == decl_host and port == decl_port:
                return True, service, f"declared:{service}"
        return False, None, "default-deny"

    def check(self, host: str, port: int) -> None:
        """Check whether a connection to (host, port) is allowed. Raises if denied."""
        allowed, service, reason = self._is_allowed(host, port)
        with self._lock:
            self.attempts += 1
            if not allowed:
                self.denials += 1
            self.events.append(EgressEvent(host, port, allowed, service, reason))
            # Keep only last 1000 events
            if len(self.events) > 1000:
                self.events = self.events[-1000:]
        if not allowed:
            raise EgressDenied(f"Egress denied: {host}:{port} ({reason})")

    def install(self) -> None:
        """Install socket-level enforcement. Idempotent."""
        if self._installed:
            return
        original_connect = socket.socket.connect
        original_create_connection = socket.create_connection
        guard = self

        def guarded_connect(sock_self, address):
            if isinstance(address, tuple) and len(address) >= 2:
                host, port = address[0], address[1]
                guard.check(str(host), int(port))
            return original_connect(sock_self, address)

        def guarded_create_connection(address, timeout=None, *args, **kwargs):
            if isinstance(address, tuple) and len(address) >= 2:
                host, port = address[0], address[1]
                guard.check(str(host), int(port))
            return original_create_connection(address, timeout, *args, **kwargs)

        self._originals = {"connect": original_connect, "create_connection": original_create_connection}
        socket.socket.connect = guarded_connect  # type: ignore[method-assign]
        socket.create_connection = guarded_create_connection  # type: ignore[method-assign]
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        socket.socket.connect = self._originals["connect"]  # type: ignore[method-assign]
        socket.create_connection = self._originals["create_connection"]  # type: ignore[method-assign]
        self._installed = False

    def set_enabled(self, service: str, enabled: bool) -> None:
        """Record that a declared service is currently usable (or not). Configuration-driven.

        This never widens the policy: it only makes the *reported* state match configuration, so
        a capability report cannot claim a service is available when it is not, or vice versa.
        """
        declared = {item.service for item in DECLARED_SERVICES}
        if service not in declared:
            raise KeyError(f"Service is not declared: {service}")
        self._enabled[service] = bool(enabled)

    def policy(self) -> dict:
        return {
            "default": "deny",
            "allow_loopback": self.allow_loopback,
            "declared_services": [
                {"service": s.service, "adapter": s.adapter,
                 "enabled": self._enabled.get(s.service, s.enabled), "note": s.note}
                for s in DECLARED_SERVICES
            ],
            "attempts": self.attempts,
            "denials": self.denials,
        }


# Global guard instance (installed by the server at startup)
GUARD = EgressGuard()
