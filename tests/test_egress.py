"""Default-deny egress: loopback allowed, external refused unless declared."""
from __future__ import annotations

import socket

import pytest

from jarvis.egress import EgressDenied, EgressGuard


@pytest.fixture
def guard():
    return EgressGuard()                     # never the process-global guard


def test_loopback_is_not_egress(guard):
    for host in ("127.0.0.1", "::1", "localhost"):
        guard.check(host, 8080)              # must not raise
    assert guard.attempts == 3 and guard.denials == 0


def test_external_connections_are_denied_by_default(guard):
    with pytest.raises(EgressDenied, match="default-deny"):
        guard.check("example.com", 443)
    assert guard.denials == 1 and guard.events[-1].allowed is False


def test_declaration_allows_exactly_one_destination(guard):
    with guard.declare("model_provider", "generativelanguage.googleapis.com", 443):
        guard.check("generativelanguage.googleapis.com", 443)
        with pytest.raises(EgressDenied):
            guard.check("generativelanguage.googleapis.com", 8443)
        with pytest.raises(EgressDenied):
            guard.check("attacker.example", 443)
    with pytest.raises(EgressDenied):        # the window is closed again
        guard.check("generativelanguage.googleapis.com", 443)
    assert guard.events[-1].service is None or guard.events[-1].allowed is False


def test_declarations_are_per_thread(guard):
    import threading
    result: dict[str, bool] = {}

    def worker():
        try:
            guard.check("example.com", 443)
        except EgressDenied:
            result["denied"] = True

    with guard.declare("model_provider", "example.com", 443):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
    assert result == {"denied": True}        # a declaration does not leak across threads


def test_install_patches_and_restores_the_socket_layer(guard):
    original = socket.socket.connect
    guard.install()
    try:
        assert socket.socket.connect is not original
        with pytest.raises(EgressDenied):
            socket.create_connection(("example.com", 443), timeout=0.01)
    finally:
        guard.uninstall()
    assert socket.socket.connect is original


def test_install_is_idempotent(guard):
    guard.install()
    try:
        patched = socket.socket.connect
        guard.install()
        assert socket.socket.connect is patched
    finally:
        guard.uninstall()


def test_policy_reports_that_every_external_connector_is_disabled(guard):
    policy = guard.policy()
    assert policy["default"] == "deny"
    assert all(service["enabled"] is False for service in policy["declared_services"])
    assert {s["service"] for s in policy["declared_services"]} >= {"model_provider", "public_web"}
