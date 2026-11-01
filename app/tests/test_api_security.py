"""Scope enforcement, request bounding and audit completeness at the API boundary."""

from __future__ import annotations

import json
import unittest

from app.jarvis import manifests
from app.tests.support import ApiTestCase


class ScopeTests(ApiTestCase):
    def test_a_device_cannot_list_clients_or_read_the_audit(self):
        _, token = self.pair_device()
        for path in ("/v1/clients", "/v1/audit"):
            with self.subTest(path=path):
                response, payload = self.call("GET", path, token=token)
                self.assertEqual(response.status, 403)
                self.assertEqual(payload["error"]["code"], "scope_denied")
                self.assertEqual(payload["error"]["detail"]["required_scope"],
                                 manifests.SCOPE_CLIENTS_READ if "clients" in path
                                 else manifests.SCOPE_AUDIT_READ)

    def test_a_device_cannot_revoke_another_client(self):
        victim, _ = self.pair_device(label="victim")
        _, attacker_token = self.pair_device(label="attacker")
        response, payload = self.call("POST", f"/v1/clients/{victim['id']}/revoke",
                                     token=attacker_token)
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["code"], "scope_denied")
        self.assertIsNone(self.app.auth.get_client(victim["id"]).revoked_at)

    def test_a_device_can_revoke_itself(self):
        client, token = self.pair_device()
        response, payload = self.call("POST", f"/v1/clients/{client['id']}/revoke", token=token)
        self.assertEqual(response.status, 200)
        self.assertIsNotNone(payload["client"]["revoked_at"])
        after, payload = self.call("GET", "/v1/status", token=token)
        self.assertEqual(after.status, 401)
        self.assertEqual(payload["error"]["code"], "token_revoked")

    def test_a_narrowly_scoped_device_is_denied_everything_else(self):
        _, token = self.pair_device(scopes=["tasks.read"])
        allowed, _ = self.call("GET", "/v1/tasks", token=token)
        self.assertEqual(allowed.status, 200)
        denied, payload = self.call("POST", "/v1/tasks", token=token, body={"title": "x"})
        self.assertEqual(denied.status, 403)
        self.assertEqual(payload["error"]["detail"]["required_scope"], "tasks.write")
        no_audio, payload = self.call("POST", "/v1/audio/sessions", token=token)
        self.assertEqual(no_audio.status, 403)
        self.assertEqual(payload["error"]["detail"]["required_scope"], "audio.capture")

    def test_unavailable_capabilities_are_503_even_for_the_operator(self):
        response, payload = self.call("GET", "/v1/audio/output", token=self.operator_token)
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["code"], "manifest_unavailable")
        self.assertEqual(payload["error"]["detail"]["manifest"], "speech.tts")
        self.assertEqual(payload["error"]["detail"]["status"], "unavailable")


class BoundingTests(ApiTestCase):
    def test_unknown_route_is_a_404_with_or_without_a_token(self):
        for token in (None, self.operator_token):
            with self.subTest(token=bool(token)):
                response, payload = self.call("GET", "/v1/conversation", token=token)
                self.assertEqual(response.status, 404)
                self.assertEqual(payload["error"]["code"], "route_not_found")

    def test_there_is_no_text_conversation_surface(self):
        _, token = self.pair_device()
        for method, path in (
            ("POST", "/v1/chat"),
            ("POST", "/v1/completions"),
            ("POST", "/v1/messages"),
            ("POST", "/v1/conversation"),
            ("POST", "/v1/ask"),
        ):
            with self.subTest(path=path):
                response, payload = self.call(method, path, token=token, body={"text": "hi"})
                self.assertEqual(response.status, 404)
                self.assertEqual(payload["error"]["code"], "route_not_found")

    def test_wrong_method_is_a_405_naming_the_allowed_methods(self):
        response, payload = self.call("PUT", "/v1/tasks", token=self.operator_token)
        self.assertEqual(response.status, 405)
        self.assertEqual(payload["error"]["detail"]["allowed"], ["GET", "POST"])

    def test_a_body_on_a_bodyless_route_is_refused(self):
        client, token = self.pair_device()
        response, payload = self.call("POST", f"/v1/clients/{client['id']}/revoke",
                                     token=token, body={"reason": "x"})
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "body_not_allowed")

    def test_oversized_body_is_refused_before_parsing(self):
        _, token = self.pair_device()
        big = b"{" + b"a" * (self.config.server.max_request_bytes + 10) + b"}"
        response, payload = self.call("POST", "/v1/tasks", token=token, body=big)
        self.assertEqual(response.status, 413)
        self.assertEqual(payload["error"]["code"], "body_too_large")

    def test_wrong_content_type_and_broken_json_are_refused(self):
        _, token = self.pair_device()
        wrong, payload = self.call("POST", "/v1/tasks", token=token, body=b"hello",
                                   content_type="text/plain")
        self.assertEqual(wrong.status, 415)
        self.assertEqual(payload["error"]["code"], "unsupported_media_type")
        broken, payload = self.call("POST", "/v1/tasks", token=token, body=b"{not json")
        self.assertEqual(broken.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_json")
        listed, payload = self.call("POST", "/v1/tasks", token=token, body=b"[1,2,3]")
        self.assertEqual(listed.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_json")

    def test_path_traversal_is_refused(self):
        response, payload = self.call("GET", "/v1/../etc/passwd", token=self.operator_token)
        self.assertIn(response.status, (400, 404))
        self.assertIn(payload["error"]["code"], ("invalid_path", "route_not_found"))

    def test_request_ids_are_echoed_or_generated(self):
        supplied, _ = self.call("GET", "/healthz", headers={"x-request-id": "req_caller-0001"})
        self.assertEqual(supplied.headers["x-request-id"], "req_caller-0001")
        generated, _ = self.call("GET", "/healthz")
        self.assertTrue(generated.headers["x-request-id"].startswith("req_"))
        injected, _ = self.call("GET", "/healthz", headers={"x-request-id": "bad id <script>"})
        self.assertNotEqual(injected.headers["x-request-id"], "bad id <script>")


class AuditTests(ApiTestCase):
    def test_every_request_outcome_lands_in_the_audit_trail(self):
        _, token = self.pair_device()
        self.call("GET", "/v1/status", token=token)
        self.call("GET", "/v1/status")
        self.call("GET", "/v1/clients", token=token)
        self.call("POST", "/v1/tasks", token=token, body={"title": "audited"})
        entries = self.audit_entries()
        outcomes = {(entry["action"], entry["outcome"]) for entry in entries}
        self.assertIn(("GET status", "allowed"), outcomes)
        self.assertIn(("POST tasks.create", "allowed"), outcomes)
        self.assertIn(("tasks.created", "allowed"), outcomes)
        # A scope denial is written by the pipeline (before the handler) with the raw path.
        self.assertIn(("GET /v1/clients", "denied"), outcomes)
        self.assertTrue(any(entry["outcome"] == "denied" and entry["actor_kind"] == "anonymous"
                            for entry in entries))

    def test_audit_records_pairing_and_revocation_without_secrets(self):
        info = self.app.auth.create_pairing_code(label_hint="tablet", platform="tablet")
        client, token = self.pair_device()
        self.call("POST", f"/v1/clients/{client['id']}/revoke", token=self.operator_token)
        rendered = json.dumps([entry for entry in self.audit_entries()])
        actions = {entry["action"] for entry in self.audit_entries()}
        self.assertIn("pairing.code_created", actions)
        self.assertIn("pairing.completed", actions)
        self.assertIn("client.revoked", actions)
        self.assertNotIn(token, rendered)
        self.assertNotIn(info["pairing_code"], rendered)
        self.assertNotIn(token[len("jvs_"):], rendered)

    def test_audit_detail_keeps_error_codes_for_diagnosis(self):
        self.call("GET", "/v1/status")
        entry = self.audit_entries()[0]
        self.assertEqual(entry["detail"]["code"], "token_missing")
        self.assertEqual(entry["detail"]["status"], 401)

    def test_audit_tail_respects_limits_and_is_newest_first(self):
        for _ in range(5):
            self.call("GET", "/healthz")
        response, payload = self.call("GET", "/v1/audit?limit=2", token=self.operator_token)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["count"], 2)
        ids = [entry["id"] for entry in payload["entries"]]
        self.assertEqual(ids, sorted(ids, reverse=True))
        clamped, payload = self.call("GET", "/v1/audit?limit=100000", token=self.operator_token)
        self.assertLessEqual(payload["count"], 500)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
