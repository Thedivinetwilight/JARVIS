"""Consistency checks for the product capability specification (v1.2.0).

These test the *documents*, not the product: the product does not exist yet. What they
protect is that the normative requirements, the machine-readable index and the roadmap
cannot drift apart, that the owner's decisions stay recorded as decisions, that the local
processing boundary is never reopened, and that no capability can be labelled implemented.

Since 1.2.0 they also protect the interface scope: the home holographic interface stays
specified-only (no display technology, no verified device, no claim of working), the device
matrix covers every declared target and marks none of them verified, platform limits are
stated rather than papered over, and the two proposals (languages, server tiers) stay marked
as proposals with no language claimed and no purchase named.
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
MATRIX_MD_FILE = PRODUCT / "device-support-matrix.md"

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
        self.assertEqual(self.spec["spec_version"], "1.2.0")
        statement = self.spec["status_statement"].lower()
        self.assertIn("implemented", statement)
        self.assertIn("harness inventory", statement)

    def test_fourteen_capabilities_with_stable_ids(self):
        ids = [cap["id"] for cap in self.caps]
        self.assertEqual(ids, [f"C-{n:02d}" for n in range(1, 15)])
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
        self.assertEqual(ids, [f"O-{n}" for n in range(1, 13)])
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
        slices = set(spec["partial_slice_families"])
        existing = set(spec["existing_families"])
        declared = existing | set(spec["evaluation_backlog"]["slice_suites"])
        self.assertEqual(declared, built,
                         "a task set exists that the index calls neither existing nor a slice")
        self.assertEqual({entry["family"] for entry in
                          spec["evaluation_backlog"]["slice_suites"].values()}, slices)
        self.assertFalse(existing & slices)
        self.assertFalse(slices & set(spec["existing_families"]))
        for fid in slices:
            self.assertIn(fid, spec["planned_families"],
                          "a sliced family must still be a planned family")

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
        self.assertEqual([m["id"] for m in planned], [f"M{n}" for n in range(5, 17)])
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
        self.assertEqual(len(rows), 14, f"summary table rows: {sorted(rows)}")
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
        for question in ("Q13", "Q16", "Q17", "Q19", "Q21", "Q22", "Q23", "Q24", "Q25", "Q26"):
            self.assertIn(question, items)
            self.assertIn(question, self.spec_text)
        for item in self.spec["open_items"]:
            with self.subTest(item=item["id"]):
                self.assertIn(item["status"], {"open", "answered", "blocked"})
                self.assertIsInstance(item["blocks"], list)
        self.assertEqual(items["Q23"]["status"], "open")


class InterfaceScopeTests(unittest.TestCase):
    """The interface scope must be specified honestly and never demonstrated."""

    def setUp(self):
        self.spec = _index()
        self.caps = _capabilities(self.spec)
        self.matrix = self.spec["device_matrix"]
        self.spec_text = SPEC_MD.read_text(encoding="utf-8")
        self.matrix_text = MATRIX_MD_FILE.read_text(encoding="utf-8")

    def test_holographic_interface_is_specified_and_not_built(self):
        cap = self.caps["C-13"]
        self.assertEqual(cap["status"], "specified")
        requirement = cap["requirement"].lower()
        self.assertIn("no display technology", requirement)
        self.assertIn("specified only", requirement)
        self.assertIn("nothing has been bought", requirement)
        self.assertEqual(cap["validated_by"]["milestones"], ["M16"])
        for criterion in cap["acceptance_criteria"]:
            self.assertEqual(criterion["result"], "not measured")
        lowered = self.matrix_text.lower()
        self.assertIn("nothing here is implemented", lowered)
        self.assertIn("no display technology", lowered)

    def test_holographic_interface_is_separate_from_vr_and_both_glasses(self):
        requirement = self.caps["C-13"]["requirement"].lower()
        self.assertIn("vr", requirement)
        self.assertIn("glasses", requirement)
        distinct = " ".join(self.matrix["distinctness"]).lower()
        self.assertIn("not vr", distinct)
        for needle in ("ray-ban", "edith glass", "aetherion"):
            self.assertIn(needle, distinct)
        self.assertNotIn("holographic", " ".join(self.matrix["owner_confirmed_targets"]).lower())

    def test_device_matrix_covers_every_declared_target(self):
        expected = ["pc", "tablet", "iphone", "android-phone", "vr", "rayban-glasses",
                    "edith-glass"]
        devices = self.matrix["devices"]
        self.assertEqual([device["id"] for device in devices], expected)
        for device in devices:
            with self.subTest(device=device["id"]):
                self.assertTrue(device["interaction_modes"])
                self.assertTrue(device["integration_boundary"].strip())
                self.assertTrue(device["platform_dependency"].strip())
                self.assertTrue(device["permissions"])
                self.assertTrue(device["unverified"])
                status = device["status"].lower()
                self.assertNotIn("verified", status)
                self.assertNotIn("supported", status)
        self.assertIn("0 of 7", self.matrix["status"])

    def test_edith_glass_is_a_separate_future_project(self):
        edith = next(d for d in self.matrix["devices"] if d["id"] == "edith-glass")
        self.assertIn("separate future project", edith["status"])
        self.assertEqual(self.matrix["separate_future_project"], ["edith-glass"])
        self.assertIn("rayban-glasses", self.matrix["owner_confirmed_targets"])
        self.assertNotIn("edith-glass", self.matrix["owner_confirmed_targets"])
        self.assertTrue(any("aetherion" in item.lower() for item in self.matrix["out_of_scope"]))

    def test_device_matrix_markdown_agrees_with_the_index(self):
        lowered = self.matrix_text.lower()
        for device in self.matrix["devices"]:
            with self.subTest(device=device["id"]):
                needle = device["name"].split(" (")[0].lower()
                self.assertIn(needle, lowered)
        self.assertIn("never", lowered)  # the naming rule for retail glasses
        self.assertIn("cellular", lowered)

    def test_telephony_platform_limits_are_normative(self):
        cap = self.caps["C-05"]
        platforms = {limit["platform"]: limit for limit in cap["platform_limits"]}
        iphone = platforms["iPhone"]
        forbidden = iphone["what_is_forbidden"].lower()
        self.assertIn("cellular", forbidden)
        self.assertIn("no access", forbidden)
        self.assertIn("android", " ".join(platforms).lower())
        self.assertIn("voip", cap["telephony_rule"].lower())
        criteria = {c["id"] for c in cap["acceptance_criteria"]}
        self.assertIn("C-05-AC7", criteria)
        self.assertIn("cellular", self.spec_text.lower())

    def test_device_access_stays_scoped_and_revocable(self):
        cap = self.caps["C-14"]
        permissions = cap["data_and_permissions"]["permissions"]
        self.assertTrue(set(permissions) <= set(self.spec["permission_verbs"]))
        self.assertIn("revocation", json.dumps(cap["data_and_permissions"]).lower())
        for device in self.matrix["devices"]:
            for grant in device["permissions"]:
                self.assertNotIn("all my things", grant.lower())
                self.assertNotIn("blanket", grant.lower())
                self.assertNotIn("any device", grant.lower())

    def test_interface_milestone_is_planned_and_gated(self):
        milestone = next(m for m in self.spec["milestones"] if m["id"] == "M16")
        self.assertEqual(milestone["status"], "planned")
        self.assertIn("M5", milestone["depends_on"])
        self.assertTrue(milestone["exit_criteria"])
        self.assertTrue(milestone.get("kill_criteria"))
        self.assertIn("M16", self.spec_text)
        gates = {gate["id"]: gate for gate in self.spec["gates"]}
        self.assertIn("holographic", " ".join(gates["G-UX"]["requires"]).lower())
        self.assertIn("cellular", " ".join(gates["G-CLAIM"]["requires"]).lower())


class ProposalTests(unittest.TestCase):
    """PR-1 and PR-2 are proposals. They may not read as decisions, support or purchases."""

    def setUp(self):
        self.spec = _index()
        self.proposals = {p["id"]: p for p in self.spec["proposals"]}
        self.spec_text = SPEC_MD.read_text(encoding="utf-8")
        self.readme_text = README_MD.read_text(encoding="utf-8")

    def test_proposals_are_labelled_and_not_requirements(self):
        self.assertEqual(sorted(self.proposals), ["PR-1", "PR-2"])
        for proposal in self.proposals.values():
            with self.subTest(proposal=proposal["id"]):
                self.assertIn("proposal", proposal["status"].lower())
                self.assertFalse(proposal["is_a_requirement"])
                self.assertTrue(proposal["what_it_does_not_claim"])
                self.assertIn(proposal["id"], self.spec_text)
                self.assertIn(proposal["id"], self.readme_text)

    def test_language_proposal_claims_no_fluency(self):
        languages = self.proposals["PR-1"]["languages"]
        self.assertEqual(len(languages), 15)
        self.assertEqual({language["status"] for language in languages}, {"not measured"})
        claims = " ".join(self.proposals["PR-1"]["what_it_does_not_claim"]).lower()
        self.assertIn("no language is claimed", claims)
        self.assertIn("q19", self.proposals["PR-1"]["status"].lower())

    def test_language_open_question_stays_open(self):
        items = {item["id"]: item for item in self.spec["open_items"]}
        self.assertEqual(items["Q19"]["status"], "open")
        self.assertIn("proposal", items["Q19"]["proposal"].lower())
        self.assertIn("owner_direction", items["Q19"])

    def test_server_proposal_selects_nothing_and_keeps_go1_blocked(self):
        proposal = self.proposals["PR-2"]
        self.assertIn("q20", proposal["status"].lower())
        self.assertEqual(set(proposal["tiers"]), {"lower", "preferred"})
        lower = proposal["tiers"]["lower"]
        preferred = proposal["tiers"]["preferred"]
        self.assertIn("32", lower["gpu_memory_gb"])
        self.assertIn("96", preferred["gpu_memory_gb"])
        self.assertIn("128", lower["system_memory_gb"])
        self.assertIn("256", preferred["system_memory_gb"])
        self.assertTrue(proposal["sources"])
        claims = " ".join(proposal["what_it_does_not_claim"]).lower()
        self.assertIn("no budget", claims)
        self.assertIn("g-o1", claims)
        gates = {gate["id"]: gate for gate in self.spec["gates"]}
        self.assertEqual(gates["G-O1"]["status"], "blocked")
        items = {item["id"]: item for item in self.spec["open_items"]}
        self.assertEqual(items["Q20"]["status"], "open")

    def test_scope_decisions_are_recorded_without_ranking_tasks(self):
        decisions = {decision["id"]: decision for decision in self.spec["owner_decisions"]}
        for decision in ("O-8", "O-9", "O-10", "O-11", "O-12"):
            self.assertIn(decision, decisions)
            self.assertIn(decision, self.spec_text)
        self.assertIn("no priority ranking", decisions["O-8"]["decision"].lower())
        self.assertIn("edith", decisions["O-11"]["decision"].lower())
        self.assertIn("aetherion", decisions["O-12"]["decision"].lower())
        items = {item["id"]: item for item in self.spec["open_items"]}
        for question in ("Q25", "Q26"):
            self.assertEqual(items[question]["status"], "open")


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
        # At spec 1.2.0 this asserted that no application area existed at all. The owner has
        # since authorised exactly one: the local server in ``app/`` (docs/adr/0001), which is
        # a service boundary, not a client and not a chat UI. The permitted top level is named
        # explicitly so any *other* application area still fails this test, and the UI-framework
        # scan below still covers the whole repository including ``app/``.
        top_level = {p.name for p in REPO.iterdir() if not p.name.startswith(".")}
        self.assertEqual(top_level, {"README.md", "docs", "eval", "app"})
        offenders = [p for p in REPO.rglob("*")
                     if p.is_file()
                     and p.suffix in {".tsx", ".jsx", ".vue", ".svelte", ".html"}
                     and ".git" not in p.parts]
        self.assertEqual(offenders, [], f"UI code found: {offenders}")
        # The server serves JSON only: no templates, no static assets, no chat surface.
        served = [p for p in (REPO / "app").rglob("*")
                  if p.is_file() and p.suffix in {".html", ".css", ".js", ".htm"}]
        self.assertEqual(served, [], f"served assets found: {served}")

    def test_root_readme_is_untouched_by_this_specification(self):
        root = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("C-01", root)
        self.assertNotIn("capability specification", root.lower())


if __name__ == "__main__":
    unittest.main()


BACKLOG = PRODUCT / "family-backlog.json"
AC_COVERAGE = PRODUCT / "ac-coverage.json"
CROSSWALK_MD = PRODUCT / "family-crosswalk.md"
MATRIX_MD = PRODUCT / "coverage-matrix.md"

COVERAGE_STATUSES = {"planned_suite", "named_audit", "human_review", "blocked_owner_input",
                     "slice_built"}
CROSSWALK_STATUSES = {"retained", "amended", "superseded"}
FIXTURE_KINDS = {"synthetic", "manual"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class EvaluationBacklogTests(unittest.TestCase):
    """The M5 backlog must stay a plan: complete, local-only, and never a capability claim."""

    def setUp(self):
        self.spec = _index()
        self.caps = _capabilities(self.spec)
        self.backlog = _load(BACKLOG)
        self.coverage = _load(AC_COVERAGE)
        self.families = {f["id"]: f for f in self.backlog["families"]}
        self.entries = self.coverage["entries"]

    def _ac_ids(self) -> set:
        return {c["id"] for cap in self.caps.values() for c in cap["acceptance_criteria"]}

    def _capability_text(self, cap_id: str) -> str:
        cap = self.caps[cap_id]
        return " ".join(c["statement"] + " " + c["metric"] + " " + c["measurement"]
                        for c in cap["acceptance_criteria"])

    def test_every_acceptance_criterion_is_mapped_exactly_once(self):
        mapped = [e["ac"] for e in self.entries]
        self.assertEqual(len(mapped), len(set(mapped)), "an acceptance criterion is mapped twice")
        self.assertEqual(set(mapped), self._ac_ids(),
                         "coverage does not match the acceptance criteria in capabilities.json")
        self.assertEqual(self.coverage["unmapped"], [])

    def test_coverage_uses_known_suites_and_statuses(self):
        known = set(self.spec["existing_families"]) | set(self.spec["planned_families"])
        known |= {g["id"] for g in self.spec["gates"]}
        statuses = set(self.coverage["status_vocabulary"])
        self.assertEqual(statuses, COVERAGE_STATUSES)
        for entry in self.entries:
            with self.subTest(ac=entry["ac"]):
                self.assertIn(entry["suite"], known, "unknown suite or gate")
                self.assertIn(entry["status"], COVERAGE_STATUSES)
                if entry["status"] == "blocked_owner_input":
                    self.assertTrue(entry["blocked_by"], "blocked criterion names no blocker")
                    for blocker in entry["blocked_by"]:
                        self.assertIn(blocker, self.coverage["blocker_vocabulary"])
                else:
                    self.assertEqual(entry["blocked_by"], [],
                                     "only blocked_owner_input may name a blocker")
                if entry["status"] == "named_audit":
                    self.assertEqual(entry["suite_kind"], "audit")
                if entry["status"] in {"planned_suite", "slice_built"}:
                    self.assertEqual(entry["suite_kind"], "family")
                if entry["status"] == "slice_built":
                    self.assertIn("slice", entry,
                                  "a built slice must say what it covers and what it does not")
                    self.assertTrue(entry["slice"]["not_covered"].strip())

    def test_coverage_summary_matches_the_entries(self):
        summary = self.coverage["summary"]
        for status in COVERAGE_STATUSES:
            counted = sum(1 for e in self.entries if e["status"] == status)
            self.assertEqual(summary["by_status"][status], counted, f"summary drift for {status}")
        self.assertEqual(summary["criteria_total"], len(self.entries))
        self.assertEqual(summary["with_objective_protocol"],
                         len(self.entries) - summary["by_status"]["human_review"])
        self.assertEqual(summary["human_review_only"],
                         [e["ac"] for e in self.entries if e["status"] == "human_review"])
        blocked = {b for e in self.entries for b in e["blocked_by"]}
        self.assertEqual(set(summary["blocked_by_owner_input"]), blocked)

    def test_backlog_covers_every_planned_family_and_claims_nothing_finished(self):
        self.assertEqual(set(self.families), set(self.spec["planned_families"]))
        built = set(load_tasksets(REPO / "eval" / "tasks"))
        sliced = {fid for fid, family in self.families.items()
                  if family.get("implemented_slice") or family.get("implemented_slice_2")
                  or family.get("implemented_slice_3")}
        self.assertEqual(sliced, set(self.spec.get("partial_slice_families", [])))
        # A block on disk must be either an existing family or a declared slice suite;
        # a family with two slices declares two suite blocks.
        declared = set(self.spec["existing_families"])
        declared |= set(self.spec["evaluation_backlog"]["slice_suites"])
        self.assertEqual(set(built), declared)
        self.assertEqual(len(self.spec["evaluation_backlog"]["slice_suites"]),
                         self.spec["evaluation_backlog"]["families"]["slices_total"])
        self.assertEqual({entry["family"] for entry in
                          self.spec["evaluation_backlog"]["slice_suites"].values()},
                         set(self.spec["partial_slice_families"]))
        for fid, family in self.families.items():
            with self.subTest(family=fid):
                self.assertEqual(family["status"], "planned",
                                 "a backlog family may only be 'planned'")
                if fid not in sliced:
                    self.assertNotIn(fid, built, "a planned family exists on disk")
                self.assertGreaterEqual(len(family["task_types"]), 2)
                for task in family["task_types"]:
                    self.assertGreater(task["count"], 0)
                    self.assertTrue(task["measures"].strip())
                self.assertTrue(family["fixtures"])
                self.assertTrue(family["harness_delta"])
                self.assertTrue(family["scoring"]["per_task"])
                self.assertTrue(family["scoring"]["gates"])
                self.assertGreaterEqual(len(family["planted_regressions"]), 2)
                ids = [r["id"] for r in family["planted_regressions"]]
                self.assertEqual(len(ids), len(set(ids)))
                for regression in family["planted_regressions"]:
                    self.assertIn(regression["expected_gate"], family["scoring"]["gates"],
                                  "regression expects a gate the family does not plan")

    def test_backlog_fixtures_are_synthetic_or_manual_and_thresholds_exist(self):
        for fid, family in self.families.items():
            with self.subTest(family=fid):
                for fixture in family["fixtures"]:
                    self.assertIn(fixture["kind"], FIXTURE_KINDS,
                                  "only synthetic or manual fixtures are allowed")
                self.assertTrue(family["thresholds"])
                for threshold in family["thresholds"]:
                    self.assertIn(threshold["criterion"], self._ac_ids(),
                                  "threshold cites an unknown criterion")
                    mapped = {e["ac"]: e for e in self.entries}
                    known = (set(self.spec["existing_families"]) | set(self.spec["planned_families"])
                             | {g["id"] for g in self.spec["gates"]})
                    self.assertIn(mapped[threshold["criterion"]]["suite"], known)

    def test_backlog_stays_local_only(self):
        #: every family either never leaves the server or names one declared external service
        anchors = ("none", "telephone network", "public web", "device vendor cloud")
        for fid, family in self.families.items():
            with self.subTest(family=fid):
                self.assertTrue(family["privacy"]["local_only"])
                egress = family["privacy"]["egress"]
                self.assertTrue(egress.startswith(anchors),
                                f"{fid} egress is not 'none' or a declared external service")
                self.assertTrue(family["privacy"]["constraints"])

    def test_backlog_thresholds_do_not_contradict_the_spec(self):
        pattern = re.compile(r"\d+(?:\.\d+)?\s*(?:%|ms\b|s\b)")
        for fid, family in self.families.items():
            for threshold in family["thresholds"]:
                cap_id = threshold["criterion"][:4]
                spec_text = self._capability_text(cap_id)
                spec_values = {re.sub(r"\s+", "", v) for v in pattern.findall(spec_text)}
                for value in pattern.findall(threshold["metric"]):
                    with self.subTest(family=fid, criterion=threshold["criterion"], value=value):
                        self.assertIn(re.sub(r"\s+", "", value), spec_values,
                                      "threshold value does not appear in the specification text")

    def test_gate_bookkeeping_matches_the_harness(self):
        """A gate may only be described as live if it exists, and only as planned if it does not."""
        gates_source = (REPO / "eval" / "harness" / "gates.py").read_text(encoding="utf-8")
        live = {gate for family in self.families.values()
                for key in ("implemented_slice", "implemented_slice_2",
                            "implemented_slice_3")
                for gate in family.get(key, {}).get("gates", [])}
        planned = {gate for family in self.families.values()
                   for gate in family["scoring"]["gates"]}
        self.assertTrue(live, "no slice gate is declared")
        self.assertTrue(live <= planned,
                        f"live gates not in the family plan: {sorted(live - planned)}")
        for gate in sorted(live):
            with self.subTest(gate=gate, state="live"):
                self.assertIn(gate, gates_source,
                              "a slice claims a gate the harness does not implement")
        for gate in sorted(planned - live):
            with self.subTest(gate=gate, state="planned"):
                self.assertNotIn(gate, gates_source,
                                 "the harness implements a gate the backlog still calls planned")

    def test_index_summary_matches_the_backlog_files(self):
        summary = self.spec["evaluation_backlog"]
        self.assertEqual(summary["families"]["planned"], len(self.families))
        self.assertEqual(summary["families"]["implemented"], 0)
        self.assertEqual(summary["families"]["ids"], [f["id"] for f in self.backlog["families"]])
        self.assertEqual(summary["families"]["planned_tasks"],
                         sum(t["count"] for f in self.families.values() for t in f["task_types"]))
        self.assertEqual(summary["families"]["planted_regressions"],
                         sum(len(f["planted_regressions"]) for f in self.families.values()))
        self.assertEqual(summary["acceptance_criteria"]["total"], len(self.entries))
        self.assertEqual(summary["acceptance_criteria"]["validated"], 0)

    def test_slices_are_backed_by_real_files_and_gates(self):
        summary = self.spec["evaluation_backlog"]
        slices = summary["slices"]
        self.assertEqual(len(slices), summary["families"]["slices_total"])
        self.assertEqual({item["family"] for item in slices},
                         set(self.spec["partial_slice_families"]))
        self.assertEqual({item.get("suite_block", item["family"]) for item in slices},
                         set(summary["slice_suites"]))
        built = load_tasksets(REPO / "eval" / "tasks")
        for item in slices:
            block = item.get("suite_block", item["family"])
            with self.subTest(slice=block):
                self.assertIn(block, built, "slice has no task set on disk")
                self.assertEqual(len(built[block]), item["tasks"])
                self.assertFalse(item["family_complete"],
                                 "a slice may not claim a finished family")
                self.assertTrue((REPO / item["tests"].split("::")[0]).is_file())
                for criterion in item["covers"] + item["partial_coverage"]:
                    entry = next(e for e in self.entries if e["ac"] == criterion)
                    self.assertIn(entry["status"], {"slice_built", "planned_suite", "named_audit",
                                                    "human_review", "blocked_owner_input"})
                for criterion in item["covers"]:
                    entry = next(e for e in self.entries if e["ac"] == criterion)
                    self.assertEqual(entry["status"], "slice_built",
                                     f"{criterion} is covered by a slice but not marked built")
        # the backlog family entries must agree with the index
        for fid in self.spec["partial_slice_families"]:
            self.assertEqual(self.families[fid]["implemented_slice"]["tasks"],
                             next(s for s in slices if s["family"] == fid)["tasks"])

    def test_slice_documentation_is_linked(self):
        matrix = MATRIX_MD.read_text(encoding="utf-8")
        backlog = (PRODUCT / "family-backlog.md").read_text(encoding="utf-8")
        self.assertIn("## 9. Implemented evaluator slices", matrix)
        for fid in self.spec["partial_slice_families"]:
            slice_id = self.families[fid]["implemented_slice"]["slice_id"]
            self.assertIn(slice_id, matrix)
            self.assertIn(slice_id, backlog)
        for needle in ("eval/tasks/b4", "eval/tasks/b4d", "eval/tasks/b16"):
            self.assertIn(needle, matrix)
            self.assertIn(needle, backlog)

    def test_slice_capabilities_keep_their_product_status(self):
        for capability in self.caps.values():
            with self.subTest(capability=capability["id"]):
                self.assertIn(capability["status"], ALLOWED_STATUSES)
                self.assertNotEqual(capability["status"], "implemented")

    def test_crosswalk_resolves_the_legacy_numbering(self):
        text = CROSSWALK_MD.read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if line.startswith("| M5-B-")]
        self.assertGreaterEqual(len(rows), 8, "crosswalk register is incomplete")
        cells = [[c.strip() for c in row.strip("|").split("|")] for row in rows]
        for row in cells:
            self.assertIn(row[3], CROSSWALK_STATUSES, f"{row[0]} has an unknown status")
            self.assertTrue(row[4], f"{row[0]} names no replacement")
        statuses = {row[0]: row[3] for row in cells}
        self.assertIn("retained", statuses.values())
        self.assertIn("superseded", statuses.values())
        for needle in ("08-evaluation-and-improvement.md",
                       "stage-0-m2-frozen-baseline.md", "stage-0-m3-planted-regression.md"):
            self.assertIn(needle, text)
        self.assertIn("by name only", text.lower())
        self.assertIn("b4", text.lower())
        self.assertIn("b7", text.lower())

    def test_coverage_matrix_section_matches_the_json(self):
        text = MATRIX_MD.read_text(encoding="utf-8")
        self.assertIn("## 8. Criterion-level coverage", text)
        row = re.compile(r"^\| (C-\d{2}-AC\d) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$", re.M)
        table = {m.group(1): (m.group(2).strip(), m.group(3).strip(), m.group(4).strip())
                 for m in row.finditer(text)}
        self.assertEqual(len(table), len(self.entries))
        for entry in self.entries:
            self.assertEqual(table[entry["ac"]],
                             (entry["suite"], entry["suite_kind"], entry["status"]),
                             f"{entry['ac']} drifts between the matrix and ac-coverage.json")
        for name in ("family-crosswalk.md", "family-backlog.md", "ac-coverage.json"):
            self.assertIn(name, text, "coverage matrix does not link its M5 artefacts")

    def test_backlog_markdown_matches_the_json_summary(self):
        text = (PRODUCT / "family-backlog.md").read_text(encoding="utf-8")
        row = re.compile(r"^\| (B\d+) \| ([^|]+) \| (\d+) \| (\d+) \| ", re.M)
        table = [(m.group(1), int(m.group(3)), int(m.group(4))) for m in row.finditer(text)]
        expected = [(f["id"],
                     sum(t["count"] for t in f["task_types"]),
                     len(f["planted_regressions"])) for f in self.backlog["families"]]
        self.assertEqual(table, expected,
                         "family-backlog.md has drifted from family-backlog.json")
        for family in self.backlog["families"]:
            self.assertIn(f"## {family['id']} —", text)

    def test_product_readme_lists_the_m5_artefacts(self):
        text = README_MD.read_text(encoding="utf-8")
        for name in ("family-crosswalk.md", "family-backlog.md", "ac-coverage.json"):
            self.assertIn(name, text)
        self.assertNotIn("\\n|", text, "a literal newline escape survives in the README table")
