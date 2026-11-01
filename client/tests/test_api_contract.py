"""The HTTP contract with the real server, plus what happens when something else answers.

The live-server tests use the genuine ``app.jarvis`` application over a loopback socket. The stub
tests cover answers a real server should never give — a redirect, an HTML page, a 503 — because a
client that mishandles those leaks a token or lies about an empty inbox.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock

from . import support

from jarvis_companion.api import MAX_RESPONSE_BYTES, ServerApi
from jarvis_companion.config import CompanionConfig
from jarvis_companion.errors import AuthRejected, ProtocolError, ServerUnreachable


class LiveApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = support.LiveServer().start()
        self.addCleanup(self.server.stop)

    def _pair(self, *, scopes=("reminders.receive", "status.read")):
        return support.paired_client(self.server, scopes=scopes)

    def test_pairing_returns_the_client_and_a_working_token(self):
        paired = self._pair()
        self.assertTrue(paired["client"]["id"].startswith("cli_"))
        self.assertIn("reminders.receive", paired["client"]["scopes"])
        payload = paired["api"].inbox(paired["token"])
        self.assertIn("events", payload)
        self.assertEqual(payload["client_id"], paired["client"]["id"])

    def test_a_wrong_pairing_code_is_refused_with_the_servers_reason(self):
        api = ServerApi(CompanionConfig(self.server.url))
        with self.assertRaises(AuthRejected) as caught:
            api.pair(code="AAAA-BBBB-CCCC", label="test pc")
        self.assertEqual(caught.exception.kind, "pairing_refused")
        self.assertIn("pairing refused", str(caught.exception))

    def test_an_unknown_token_is_reported_as_unauthenticated_not_empty(self):
        api = ServerApi(CompanionConfig(self.server.url))
        with self.assertRaises(AuthRejected) as caught:
            api.inbox("jvs_not_a_real_token")
        self.assertEqual(caught.exception.kind, "unauthenticated")

    def test_a_token_without_the_receive_scope_says_which_scope_is_missing(self):
        paired = self._pair(scopes=("status.read",))
        with self.assertRaises(AuthRejected) as caught:
            paired["api"].inbox(paired["token"])
        self.assertEqual(caught.exception.kind, "scope_denied")
        self.assertEqual(caught.exception.detail["required_scope"], "reminders.receive")

    def test_another_clients_delivery_cannot_be_acknowledged(self):
        first = self._pair()
        second = self._pair()
        self.server.remind(client_id=first["client"]["id"])
        self.server.advance(seconds=3610)
        delivery_id = first["api"].inbox(first["token"])["events"][0]["delivery_id"]
        with self.assertRaises(ProtocolError) as caught:
            second["api"].ack(second["token"], delivery_id)
        self.assertEqual(caught.exception.detail["code"], "delivery_not_found")

    def test_acknowledging_a_closed_delivery_is_reported(self):
        paired = self._pair()
        self.server.remind(client_id=paired["client"]["id"])
        self.server.advance(seconds=3610)
        delivery_id = paired["api"].inbox(paired["token"])["events"][0]["delivery_id"]
        # Beyond the 24-hour window the server closes the delivery as expired.
        self.server.advance(hours=25)
        paired["api"].inbox(paired["token"])
        with self.assertRaises(ProtocolError) as caught:
            paired["api"].ack(paired["token"], delivery_id)
        self.assertEqual(caught.exception.detail["code"], "delivery_closed")
        self.assertIn("closed", str(caught.exception))

    def test_acknowledging_is_idempotent_on_the_server(self):
        paired = self._pair()
        self.server.remind(client_id=paired["client"]["id"])
        self.server.advance(seconds=3610)
        delivery_id = paired["api"].inbox(paired["token"])["events"][0]["delivery_id"]
        first = paired["api"].ack(paired["token"], delivery_id)
        second = paired["api"].ack(paired["token"], delivery_id)
        self.assertEqual(first["state"], "acknowledged")
        self.assertEqual(second["state"], "acknowledged")

    def test_self_revocation_makes_the_token_stop_working(self):
        paired = self._pair()
        result = paired["api"].revoke_self(paired["token"], paired["client"]["id"])
        self.assertIn("client", result)
        with self.assertRaises(AuthRejected) as caught:
            paired["api"].inbox(paired["token"])
        self.assertEqual(caught.exception.kind, "token_revoked")


class StubApiTests(unittest.TestCase):
    def _api(self, stub: support.StubServer) -> ServerApi:
        return ServerApi(CompanionConfig(stub.url))

    def test_a_redirect_is_refused_and_the_token_is_not_forwarded(self):
        target = support.StubServer(status=200).start()
        self.addCleanup(target.stop)
        redirector = support.StubServer(status=302, redirect_to=f"{target.url}/steal").start()
        self.addCleanup(redirector.stop)
        with self.assertRaises(ProtocolError) as caught:
            self._api(redirector).inbox("jvs_secret_token")
        self.assertIn("redirect", str(caught.exception))
        self.assertEqual(target.requests, [], "the token was forwarded to another host")

    def test_an_html_response_is_a_protocol_error_not_an_empty_inbox(self):
        stub = support.StubServer(status=200, body=b"<html>not the API</html>",
                                  content_type="text/html").start()
        self.addCleanup(stub.stop)
        with self.assertRaises(ProtocolError) as caught:
            self._api(stub).inbox("jvs_secret_token")
        self.assertIn("not JSON", str(caught.exception))

    def test_an_empty_body_is_a_protocol_error(self):
        stub = support.StubServer(status=200, body=b"").start()
        self.addCleanup(stub.stop)
        with self.assertRaises(ProtocolError):
            self._api(stub).health()

    def test_a_server_that_is_not_ready_is_transient_and_explained(self):
        stub = support.StubServer(
            status=503, body=support.error_body("display_timezone_unresolved",
                                                "reminders.display_timezone is not a known IANA zone")
        ).start()
        self.addCleanup(stub.stop)
        with self.assertRaises(ServerUnreachable) as caught:
            self._api(stub).inbox("jvs_secret_token")
        self.assertIn("not ready", str(caught.exception))
        self.assertIn("display_timezone_unresolved", str(caught.exception))

    def test_an_oversized_response_is_refused(self):
        stub = support.StubServer(
            status=200, body=b'{"pad": "' + b"x" * (MAX_RESPONSE_BYTES + 10) + b'"}'
        ).start()
        self.addCleanup(stub.stop)
        with self.assertRaises((ProtocolError, ServerUnreachable)):
            self._api(stub).inbox("jvs_secret_token")

    def test_an_unreachable_server_is_a_typed_transient_failure(self):
        stub = support.StubServer().start()
        url = stub.url
        stub.stop()
        with self.assertRaises(ServerUnreachable) as caught:
            ServerApi(CompanionConfig(url)).health()
        self.assertIn("cannot reach", str(caught.exception))

    def test_the_request_looks_nothing_like_a_browser_and_sets_no_cookies(self):
        stub = support.StubServer(status=200, body=b'{"events": [], "display_timezone": "UTC"}'
                                 ).start()
        self.addCleanup(stub.stop)
        self._api(stub).inbox("jvs_secret_token")
        headers = stub.requests[0]["headers"]
        self.assertTrue(headers["user-agent"].startswith("jarvis-companion/"))
        for forbidden in ("cookie", "referer", "origin", "accept-language", "sec-fetch-mode"):
            self.assertNotIn(forbidden, headers)
        self.assertEqual(headers["authorization"], "Bearer jvs_secret_token")
        self.assertEqual(headers["connection"], "close")

    def test_environment_proxies_are_ignored(self):
        proxy = support.StubServer(status=200).start()
        self.addCleanup(proxy.stop)
        server = support.LiveServer().start()
        self.addCleanup(server.stop)
        with unittest.mock.patch.dict(os.environ, {
            "HTTP_PROXY": proxy.url, "HTTPS_PROXY": proxy.url,
            "http_proxy": proxy.url, "https_proxy": proxy.url,
        }):
            health = ServerApi(CompanionConfig(server.url)).health()
        self.assertEqual(health["status"], "ok")
        self.assertEqual(proxy.requests, [], "the companion used an environment proxy")

    def test_an_error_body_without_a_reason_still_maps_to_a_typed_failure(self):
        stub = support.StubServer(status=500, body=b"{}").start()
        self.addCleanup(stub.stop)
        with self.assertRaises(ServerUnreachable):
            self._api(stub).inbox("jvs_secret_token")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
