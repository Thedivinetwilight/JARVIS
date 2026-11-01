"""Fail-closed network isolation for benchmark adapters.

The benchmark runs adapters in their own process, and that process loses the network *before the
adapter is imported*:

* ``socket.socket.connect`` / ``connect_ex`` go through the application's egress guard, configured
  here with ``allow_loopback=False``, so even a local daemon is refused by default;
* name resolution (``getaddrinfo``, ``gethostbyname``, ``gethostbyname_ex``) is refused, because a
  look-up is already an outbound request;
* every refusal is counted, and **one refusal ends the run**: the worker reports the violation to
  the parent, the parent stops scheduling and the result is marked ``violated`` with the decision
  ``rejected``. An adapter that swallows the exception cannot hide the attempt, because the counter
  lives outside the adapter.

A benchmark adapter legitimately needs files, not sockets. The single exception is an operator who
runs inference in a local daemon: they may pass ``--allow-loopback-ipc``, which permits loopback
only, records the deviation in the result, and still refuses every other destination.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field

from app.jarvis.egress import EgressGuard

LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "ip6-localhost", "0.0.0.0"}


def _is_loopback(host: str) -> bool:
    host = (host or "").strip().lower()
    return host in LOOPBACK_NAMES or host.startswith("127.") or host == "::1"


@dataclass
class AdapterSandbox:
    allow_loopback: bool = False
    guard: EgressGuard = field(default_factory=lambda: EgressGuard(allow_loopback=False))
    violations: list = field(default_factory=list)
    attempts: int = 0
    _installed: bool = False
    _originals: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------------ lifecycle
    def install(self) -> None:
        if self._installed:
            return
        self.guard = EgressGuard(allow_loopback=self.allow_loopback)
        self.guard.install()
        self._originals = {
            "getaddrinfo": socket.getaddrinfo,
            "gethostbyname": socket.gethostbyname,
            "gethostbyname_ex": socket.gethostbyname_ex,
            "create_connection": socket.create_connection,
        }
        sandbox = self

        def guarded_getaddrinfo(host, port, *arguments, **keywords):
            sandbox._check_lookup(str(host), int(port or 0))
            return sandbox._originals["getaddrinfo"](host, port, *arguments, **keywords)

        def guarded_gethostbyname(host):
            sandbox._check_lookup(str(host), 0)
            return sandbox._originals["gethostbyname"](host)

        def guarded_gethostbyname_ex(host):
            sandbox._check_lookup(str(host), 0)
            return sandbox._originals["gethostbyname_ex"](host)

        def guarded_create_connection(address, *arguments, **keywords):
            if isinstance(address, tuple) and address:
                sandbox._check_lookup(str(address[0]), int(address[1]) if len(address) > 1 else 0)
            return sandbox._originals["create_connection"](address, *arguments, **keywords)

        socket.getaddrinfo = guarded_getaddrinfo          # type: ignore[assignment]
        socket.gethostbyname = guarded_gethostbyname      # type: ignore[assignment]
        socket.gethostbyname_ex = guarded_gethostbyname_ex  # type: ignore[assignment]
        socket.create_connection = guarded_create_connection  # type: ignore[assignment]
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        for name, original in self._originals.items():
            setattr(socket, name, original)
        self.guard.uninstall()
        self._installed = False

    # ------------------------------------------------------------------ checks
    def _record(self, kind: str, host: str, port: int, reason: str) -> None:
        with self._lock:
            self.attempts += 1
            self.violations.append({
                "kind": kind,
                "host": host,
                "port": port,
                "reason": reason,
                "at_unix": round(time.time(), 3),
            })

    def _check_lookup(self, host: str, port: int) -> None:
        allowed = self.allow_loopback and _is_loopback(host)
        if allowed:
            return
        reason = (
            "name resolution is not permitted: a look-up is already an outbound request"
            if not self.allow_loopback else
            "only loopback is permitted with --allow-loopback-ipc"
        )
        self._record("lookup", host, port, reason)
        raise _Denied(host, port, reason)

    def check_connect(self, host: str, port: int) -> None:
        allowed, _service, reason = self.guard.allows(host, port)
        if allowed:
            return
        self._record("connect", host, int(port), reason)
        raise _Denied(host, int(port), reason)

    # ------------------------------------------------------------------ reporting
    def status(self) -> dict:
        counters = self.guard.policy()["counters"]
        return {
            "network_denied": True,
            "loopback_allowed": self.allow_loopback,
            "lookup_denied": True,
            "attempts": self.attempts,
            "guard_attempts": counters["attempts"],
            "guard_denials": counters["denials"],
            "violations": len(self.violations),
            "violation_detail": list(self.violations[:10]),
        }

    @property
    def violated(self) -> bool:
        return bool(self.violations) or self.guard.denials > 0


class _Denied(OSError):
    """Raised by the sandbox. Subclasses ``OSError`` so callers close their sockets."""

    def __init__(self, host: str, port: int, reason: str) -> None:
        super().__init__(f"network access denied to {host}:{port}: {reason}")
        self.host = host
        self.port = port
        self.reason = reason


Denied = _Denied
