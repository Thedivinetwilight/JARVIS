"""Client pairing, token lifetime and revocation."""

from __future__ import annotations

import json
import unittest

from app.jarvis import manifests
from app.jarvis.errors import (
    BadRequest,
    NotFound,
    TokenExpired,
    TokenRevoked,
    Unauthenticated,
)
from app.tests.support import ApiTestCase

SECRET = "unit-test-secret-0123456789abcdefghijklmnop"


class PairingTests(ApiTestCase):
    def test_pairing_code_is_single_use_and_returns_a_scoped_device_token(self):
        info = self.app.auth.create_pairing_code(label_hint="kitchen tablet", platform="tablet")
        # Typed-human format: three groups of four characters from an unambiguous alphabet.
        self.assertRegex(info["pairing_code"], r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertEqual(info["scopes"], sorted(manifests.DEFAULT_DEVICE_SCOPES))
        response, payload = self.call(
            "POST", "/v1/pairing",
            body={"code": info["pairing_code"], "label": "kitchen", "platform": "tablet"},
        )
        self.assertEqual(response.status, 201)
        self.assertTrue(payload["token"].startswith("jvs_"), payload)
        self.assertEqual(payload["client"]["kind"], "device")
        self.assertEqual(response.headers["cache-control"], "no-store")

        again, payload = self.call(
            "POST", "/v1/pairing",
            body={"code": info["pairing_code"], "label": "kitchen", "platform": "tablet"},
        )
        self.assertEqual(again.status, 401)
        self.assertEqual(payload["error"]["code"], "pairing_code_invalid")

    def test_pairing_code_can_be_typed_with_spaces_and_lower_case(self):
        info = self.app.auth.create_pairing_code(label_hint="tablet", platform="tablet")
        messy = "  " + info["pairing_code"].lower().replace("-", " ") + "  "
        response, _ = self.call("POST", "/v1/pairing",
                                body={"code": messy, "label": "t", "platform": "tablet"})
        self.assertEqual(response.status, 201)

    def test_expired_pairing_code_is_refused(self):
        info = self.app.auth.create_pairing_code(label_hint="tablet", platform="tablet",
                                                  ttl_minutes=5)
        self.clock.advance(minutes=6)
        response, payload = self.call("POST", "/v1/pairing",
                                      body={"code": info["pairing_code"], "label": "t",
                                            "platform": "tablet"})
        self.assertEqual(response.status, 401)
        self.assertEqual(payload["error"]["code"], "pairing_code_invalid")

    def test_unknown_pairing_code_is_refused_and_audited(self):
        response, payload = self.call("POST", "/v1/pairing",
                                      body={"code": "prc-0000-0000-0000-0000", "label": "t",
                                            "platform": "tablet"})
        self.assertEqual(response.status, 401)
        actions = [(entry["action"], entry["outcome"]) for entry in self.audit_entries()]
        self.assertIn(("pairing.attempt", "denied"), actions)

    def test_repeated_poor_attempts_are_throttled(self):
        for _ in range(self.config.auth.pairing_max_failed_attempts):
            self.call("POST", "/v1/pairing",
                      body={"code": "prc-1111-2222-3333-4444", "label": "t", "platform": "tablet"})
        info = self.app.auth.create_pairing_code(label_hint="tablet", platform="tablet")
        response, payload = self.call("POST", "/v1/pairing",
                                      body={"code": info["pairing_code"], "label": "t",
                                            "platform": "tablet"})
        self.assertEqual(response.status, 401)
        self.assertEqual(payload["error"]["code"], "pairing_throttled")
        # The throttle window is short, not permanent: once it has passed, a fresh code works.
        self.clock.advance(minutes=11)
        fresh = self.app.auth.create_pairing_code(label_hint="tablet", platform="tablet")
        response, _ = self.call("POST", "/v1/pairing",
                                body={"code": fresh["pairing_code"], "label": "t",
                                      "platform": "tablet"})
        self.assertEqual(response.status, 201)

    def test_pairing_code_may_not_carry_administrative_scopes(self):
        with self.assertRaises(BadRequest) as caught:
            self.app.auth.create_pairing_code(label_hint="tablet", platform="tablet",
                                              scopes=["clients.revoke"])
        self.assertEqual(caught.exception.code, "scope_not_grantable")

    def test_pairing_code_rejects_unknown_or_empty_scopes(self):
        with self.assertRaises(BadRequest) as caught:
            self.app.auth.create_pairing_code(label_hint="tablet", platform="tablet",
                                              scopes=["router.admin"])
        self.assertEqual(caught.exception.code, "unknown_scope")
        with self.assertRaises(BadRequest) as caught:
            self.app.auth.create_pairing_code(label_hint="tablet", platform="tablet", scopes=[])
        self.assertEqual(caught.exception.code, "scope_required")

    def test_pairing_requires_a_label_hint_and_platform(self):
        with self.assertRaises(BadRequest):
            self.app.auth.create_pairing_code(label_hint="  ", platform="tablet")
        with self.assertRaises(BadRequest):
            self.app.auth.create_pairing_code(label_hint="tablet", platform="")

    def test_paired_device_receives_auditable_per_manifest_grants(self):
        client, _ = self.pair_device()
        grants = self.app.auth.grants_for(client["id"])
        self.assertTrue(grants)
        for grant in grants:
            self.assertIn(grant["manifest_id"], {m.id for m in manifests.MANIFESTS})
            self.assertTrue(grant["scopes"])
            self.assertIsNone(grant["revoked_at"])
        self.assertEqual({g["manifest_id"] for g in grants},
                         {"tasks", "reminders", "speech.asr", "audio.session"} - {"speech.asr"})


class TokenTests(ApiTestCase):
    def test_operator_token_carries_administrative_scopes(self):
        self.assertTrue(self.operator.is_operator)
        for scope in ("clients.read", "clients.revoke", "audit.read", "tasks.write"):
            self.assertTrue(self.operator.has_scope(scope), scope)
        response, payload = self.call("GET", "/v1/clients/me", token=self.operator_token)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["client"]["kind"], "operator")

    def test_tokens_are_stored_only_as_digests(self):
        client, token = self.pair_device()
        with self.app.store.connection() as connection:
            dump = "\n".join(
                str(value)
                for table in ("clients", "pairing_codes", "client_grants")
                for row in connection.execute(f"SELECT * FROM {table}").fetchall()
                for value in tuple(row)
            )
        self.assertNotIn(token, dump)
        self.assertNotIn(token[len("jvs_"):], dump)
        self.assertIn(client["token_prefix"], dump)
        self.assertNotEqual(client["token_prefix"], token[:8])

    def test_missing_malformed_and_unknown_tokens_are_rejected(self):
        missing, payload = self.call("GET", "/v1/status")
        self.assertEqual(missing.status, 401)
        self.assertEqual(payload["error"]["code"], "token_missing")
        malformed, payload = self.call("GET", "/v1/status", token="not-a-token")
        self.assertEqual(malformed.status, 401)
        self.assertEqual(payload["error"]["code"], "token_invalid")
        unknown, payload = self.call("GET", "/v1/status", token="jvs_" + "0" * 40)
        self.assertEqual(unknown.status, 401)
        self.assertEqual(payload["error"]["code"], "token_invalid")

    def test_expired_token_is_rejected(self):
        _, token = self.pair_device()
        self.clock.advance(days=self.config.auth.device_token_ttl_days + 1)
        response, payload = self.call("GET", "/v1/status", token=token)
        self.assertEqual(response.status, 401)
        self.assertEqual(payload["error"]["code"], "token_expired")
        with self.assertRaises(TokenExpired):
            self.app.auth.authenticate(token)

    def test_revoked_token_stops_working_immediately(self):
        client, token = self.pair_device()
        response, _ = self.call("POST", f"/v1/clients/{client['id']}/revoke",
                                token=self.operator_token)
        self.assertEqual(response.status, 200)
        after, payload = self.call("GET", "/v1/status", token=token)
        self.assertEqual(after.status, 401)
        self.assertEqual(payload["error"]["code"], "token_revoked")
        with self.assertRaises(TokenRevoked):
            self.app.auth.authenticate(token)

    def test_revocation_is_idempotent_and_revokes_grants(self):
        client, _ = self.pair_device()
        for _ in range(2):
            response, payload = self.call("POST", f"/v1/clients/{client['id']}/revoke",
                                          token=self.operator_token)
            self.assertEqual(response.status, 200)
            self.assertIsNotNone(payload["client"]["revoked_at"])
        for grant in self.app.auth.grants_for(client["id"]):
            self.assertIsNotNone(grant["revoked_at"])

    def test_revoking_an_unknown_client_is_a_404(self):
        response, payload = self.call("POST", "/v1/clients/cli_missing/revoke",
                                      token=self.operator_token)
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"]["code"], "client_not_found")
        with self.assertRaises(NotFound):
            self.app.auth.get_client("cli_missing")

    def test_client_listing_never_exposes_digests_or_tokens(self):
        self.pair_device()
        response, payload = self.call("GET", "/v1/clients", token=self.operator_token)
        self.assertEqual(response.status, 200)
        rendered = json.dumps(payload)
        self.assertNotIn("token_hash", rendered)
        self.assertNotIn(self.operator_token, rendered)
        for client in payload["clients"]:
            self.assertEqual(len(client["token_prefix"]), 8)
        self.assertEqual(len(payload["clients"]), 2)
        kinds = {client["kind"] for client in payload["clients"]}
        self.assertEqual(kinds, {"operator", "device"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
