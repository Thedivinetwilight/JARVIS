"""Health, readiness, limitation and capability reporting — and the promise that nothing is
claimed as implemented."""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from app.jarvis import capabilities, manifests, reporting
from app.jarvis.egress import GUARD
from app.tests.support import ApiTestCase, build_app

REPO = pathlib.Path(__file__).resolve().parents[2]

#: Exact, published product-specification statuses. If the specification moves, this
#: register and the JSON must move with it — the parity test below enforces that.
EXPECTED_IDS = [f"C-{index:02d}" for index in range(1, 15)]


class CapabilityParityTests(unittest.TestCase):
    """`app/jarvis/capabilities.py` mirrors `docs/product/capabilities.json` exactly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((REPO / "docs" / "product" / "capabilities.json").read_text(
            encoding="utf-8"))

    def test_spec_version_matches_the_published_specification(self):
        self.assertEqual(capabilities.SPEC_VERSION, self.spec["spec_version"])

    def test_every_capability_id_and_status_matches_the_json(self):
        published = [(item["id"], item["title"], item["status"])
                     for item in self.spec["capabilities"]]
        self.assertEqual([row[0] for row in capabilities.PRODUCT_CAPABILITIES], EXPECTED_IDS)
        self.assertEqual(list(capabilities.PRODUCT_CAPABILITIES), published)

    def test_no_product_capability_is_reported_as_implemented(self):
        for entry in capabilities.capability_report():
            self.assertEqual(entry["implementation"], "not implemented", entry["id"])
            self.assertIn("specified", entry["spec_status"])
        self.assertEqual({entry["implementation"] for entry in capabilities.capability_report()},
                         {"not implemented"})

    def test_infrastructure_report_is_separately_named_and_never_claims_a_capability(self):
        entries = capabilities.infrastructure_report()
        components = {entry["component"] for entry in entries}
        for component in ("tasks", "reminders", "auth.pairing", "egress", "storage"):
            self.assertIn(component, components)
        model = next(entry for entry in entries if entry["component"] == "model")
        self.assertEqual(model["status"], "not-selected")
        for entry in entries:
            self.assertFalse(entry["component"].startswith("C-"))
            self.assertNotIn("capability", entry["component"])


class ReportingTests(ApiTestCase):
    def test_health_is_public_and_carries_no_secret(self):
        response, payload = self.call("GET", "/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertNotIn("secret", json.dumps(payload).lower())

    def test_readiness_requires_migrations_and_the_guard(self):
        GUARD.uninstall()
        blocked, payload = self.call("GET", "/readyz")
        self.assertEqual(blocked.status, 503)
        self.assertFalse(payload["ready"])
        self.assertFalse(payload["checks"]["egress_guard"]["ok"])
        GUARD.install()
        self.addCleanup(GUARD.uninstall)
        ready, payload = self.call("GET", "/readyz")
        self.assertEqual(ready.status, 200)
        self.assertTrue(payload["ready"])
        self.assertTrue(payload["checks"]["adapters"]["ok"])

    def test_readiness_reports_pending_migrations(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        app, _config = build_app(directory, migrate=False)
        GUARD.install()
        self.addCleanup(GUARD.uninstall)
        response = app.request("GET", "/readyz")
        payload = json.loads(response.body)
        self.assertEqual(response.status, 503)
        self.assertFalse(payload["checks"]["database"]["ok"])
        self.assertIn("0001_core.sql", payload["checks"]["database"]["detail"])

    def test_status_reports_the_server_without_claiming_capabilities(self):
        response, payload = self.call("GET", "/v1/status", token=self.operator_token)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["server"]["processing"], "local-only")
        self.assertEqual(payload["product_spec"]["spec_version"], capabilities.SPEC_VERSION)
        implementations = {item["implementation"]
                           for item in payload["product_spec"]["capabilities"]}
        self.assertEqual(implementations, {"not implemented"})
        self.assertEqual(len(payload["product_spec"]["capabilities"]), 14)
        self.assertNotIn("secret", json.dumps(payload["config"]).lower())
        self.assertTrue(payload["storage"]["migrations_applied"])

    def test_limitations_are_stable_coded_and_honest(self):
        response, payload = self.call("GET", "/v1/limitations", token=self.operator_token)
        self.assertEqual(response.status, 200)
        codes = {item["code"] for item in payload["limitations"]}
        for expected in (
            "speech_asr_unavailable",
            "speech_tts_unavailable",
            "text_conversation_not_supported",
            "holographic_surface_no_display",
            "no_model_selected",
            "language_support_unmeasured",
            "external_services_disabled",
            "no_client_applications",
            "reminder_delivery_unavailable",
            "natural_language_entry_unavailable",
            "device_platform_roles_not_implemented",
        ):
            self.assertIn(expected, codes)
        self.assertEqual(payload["count"], len(payload["limitations"]))
        rendered = json.dumps(payload).lower()
        self.assertNotIn("fully working", rendered)
        # The only mentions of chat are the ones that say there is none.
        self.assertIn("no text-chat endpoint exists", rendered)
        self.assertNotIn("chat endpoint available", rendered)

    def test_the_model_limitation_states_that_no_model_is_chosen(self):
        entry = next(item for item in reporting.limitation_catalogue()
                     if item["code"] == "no_model_selected")
        text = json.dumps(entry).lower()
        self.assertIn("gate g-o1 is blocked", text)
        self.assertIn("nothing here reasons about language", text)
        # A limitation entry may not smuggle in a vendor or purchase hint.
        for banned in ("gpu", "purchase", "price", "btf", "flops", "tops"):
            self.assertNotIn(banned, text)

    def test_every_limitation_names_its_affects_and_a_reference(self):
        for entry in reporting.limitation_catalogue():
            self.assertTrue(entry["affects"], entry["code"])
            for capability_id in entry["affects"]:
                self.assertRegex(capability_id, r"^C-\d{2}$")
            self.assertTrue(entry["spec_reference"], entry["code"])
            self.assertTrue(entry["title"], entry["code"])
            self.assertTrue(entry["detail"], entry["code"])


class SurfaceTests(ApiTestCase):
    def test_surfaces_are_boundary_only_and_kept_distinct(self):
        response, payload = self.call("GET", "/v1/surfaces", token=self.operator_token)
        self.assertEqual(response.status, 200)
        surfaces = {item["id"]: item for item in payload["surfaces"]}
        self.assertIn("surface.holographic", surfaces)
        for item in payload["surfaces"]:
            self.assertIn(item["status"], ("boundary-only", "not-a-target"))
            self.assertFalse(item["implemented"], item["id"])
        notes = " ".join(payload["notes"]).lower()
        self.assertIn("not vr", notes)
        self.assertIn("not ray-ban glasses", notes)
        self.assertIn("not edith glass", notes)
        self.assertIn("no optics", notes)
        self.assertFalse(payload["verified"])

    def test_edith_glass_is_not_a_target_of_this_server(self):
        edith = next(item for item in manifests.MANIFESTS if "edith" in item.id)
        self.assertEqual(edith.status, "not-a-target")

    def test_manifests_list_scopes_and_report_disabled_connectors(self):
        response, payload = self.call("GET", "/v1/manifests", token=self.operator_token)
        self.assertEqual(response.status, 200)
        by_id = {item["id"]: item for item in payload["manifests"]}
        self.assertEqual(by_id["tasks"]["status"], "available")
        self.assertEqual(by_id["reminders"]["status"], "available")
        self.assertEqual(by_id["speech.asr"]["status"], "unavailable")
        self.assertEqual(by_id["speech.tts"]["status"], "unavailable")
        connectors = [item for item in payload["manifests"] if item["kind"] == "connector"]
        self.assertTrue(connectors)
        for connector in connectors:
            self.assertFalse(connector["servable"])
        self.assertTrue(set(payload["granted_scopes"]) <= set(payload["known_scopes"]))

    def test_config_endpoint_never_returns_the_secret(self):
        response, payload = self.call("GET", "/v1/config", token=self.operator_token)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["secret"], "not returned, not logged, not stored in the repository")
        self.assertNotIn("server_secret", json.dumps(payload))

    def test_egress_policy_endpoint_shows_deny_by_default(self):
        response, payload = self.call("GET", "/v1/egress/policy", token=self.operator_token)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["default"], "deny")
        self.assertTrue(all(not item["enabled"] for item in payload["declared_external_services"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
