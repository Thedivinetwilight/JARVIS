"""Consistency checks for the product capability specification (v1.1.0).

These test the *documents*, not the product: the product does not exist yet. What they
protect is that the normative requirements, the machine-readable index and the roadmap
cannot drift apart, that the owner's decisions stay recorded as decisions, that the local
processing boundary is never reopened, and that no capability can be labelled implemented.
"""
import json
import re
import unittest
from pathlib import Path

from eval.harness.taskset import load_tasksets

REPO = Path(__file__).resolve().parents[2]
PRODUCT = REPO / "docs" / "product"
SPEC_MD = PRODUCT / "capability-specification.md"
ROADMAP_MD = PRODUCT / "capability-roadmap.md"
README_MD = PRODUCT / "README.md"
INDEX = PRODUCT / "capabilities.json"

ALLOWED_STATUSES = {"specified", "specified · harness-only", "blocked"}
REQUIRED_CAP_FIELDS = ("id", "title", "requirement", "status", "depends_on",
                       "acceptance_criteria", "data_and_permissions", "validated_by",
                       "today", "risks")
REQUIRED_PERMISSION_FIELDS = ("data_classes", "permissions", "retention", "sensitivity",
                              "approval_tier", "deletion", "processing")

#: A capability must never name a specific model, vendor or accelerator: that is G-O1's job.
BANNED_VENDOR_TOKENS = ("openai", "gpt-", "chatgpt", "anthropic", "claude", "gemini",
                        "llama", "qwen", "mistral", "deepseek", "h100", "a100", "rtx",
                        "cuda core")


def _index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def _capabilities(spec: dict) -> dict:
    return {cap["id"]: cap for cap in spec["capabilities"]}


class ProductSpecStructureTests(unittest.TestCase):
    def setUp(self):
        self.spec = _index()
        self.caps = self.spec["capabilities"]

    def test_spec_version_and_status_statement(self):
        self.assertEqual(self.spec["spec_version"], "1.1.0")
        statement = self.spec["status_statement"].lower()
        self.assertIn("implemented", statement)
        self.assertIn("harness inventory", statement)

    def test_twelve_capabilities_with_stable_ids(self):
        ids = [cap["id"] for cap in self.caps]
        self.assertEqual(ids, [f"C-{n:02d}" for n in range(1, 13)])
        self.assertEqual(len(set(ids)), len(ids))

    def test_every_capability_is_complete(self):
        for cap in self.caps:
            with self.subTest(capability=cap["id"]):
                for field in REQUIRED_CAP_FIELDS:
                    self.assertIn(field, cap, f"{cap['id']} missing {field}")
                self.assertTrue(cap["requirement"].strip())
                self.assertGreaterEqual(len(cap["acceptance_criteria"]), 2)
                self.assertTrue(cap["risks"])

    def test_no_capability_is_marked_implemented(self):
        for cap in self.caps:
            self.assertIn(cap["status"], ALLOWED_STATUSES, f"{cap['id']}: {cap['status']}")
            self.assertNotIn("implemented", cap["status"].lower())

    def test_acceptance_criteria_are_measurable(self):
        for cap in self.caps:
            for criterion in cap["acceptance_criteria"]:
                with self.subTest(criterion=criterion["id"]):
                    for field in ("id", "statement", "metric", "measurement",
                                  "validated_by", "result"):
                        self.assertIn(field, criterion)
                    self.assertGreater(len(criterion["metric"]), 10)
                    self.assertGreater(len(criterion["measurement"]), 10)
                    self.assertEqual(criterion["result"], "not measured")
                    self.assertTrue(criterion["provisional"])

    def test_dependencies_and_data_needs(self):
        known = {cap["id"] for cap in self.caps}
        data_classes = set(self.spec["data_classes"])
        verbs = set(self.spec["permission_verbs"])
        for cap in self.caps:
            deps = set(cap["depends_on"])
            self.assertTrue(deps <= known - {cap["id"]}, f"{cap['id']} bad dependency")
            permissions = cap["data_and_permissions"]
            for field in REQUIRED_PERMISSION_FIELDS:
                self.assertIn(field, permissions, f"{cap['id']} missing {field}")
            self.assertTrue(set(permissions["data_classes"]) <= data_classes)
            self.assertTrue(set(permissions["permissions"]) <= verbs)

    def test_every_capability_names_a_planned_milestone_and_family(self):
        milestone_ids = {m["id"] for m in self.spec["milestones"]}
        existing = set(self.spec["existing_families"])
        planned = set(self.spec["planned_families"])
        for cap in self.caps:
            validator = cap["validated_by"]
            self.assertTrue(validator["families"], f"{cap['id']} has no validator")
            self.assertTrue(set(validator["families"]) <= existing | planned)
            self.assertTrue(validator["milestones"])
            self.assertTrue(set(validator["milestones"]) <= milestone_ids)

    def test_processing_is_local_everywhere(self):
        for cap in self.caps:
            with self.subTest(capability=cap["id"]):
                processing = cap["data_and_permissions"]["processing"].lower()
                if cap["data_and_permissions"]["data_classes"]:
                    self.assertIn("local", processing,
                                  f"{cap['id']} does not state local processing")


class OwnerDecisionTests(unittest.TestCase):
    """The owner's decisions are requirements now, and must stay visible as such."""

    def setUp(self):
        self.spec = _index()
        self.caps = _capabilities(self.spec)
        self.spec_text = SPEC_MD.read_text(encoding="utf-8")

    def test_all_owner_decisions_are_recorded(self):
        ids = [decision["id"] for decision in self.spec["owner_decisions"]]
        self.assertEqual(ids, [f"O-{n}" for n in range(1, 8)])
        for decision in self.spec["owner_decisions"]:
            self.assertTrue(decision["decision"].strip())
            self.assertTrue(decision["affects"])
            self.assertEqual(decision["date"], "2026-11-01")
            self.assertIn(decision["id"], self.spec_text)

    def test_dedicated_server_and_local_only_processing(self):
        deployment = self.spec["deployment"]
        self.assertIn("dedicated server", deployment["target"].lower())
        for key in ("inference", "personal_data", "memory", "voice_processing",
                    "call_summaries"):
            with self.subTest(function=key):
                self.assertEqual(deployment[key], "local only")

    def test_local_boundary_lists_functions_and_forbids_cloud_ai(self):
        boundary = self.spec["processing_boundary"]
        mandatory = " ".join(boundary["local_mandatory"]).lower()
        for needle in ("inference", "speech recognition", "speaker verification",
                       "memory", "call summaries"):
            self.assertIn(needle, mandatory)
        prohibited = " ".join(boundary["prohibited"]).lower()
        self.assertIn("cloud", prohibited)
        self.assertIn("ai inference", prohibited)

    def test_unavoidable_external_services_are_identified_separately(self):
        services = self.spec["processing_boundary"]["external_services"]
        self.assertGreaterEqual(len(services), 3)
        names = " ".join(service["service"] for service in services).lower()
        self.assertIn("telephone", names)
        self.assertIn("web", names)
        for service in services:
            with self.subTest(service=service["service"]):
                for field in ("purpose", "data_leaving", "why_unavoidable", "controls"):
                    self.assertTrue(service.get(field), f"{service['service']} missing {field}")

    def test_local_versus_cloud_is_not_left_open(self):
        items = {item["id"]: item for item in self.spec["open_items"]}
        self.assertEqual(items["Q16"]["status"], "answered")
        self.assertIn("local", json.dumps(items["Q16"]).lower())
        # No open question may reopen the processing location.
        for item in self.spec["open_items"]:
            if item["status"] != "open":
                continue
            text = (item["item"] + " " + item.get("decision", "")).lower()
            self.assertNotIn("cloud or local", text)
            self.assertNotIn("local versus cloud", text)

    def test_local_and_scope_gates_exist(self):
        gates = {gate["id"]: gate for gate in self.spec["gates"]}
        self.assertIn("G-LOCAL", gates)
        self.assertIn("G-SCOPE", gates)
        self.assertIn("cloud", json.dumps(gates["G-LOCAL"]).lower())
        self.assertIn("blanket", json.dumps(gates["G-SCOPE"]).lower())

    def test_call_policy_is_a_proposal_not_a_requirement(self):
        call = self.caps["C-05"]
        self.assertNotIn("refuse", call["requirement"].lower())
        proposals = call.get("proposed_policies", [])
        self.assertTrue(proposals, "C-05 must carry proposed policy P-1")
        self.assertEqual(proposals[0]["id"], "P-1")
        self.assertIn("awaiting owner review", proposals[0]["status"])
        self.assertEqual(self.spec["proposed_policies"][0]["id"], "P-1")
        self.assertIn("awaiting owner review", self.spec_text.lower())
        self.assertIn("P-1", self.spec_text)

    def test_multilingual_coverage_and_honest_tiers(self):
        criteria = {c["id"]: c for c in self.caps["C-03"]["acceptance_criteria"]}
        multilingual = [c for c in criteria.values() if "B19" in c["validated_by"]]
        self.assertGreaterEqual(len(multilingual), 2)
        text = " ".join(c["statement"] + " " + c["metric"] + " " + c["measurement"]
                        for c in multilingual).lower()
        self.assertIn("unsupported", text)
        self.assertIn("per language", text)
        self.assertIn("B19", json.dumps(self.spec["planned_families"]))

    def test_voice_speed_is_provisional_and_server_measured(self):
        latency = next(c for c in self.caps["C-03"]["acceptance_criteria"]
                       if "latency" in (c["statement"] + c["metric"]).lower()
                       or "speed" in c["statement"].lower())
        self.assertIn("server", latency["measurement"].lower())
        self.assertIn("provisional", latency["metric"].lower())

    def test_smart_home_is_a_capability_with_its_own_family(self):
        home = self.caps["C-11"]
        self.assertIn("smart-home", home["title"].lower())
        families = {c["validated_by"] for c in home["acceptance_criteria"]}
        self.assertIn("B18", families)
        self.assertIn("B18", self.spec["planned_families"])
        self.assertIn("permission", home["requirement"].lower())

    def test_connectors_are_scoped_and_revocable(self):
        connectors = self.caps["C-12"]
        text = (connectors["requirement"] + " " + " ".join(
            c["statement"] for c in connectors["acceptance_criteria"])).lower()
        for needle in ("scoped", "revocation", "audit", "blanket"):
            self.assertIn(needle, text)
        self.assertIn("B20", connectors["validated_by"]["families"])


class FamilyRealityTests(unittest.TestCase):
    """A family is either built (it exists on disk) or declared planned. Never both."""

    def test_existing_families_are_built_and_planned_ones_are_not(self):
        spec = _index()
        built = set(load_tasksets(REPO / "eval" / "tasks"))
        self.assertEqual(set(spec["existing_families"]), built)
        self.assertFalse(set(spec["planned_families"]) & built)

    def test_harness_only_capabilities_point_at_built_families(self):
        spec = _index()
        built = set(load_tasksets(REPO / "eval" / "tasks"))
        for cap in spec["capabilities"]:
            if cap["status"] == "specified · harness-only":
                with self.subTest(capability=cap["id"]):
                    self.assertTrue(cap["today"]["fixtures"],
                                    f"{cap['id']} claims harness evidence but lists none")
                    named = " ".join(cap["today"]["fixtures"])
                    self.assertTrue(any(block in named for block in built),
                                    f"{cap['id']} cites no built family: {named}")


class RoadmapAndGateTests(unittest.TestCase):
    def setUp(self):
        self.spec = _index()

    def test_completed_milestones_have_evidence_on_disk(self):
        for milestone in self.spec["milestones"]:
            if milestone["status"] != "complete":
                continue
            with self.subTest(milestone=milestone["id"]):
                self.assertIn("evidence", milestone)
                self.assertTrue((REPO / milestone["evidence"]).is_file())
        complete = [m["id"] for m in self.spec["milestones"] if m["status"] == "complete"]
        self.assertEqual(complete, ["M1", "M2", "M3", "M4"])

    def test_planned_milestones_have_exit_criteria_and_dependencies(self):
        ids = {m["id"] for m in self.spec["milestones"]}
        planned = [m for m in self.spec["milestones"] if m["status"] != "complete"]
        self.assertEqual([m["id"] for m in planned], [f"M{n}" for n in range(5, 16)])
        for milestone in planned:
            with self.subTest(milestone=milestone["id"]):
                self.assertTrue(milestone["exit_criteria"])
                self.assertTrue(milestone["depends_on"])
                self.assertTrue(set(milestone["depends_on"]) <= ids)

    def test_model_selection_is_still_blocked_behind_the_updated_mapping(self):
        gates = {gate["id"]: gate for gate in self.spec["gates"]}
        gate = gates["G-O1"]
        self.assertEqual(gate["status"], "blocked")
        self.assertTrue(any("M5" in requirement for requirement in gate["requires"]))
        self.assertTrue(any("updated" in requirement.lower()
                            for requirement in gate["requires"]))
        self.assertIn("M15", gate["blocks"])
        mapping = next(m for m in self.spec["milestones"] if m["id"] == "M5")
        self.assertEqual(mapping["status"], "planned")
        adoption = next(m for m in self.spec["milestones"] if m["id"] == "M15")
        self.assertEqual(adoption["status"], "planned")
        self.assertIn("M5", adoption["depends_on"])

    def test_no_chat_box_gate_exists(self):
        gates = {gate["id"]: gate for gate in self.spec["gates"]}
        self.assertIn("G-UX", gates)
        self.assertIn("chat", json.dumps(gates["G-UX"]).lower())


class MarkdownAgreementTests(unittest.TestCase):
    """The readable documents and the machine-readable index must say the same thing."""

    def setUp(self):
        self.spec = _index()
        self.spec_text = SPEC_MD.read_text(encoding="utf-8")
        self.roadmap_text = ROADMAP_MD.read_text(encoding="utf-8")
        self.readme_text = README_MD.read_text(encoding="utf-8")

    def test_every_capability_appears_in_both_documents(self):
        lowered = self.spec_text.lower()
        for cap in self.spec["capabilities"]:
            with self.subTest(capability=cap["id"]):
                self.assertIn(cap["id"], self.spec_text)
                self.assertIn(cap["id"], self.readme_text)
                needle = cap["title"].split(" (")[0].split(" and ")[0][:24].lower()
                self.assertIn(needle, lowered)

    def test_roadmap_mentions_every_milestone_and_gate(self):
        for milestone in self.spec["milestones"]:
            self.assertIn(milestone["id"], self.roadmap_text)
        for gate in self.spec["gates"]:
            self.assertIn(gate["id"], self.roadmap_text)

    def test_summary_table_statuses_match_the_index(self):
        rows = {}
        for line in self.spec_text.splitlines():
            if not line.startswith("| C-"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) >= 3 and re.fullmatch(r"C-\d{2}", cells[0]):
                rows[cells[0]] = cells[2]
        self.assertEqual(len(rows), 12, f"summary table rows: {sorted(rows)}")
        for cap in self.spec["capabilities"]:
            with self.subTest(capability=cap["id"]):
                self.assertEqual(rows[cap["id"]], cap["status"],
                                 f"{cap['id']} status differs between Markdown and JSON")

    def test_documents_state_that_nothing_is_implemented(self):
        phrases = ("not implemented", "nothing here is implemented",
                   "nothing in this specification is implemented",
                   "no capability is implemented", "nothing on this roadmap")
        for name, text in (("spec", self.spec_text), ("roadmap", self.roadmap_text),
                           ("readme", self.readme_text)):
            with self.subTest(document=name):
                lowered = text.lower()
                self.assertTrue(any(phrase in lowered for phrase in phrases),
                                f"{name} does not state that nothing is implemented")
        self.assertIn("harness inventory", self.readme_text.lower())

    def test_no_model_or_vendor_is_named(self):
        haystack = " ".join(p.read_text(encoding="utf-8")
                            for p in sorted(PRODUCT.glob("*")) if p.is_file())
        lowered = haystack.lower()
        hits = [token for token in BANNED_VENDOR_TOKENS if token in lowered]
        self.assertEqual(hits, [], f"product docs name a model, vendor or accelerator: {hits}")

    def test_open_items_have_status_and_are_recorded(self):
        items = {item["id"]: item for item in self.spec["open_items"]}
        for question in ("Q13", "Q16", "Q17", "Q19", "Q21", "Q22", "Q23", "Q24"):
            self.assertIn(question, items)
            self.assertIn(question, self.spec_text)
        for item in self.spec["open_items"]:
            with self.subTest(item=item["id"]):
                self.assertIn(item["status"], {"open", "answered", "blocked"})
                self.assertIsInstance(item["blocks"], list)
        self.assertEqual(items["Q23"]["status"], "open")


class HarnessBoundaryTests(unittest.TestCase):
    """The evaluator registry must never be presented as product functionality."""

    def test_registry_readme_labels_it_a_harness_inventory(self):
        text = (REPO / "eval" / "registry" / "README.md").read_text(encoding="utf-8")
        lowered = text.lower()
        self.assertIn("harness", lowered)
        self.assertIn("not", lowered)
        self.assertIn("capability-specification", text)

    def test_eval_readme_points_at_the_product_spec(self):
        text = (REPO / "eval" / "README.md").read_text(encoding="utf-8")
        self.assertIn("../docs/product/capability-specification.md", text)

    def test_no_application_or_chat_ui_code_exists(self):
        top_level = {p.name for p in REPO.iterdir() if not p.name.startswith(".")}
        self.assertEqual(top_level, {"README.md", "docs", "eval"})
        offenders = [p for p in REPO.rglob("*")
                     if p.is_file()
                     and p.suffix in {".tsx", ".jsx", ".vue", ".svelte", ".html"}
                     and ".git" not in p.parts]
        self.assertEqual(offenders, [], f"UI code found: {offenders}")

    def test_root_readme_is_untouched_by_this_specification(self):
        root = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("C-01", root)
        self.assertNotIn("capability specification", root.lower())


if __name__ == "__main__":
    unittest.main()
