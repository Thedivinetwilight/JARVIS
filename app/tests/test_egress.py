"""Default-deny outbound network policy."""

from __future__ import annotations

import socket
import unittest
import urllib.error
import urllib.request

from app.jarvis import speech
from app.jarvis.egress import DECLARED_SERVICES, GUARD, EgressDenied, UnimplementedAdapter
from app.jarvis.errors import Unavailable
from app.tests.support import build_app

#: TEST-NET-3 (RFC 5737): reserved for documentation, never routable. If the guard ever
#: failed to block, this test would hang on a connect attempt instead of "succeeding".
UNROUTABLE = "203.0.113.7"


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        GUARD.uninstall()
        self.addCleanup(GUARD.uninstall)

    def test_policy_is_deny_by_default(self):
        policy = GUARD.policy()
        self.assertEqual(policy["default"], "deny")
        self.assertTrue(policy["loopback_allowed"])
        declared = policy["declared_external_services"]
        self.assertEqual(len(declared), 4)
        self.assertTrue(all(not item["enabled"] for item in declared))
        self.assertEqual(len(DECLARED_SERVICES), 4)
        for service in DECLARED_SERVICES:
            self.assertFalse(service.enabled, service.service)
            self.assertEqual(service.adapter, "not_implemented")
            self.assertTrue(service.note)

    def test_loopback_is_not_treated_as_egress(self):
        for host in ("127.0.0.1", "127.0.0.53", "::1", "localhost", "LOCALHOST"):
            with self.subTest(host=host):
                allowed, _, reason = GUARD.allows(host, 7420)
                self.assertTrue(allowed, reason)

    def test_external_hosts_are_denied(self):
        for host, port in ((UNROUTABLE, 443), ("example.com", 443), ("8.8.8.8", 53),
                           ("printer.local", 631)):
            with self.subTest(host=host):
                allowed, _, reason = GUARD.allows(host, port)
                self.assertFalse(allowed)
                self.assertIn("no declared external-service adapter", reason)
                with self.assertRaises(EgressDenied):
                    GUARD.check(host, port)

    def test_a_declaration_permits_exactly_one_destination(self):
        with GUARD.declare("update_downloads", "updates.example.org", 443):
            self.assertTrue(GUARD.allows("updates.example.org", 443)[0])
            self.assertFalse(GUARD.allows("updates.example.org", 8443)[0])
            self.assertFalse(GUARD.allows("other.example.org", 443)[0])
        self.assertFalse(GUARD.allows("updates.example.org", 443)[0])

    def test_unknown_services_cannot_be_declared(self):
        with self.assertRaises(EgressDenied):
            with GUARD.declare("social_media", "example.com", 443):
                pass

    def test_every_declared_service_names_its_spec_reference(self):
        for service in DECLARED_SERVICES:
            with self.subTest(service=service.service):
                self.assertFalse(service.enabled)
                self.assertIn("capability-specification.md#02", service.spec_reference)
                self.assertTrue(service.note)

    def test_a_declaration_lifts_the_denial_only_for_that_service(self):
        with GUARD.declare("public_web", "example.org", 443):
            allowed, service, _ = GUARD.allows("example.org", 443)
            self.assertTrue(allowed)
            self.assertEqual(service, "public_web")
        self.assertFalse(GUARD.allows("example.org", 443)[0])

    def test_declaration_and_counters_are_reported(self):
        before = GUARD.policy()["counters"]["denials"]
        with self.assertRaises(EgressDenied):
            GUARD.check("192.0.2.10", 443)
        policy = GUARD.policy()
        self.assertEqual(policy["counters"]["denials"], before + 1)
        self.assertGreaterEqual(policy["counters"]["attempts"], before + 1)

    def test_adapter_stub_refuses_instead_of_calling(self):
        adapter = UnimplementedAdapter("public_web")
        with self.assertRaises(Unavailable):
            adapter.call("https://example.com")

    def test_calling_an_unconfigured_adapter_refuses(self):
        registry = speech.build_registry({"speech_asr": "unavailable", "speech_tts": "unavailable"})
        with self.assertRaises(Unavailable):
            registry["asr"].recognise("/nonexistent.wav")
        with self.assertRaises(Unavailable):
            registry["tts"].synthesise({"text": "hello"})


class EnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        GUARD.uninstall()
        self.addCleanup(GUARD.uninstall)

    def test_install_patches_the_socket_layer_and_uninstall_restores_it(self):
        original = socket.socket.connect
        self.assertTrue(GUARD.install())
        self.assertIsNot(socket.socket.connect, original)
        self.assertTrue(GUARD.installed)
        GUARD.uninstall()
        self.assertIs(socket.socket.connect, original)
        self.assertFalse(GUARD.installed)

    def test_blocked_connect_raises_before_any_packet_is_sent(self):
        GUARD.install()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        with self.assertRaises(EgressDenied):
            sock.connect((UNROUTABLE, 443))

    def test_raw_socket_to_loopback_is_still_allowed(self):
        GUARD.install()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client = socket.create_connection(server.getsockname(), timeout=2)
        self.addCleanup(client.close)
        accepted, _peer = server.accept()
        self.addCleanup(accepted.close)
        self.assertIsNotNone(client.getsockname())

    def test_urllib_request_to_the_internet_is_blocked(self):
        GUARD.install()
        with self.assertRaises((EgressDenied, urllib.error.URLError)) as caught:
            urllib.request.urlopen(f"http://{UNROUTABLE}/", timeout=2)
        error = caught.exception
        # urllib wraps socket-level failures; the guard's refusal must be the cause either way.
        if isinstance(error, urllib.error.URLError):
            error = error.reason
        self.assertIsInstance(error, EgressDenied)
        self.assertEqual(error.host, UNROUTABLE)

    def test_readiness_reports_the_guard(self):
        app, config = build_app(self._dir())
        try:
            self.assertFalse(app.request("GET", "/readyz").status == 200)
            GUARD.install()
            ready = app.request("GET", "/readyz")
            self.assertEqual(ready.status, 200)
        finally:
            app.guard.uninstall()

    def _dir(self):
        import pathlib
        import tempfile

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return pathlib.Path(directory.name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
