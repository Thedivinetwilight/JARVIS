"""Default-deny outbound network policy.

The rule is simple and enforced at the socket layer: a connection to anything other than
loopback is refused unless the call is made inside an explicit, narrow declaration for one
of the four *declared* external services, and no adapter for those services exists in this
build. Loopback is not egress — it never leaves the machine — so it stays permitted.

There is no "allow all" switch, no environment variable that widens the policy, and no
HTTP client helper in this package: the only way out is ``EgressGuard.declare``.
"""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from .errors import EgressDenied, Unavailable

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "ip6-localhost"}


@dataclass(frozen=True)
class DeclaredService:
    """One of the four external services named in the product specification, section 0.2."""

    service: str
    adapter: str
    enabled: bool
    note: str
    spec_reference: str = "docs/product/capability-specification.md#02-deployment-and-processing-boundary-normative"


DECLARED_SERVICES: tuple[DeclaredService, ...] = (
    DeclaredService(
        service="telephone_network",
        adapter="not_implemented",
        enabled=False,
        note="calls need a telephony scope, jurisdiction rules (Q17) and an owner decision "
             "(Q22); none exists yet",
    ),
    DeclaredService(
        service="public_web",
        adapter="not_implemented",
        enabled=False,
        note="retrieval would leave the server; it stays off until it is scoped per request "
             "and approved",
    ),
    DeclaredService(
        service="device_vendor_cloud",
        adapter="not_implemented",
        enabled=False,
        note="only for a device with no local path, per device, with the minimum data; no "
             "device inventory exists (Q21, Q24)",
    ),
    DeclaredService(
        service="update_downloads",
        adapter="not_implemented",
        enabled=False,
        note="software and model updates are not selected or automated yet",
    ),
)


@dataclass
class EgressEvent:
    host: str
    port: int
    allowed: bool
    service: str | None
    reason: str


class EgressGuard:
    """Refuses outbound connections unless a declaration is active for that destination.

    ``allow_loopback`` exists for one caller: the benchmark harness, which runs an
    operator-supplied adapter and must deny *every* socket attempt (a local daemon can be a
    network hop too). The server always uses the default, where loopback is not egress.
    """

    def __init__(self, *, allow_loopback: bool = True) -> None:
        self.allow_loopback = allow_loopback
        self._local = threading.local()
        self._lock = threading.Lock()
        self._installed = False
        self._originals: dict[str, object] = {}
        self.attempts = 0
        self.denials = 0
        self.events: list[EgressEvent] = []

    # ------------------------------------------------------------------ state
    def _declarations(self) -> list[tuple[str, str, int]]:
        stack = getattr(self._local, "stack", None)
        return list(stack) if stack else []

    @contextmanager
    def declare(self, service: str, host: str, port: int) -> Iterator[None]:
        """Permit exactly one destination inside this block, for one declared service."""
        known = {item.service for item in DECLARED_SERVICES}
        if service not in known:
            raise EgressDenied(host, port, f"service {service!r} is not a declared external service")
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        stack.append((service, host, int(port)))
        try:
            yield
        finally:
            stack.pop()

    def allows(self, host: str, port: int) -> tuple[bool, str | None, str]:
        host = (host or "").strip().lower()
        if self.allow_loopback and (host in LOOPBACK_HOSTS or host.startswith("127.") or host == "::1"):
            return True, None, "loopback is not egress"
        for service, declared_host, declared_port in self._declarations():
            if declared_host.lower() == host and int(declared_port) == int(port):
                return True, service, "declared adapter destination"
        return False, None, "no declared external-service adapter is active"

    def check(self, host: str, port: int) -> None:
        allowed, service, reason = self.allows(host, port)
        with self._lock:
            self.attempts += 1
            if not allowed:
                self.denials += 1
            self.events.append(EgressEvent(host=host, port=int(port), allowed=allowed,
                                           service=service, reason=reason))
            if len(self.events) > 200:
                del self.events[:100]
        if not allowed:
            raise EgressDenied(host, int(port), reason)

    # ------------------------------------------------------------------ patching
    def install(self) -> bool:
        """Patch ``socket`` once. Returns True if this call installed the guard."""
        with self._lock:
            if self._installed:
                return False
            guard = self

            original_connect = socket.socket.connect
            original_connect_ex = socket.socket.connect_ex

            def guarded_connect(sock, address):  # type: ignore[no-untyped-def]
                if isinstance(address, tuple) and len(address) >= 2:
                    guard.check(str(address[0]), int(address[1]))
                return original_connect(sock, address)

            def guarded_connect_ex(sock, address):  # type: ignore[no-untyped-def]
                if isinstance(address, tuple) and len(address) >= 2:
                    guard.check(str(address[0]), int(address[1]))
                return original_connect_ex(sock, address)

            self._originals = {"connect": original_connect, "connect_ex": original_connect_ex}
            socket.socket.connect = guarded_connect  # type: ignore[method-assign]
            socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
            self._installed = True
            return True

    def uninstall(self) -> None:
        with self._lock:
            if not self._installed:
                return
            socket.socket.connect = self._originals["connect"]  # type: ignore[method-assign]
            socket.socket.connect_ex = self._originals["connect_ex"]  # type: ignore[method-assign]
            self._installed = False

    @property
    def installed(self) -> bool:
        return self._installed

    # ------------------------------------------------------------------ reporting
    def policy(self) -> dict:
        return {
            "default": "deny",
            "loopback_allowed": self.allow_loopback,
            "loopback_reason": "a loopback connection never leaves the host",
            "guard_installed": self._installed,
            "declared_external_services": [
                {
                    "service": item.service,
                    "adapter": item.adapter,
                    "enabled": item.enabled,
                    "note": item.note,
                    "spec_reference": item.spec_reference,
                }
                for item in DECLARED_SERVICES
            ],
            "counters": {"attempts": self.attempts, "denials": self.denials},
        }


#: Process-wide guard. The server installs it at startup; tests can inspect it directly.
GUARD = EgressGuard()


class UnimplementedAdapter:
    """Placeholder for a declared external service.

    It exists so the contract is explicit: an adapter must (a) be enabled by configuration,
    (b) hold a scope granted by the owner, and (c) open the connection inside
    ``GUARD.declare(...)``. None of that is true for any service yet.
    """

    def __init__(self, service: str) -> None:
        self.service = service

    def call(self, *args, **kwargs):  # pragma: no cover - always refuses
        raise Unavailable(
            f"the {self.service} adapter is not implemented; the service stays disabled "
            "until it is individually scoped and approved",
            code=f"adapter_unavailable:{self.service}",
        )


ADAPTERS = {item.service: UnimplementedAdapter(item.service) for item in DECLARED_SERVICES}
