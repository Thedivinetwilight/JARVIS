"""The HTTP adapter: real sockets, real bytes, no framework."""

from __future__ import annotations

import http.client
import json
import pathlib
import tempfile
import unittest

from app.jarvis.egress import GUARD
from app.jarvis.http import bound_address, build_server
from app.tests.support import build_app


class HTTPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = pathlib.Path(self._tmp.name)
        self.app, self.config = build_app(self.directory)
        GUARD.install()
        self.addCleanup(GUARD.uninstall)
        # Port 0: the kernel picks a free port, so tests never collide.
        object.__setattr__(self.config.server, "port", 0)
        self.server = build_server(self.config, self.app)
        self.host, self.port = bound_address(self.server)
        self.server.timeout = 0.2
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    # ------------------------------------------------------------------ helpers
    def request(self, method: str, path: str, *, body: bytes | None = None,
                headers: dict[str, str] | None = None, connection=None):
        owned = connection is None
        connection = connection or http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            return response, payload
        finally:
            if owned:
                connection.close()

    def json_request(self, method: str, path: str, *, token: str | None = None,
                     body: dict | None = None, connection=None):
        headers = {"content-type": "application/json"} if body is not None else {}
        if token:
            headers["authorization"] = f"Bearer {token}"
        encoded = json.dumps(body).encode() if body is not None else None
        response, payload = self.request(method, path, body=encoded, headers=headers,
                                         connection=connection)
        return response, json.loads(payload) if payload else None

    # ------------------------------------------------------------------ tests
    def test_health_over_the_wire(self):
        response, payload = self.request("GET", "/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(payload)["status"], "ok")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")

    def test_protected_routes_need_a_bearer_token(self):
        response, payload = self.request("GET", "/v1/status")
        self.assertEqual(response.status, 401)
        self.assertEqual(json.loads(payload)["error"]["code"], "token_missing")

    def test_pairing_then_task_over_http(self):
        info = self.app.auth.create_pairing_code(label_hint="tablet", platform="tablet")
        response, payload = self.json_request(
            "POST", "/v1/pairing",
            body={"code": info["pairing_code"], "label": "kitchen", "platform": "tablet"},
        )
        self.assertEqual(response.status, 201)
        token = payload["token"]
        created, payload = self.json_request("POST", "/v1/tasks", token=token,
                                             body={"title": "over the wire"})
        self.assertEqual(created.status, 201)
        listed, payload = self.json_request("GET", "/v1/tasks", token=token)
        self.assertEqual(listed.status, 200)
        self.assertEqual(payload["count"], 1)

    def test_a_body_larger_than_the_limit_is_refused_without_reading_it(self):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.putrequest("POST", "/v1/tasks")
        connection.putheader("content-type", "application/json")
        connection.putheader("content-length", str(self.config.server.max_request_bytes + 1))
        connection.endheaders()
        response = connection.getresponse()
        payload = json.loads(response.read())
        self.assertEqual(response.status, 413)
        self.assertEqual(payload["error"]["code"], "body_too_large")

    def test_unsupported_methods_are_refused_by_the_adapter(self):
        response, payload = self.request("PUT", "/v1/tasks")
        self.assertEqual(response.status, 405)
        self.assertEqual(json.loads(payload)["error"]["code"], "method_not_allowed")
        options, _ = self.request("OPTIONS", "/v1/tasks")
        self.assertEqual(options.status, 405)
        # No permissive CORS headers anywhere.
        self.assertIsNone(options.getheader("Access-Control-Allow-Origin"))

    def test_unknown_route_is_json_not_html(self):
        response, payload = self.request("GET", "/v1/nothing")
        self.assertEqual(response.status, 404)
        self.assertIn("application/json", response.getheader("Content-Type"))
        self.assertEqual(json.loads(payload)["error"]["code"], "route_not_found")

    def test_connections_are_reusable_and_requests_are_independent(self):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        self.addCleanup(connection.close)
        first, _ = self.request("GET", "/healthz", connection=connection)
        second, _ = self.request("GET", "/readyz", connection=connection)
        third, _ = self.request("GET", "/healthz", connection=connection)
        self.assertEqual([first.status, second.status, third.status], [200, 200, 200])

    def test_bad_content_length_is_rejected(self):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.putrequest("POST", "/v1/tasks")
        connection.putheader("content-length", "not-a-number")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.read())["error"]["code"], "bad_content_length")

    def test_the_server_does_not_answer_requests_from_other_origins(self):
        # There is no CORS support at all, by design: a browser page cannot drive this server.
        response, _ = self.request("GET", "/healthz",
                                   headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
