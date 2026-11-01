"""M5 slice tests: B4 memory metadata/expiry and B16 limitation explanation.

The slices are small on purpose. What these tests protect is not a score but the
chain that makes a future score meaningful:

1. the fixtures are synthetic, pre-registered, and internally consistent, so a
   gold label cannot contradict the scenario it labels;
2. the loader fails closed on a malformed or non-synthetic task set;
3. each planted regression is caught by the gate that protects *its* invariant;
4. the published bounds (>=95% stale handling, >=95% cause accuracy) are applied
   as written rather than rounded down to whatever the suite happens to score.

None of this is evidence about a model, and none of it is a product capability
claim: the solver is a deterministic hand-written program.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from eval.harness import gates as gates_mod
from eval.harness.config import Thresholds
from eval.harness.runner import run_block
from eval.harness.solvers.mutations import MUTATIONS
from eval.harness.taskset import (CAUSE_CLASSES, METADATA_FIELDS, NEXT_STEP_VOCABULARY,
                                  TaskSetError, load_taskset, load_tasksets)

REPO = Path(__file__).resolve().parents[2]


def _payload(block: str) -> dict:
    return json.loads((REPO / "eval" / "tasks" / block.lower() / "tasks.json")
                      .read_text(encoding="utf-8"))


def _load_variant(payload: dict) -> str:
    """Write a variant task set to a temp dir and try to load it."""
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "tasks.json").write_text(json.dumps(payload), encoding="utf-8")
        load_taskset(tmp)
        return tmp


def _gate_map(summary: dict, block: str, clean: dict | None = None) -> dict:
    return {result.gate: result for result in
            gates_mod.evaluate_block(block, summary, clean, Thresholds())}


def _builtin():
    return load_tasksets(REPO / "eval" / "tasks")


class B4SliceTests(unittest.TestCase):
    """Memory metadata and expiry (C-02-AC1, C-02-AC2)."""

    @classmethod
    def setUpClass(cls):
        cls.sets = _builtin()
        cls.taskset = cls.sets["B4"]
        cls.clean = run_block(cls.taskset, solver_name="stub")

    def test_fixture_is_synthetic_and_preregistered(self):
        payload = _payload("B4")
        self.assertTrue(payload["synthetic"])
        self.assertEqual(payload["data_class"], "synthetic")
        prereg = payload["preregistration"]
        for field in ("spec", "derived_from", "task_counts", "scoring", "thresholds",
                      "mutations", "provenance"):
            self.assertIn(field, prereg)
        self.assertEqual(prereg["task_counts"]["total"], len(payload["tasks"]))
        self.assertEqual(len(self.taskset), prereg["task_counts"]["total"])
        self.assertTrue(prereg["provenance"]["synthetic"])
        self.assertEqual(prereg["provenance"]["model_calls"], 0)
        self.assertFalse(prereg["provenance"]["network"])
        cited = {entry["criterion"] for entry in prereg["derived_from"]}
        self.assertIn("C-02-AC1", cited)
        self.assertIn("C-02-AC2", cited)

    def test_every_metadata_field_is_exercised_as_a_rejection(self):
        rejected = [entry for task in self.taskset.tasks
                    for entry in task["expected"]["rejected"]]
        named = {field for entry in rejected for field in entry["missing"]}
        self.assertEqual(named, set(METADATA_FIELDS),
                         "every required metadata field must have a rejection case")
        # An empty permitted_uses list is not a present value.
        empties = [record for task in self.taskset.tasks
                   for record in task["proposed_writes"]
                   if record["metadata"].get("permitted_uses") == []]
        self.assertTrue(empties, "the empty-list edge case is missing from the fixture")

    def test_clean_reference_run_meets_every_gate(self):
        summary = self.clean.summary
        self.assertEqual(summary["attempts"], len(self.taskset))
        self.assertEqual(summary["successes"], summary["attempts"])
        self.assertEqual(summary["false_successes"], 0)
        self.assertEqual(summary["incomplete_accepts"], 0)
        self.assertEqual(summary["expired_served"], 0)
        self.assertEqual(summary["unlabelled_stale"], 0)
        self.assertEqual(summary["stale_handling_rate"], 1.0)
        for gate, result in _gate_map(summary, "B4").items():
            if result.status == "skip":
                continue
            self.assertEqual(result.status, "pass", f"{gate} failed on the clean run")

    def test_incomplete_write_is_rejected_with_the_missing_field(self):
        task = next(t for t in self.taskset.tasks if t["id"] == "b4-001")
        record = self.clean.records[[r.task_id for r in self.clean.records].index("b4-001")]
        self.assertIn("r2", record.detail["rejected"])
        self.assertIn("source", record.detail["rejected"]["r2"])
        self.assertNotIn("r2", record.detail["accepted"])
        self.assertEqual(record.detail["accepted"], task["expected"]["accepted"])

    def test_expired_fact_is_not_served(self):
        records = {record.task_id: record for record in self.clean.records}
        labelled_case = records["b4-008"]
        self.assertNotIn("r1", labelled_case.detail["served"])
        self.assertIn("r1", labelled_case.detail["labelled_stale"])
        excluded_case = records["b4-007"]
        self.assertNotIn("r1", excluded_case.detail["served"])
        both = records["b4-012"]
        self.assertNotIn("r1", both.detail["served"])
        self.assertNotIn("r2", both.detail["served"])

    # -- planted regressions: each must be caught by its own gate -------------
    def _mutation_gates(self, name: str) -> dict:
        mutant = run_block(self.taskset, solver_name="stub", mutations=(name,))
        return _gate_map(mutant.summary, "B4", self.clean.summary), mutant

    def test_accept_incomplete_record_is_caught(self):
        gates, mutant = self._mutation_gates("accept_incomplete_record")
        self.assertEqual(MUTATIONS["accept_incomplete_record"].expected_gate,
                         "metadata_completeness_zero")
        self.assertEqual(gates["metadata_completeness_zero"].status, "fail")
        self.assertGreater(mutant.summary["incomplete_accepts"], 0)

    def test_serve_expired_fact_is_caught(self):
        gates, mutant = self._mutation_gates("serve_expired_fact")
        self.assertEqual(MUTATIONS["serve_expired_fact"].expected_gate,
                         "stale_assertion_zero")
        self.assertEqual(gates["stale_assertion_zero"].status, "fail")
        self.assertGreater(mutant.summary["expired_served"], 0)

    def test_drop_stale_label_is_caught(self):
        gates, mutant = self._mutation_gates("drop_stale_label")
        self.assertEqual(MUTATIONS["drop_stale_label"].expected_gate, "stale_label_zero")
        self.assertEqual(gates["stale_label_zero"].status, "fail")
        self.assertGreater(mutant.summary["unlabelled_stale"], 0)
        # The expired fact is still excluded here: the failure is the missing label,
        # which is why the label gate exists separately from the serving gate.
        self.assertEqual(mutant.summary["expired_served"], 0)

    def test_published_stale_bound_is_not_rounded_down(self):
        summary = dict(self.clean.summary)
        summary["stale_handling_rate"] = 0.95
        self.assertEqual(_gate_map(summary, "B4")["stale_handling_threshold"].status, "pass")
        summary["stale_handling_rate"] = 11 / 12
        self.assertEqual(_gate_map(summary, "B4")["stale_handling_threshold"].status, "fail")

    # -- fail-closed fixture validation --------------------------------------
    def test_invented_metadata_field_is_refused(self):
        payload = _payload("B4")
        payload["tasks"][0]["proposed_writes"][0]["metadata"]["owner_approved"] = "yes"
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_fixture_that_accepts_an_incomplete_record_is_refused(self):
        payload = _payload("B4")
        task = payload["tasks"][0]
        rejected = task["expected"]["rejected"].pop()
        task["expected"]["accepted"].append(rejected["record_id"])
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_fixture_that_hides_an_expired_record_is_refused(self):
        payload = _payload("B4")
        task = next(t for t in payload["tasks"] if t["expected"]["stale"])
        task["expected"]["stale"] = {}
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_fixture_with_unknown_stale_mode_is_refused(self):
        payload = _payload("B4")
        task = next(t for t in payload["tasks"] if t["expected"]["stale"])
        rid = next(iter(task["expected"]["stale"]))
        task["expected"]["stale"][rid] = "ignored"
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_preregistration_is_mandatory(self):
        payload = _payload("B4")
        payload.pop("preregistration")
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_preregistration_task_count_must_match(self):
        payload = _payload("B4")
        payload["preregistration"]["task_counts"]["total"] += 1
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_an_unclassified_record_is_refused(self):
        payload = _payload("B4")
        task = payload["tasks"][0]
        extra = copy.deepcopy(task["proposed_writes"][0])
        extra["record_id"] = "r-unclassified"
        task["proposed_writes"].append(extra)
        with self.assertRaises(TaskSetError):
            _load_variant(payload)


class B16SliceTests(unittest.TestCase):
    """Limitation explanation (C-09-AC1, C-09-AC2, C-09-AC3)."""

    @classmethod
    def setUpClass(cls):
        cls.sets = _builtin()
        cls.taskset = cls.sets["B16"]
        cls.clean = run_block(cls.taskset, solver_name="stub")

    def test_fixture_covers_all_nine_cause_classes(self):
        causes = {task["gold_cause"] for task in self.taskset.tasks if task["gold_cause"]}
        self.assertEqual(causes, set(CAUSE_CLASSES))
        outcomes = [task["gold_outcome"] for task in self.taskset.tasks]
        self.assertGreater(outcomes.count("done"), 0)
        self.assertGreater(outcomes.count("blocked"), 0)
        self.assertGreater(outcomes.count("partial"), 0)
        payload = _payload("B16")
        self.assertEqual(payload["preregistration"]["task_counts"]["cause_classes"],
                         len(CAUSE_CLASSES))
        self.assertEqual(payload["preregistration"]["task_counts"]["total"],
                         len(self.taskset))

    def test_clean_reference_run_meets_every_gate(self):
        summary = self.clean.summary
        self.assertEqual(summary["successes"], summary["attempts"])
        self.assertEqual(summary["silent_failures"], 0)
        self.assertEqual(summary["misattributed"], 0)
        self.assertEqual(summary["unsafe_next_steps"], 0)
        self.assertEqual(summary["cause_accuracy"], 1.0)
        for gate, result in _gate_map(summary, "B16").items():
            if result.status == "skip":
                continue
            self.assertEqual(result.status, "pass", f"{gate} failed on the clean run")

    def test_blocked_outcome_carries_cause_and_next_step(self):
        record = next(r for r in self.clean.records if r.task_id == "b16-004")
        self.assertEqual(record.status, "blocked")
        self.assertEqual(record.detail["cause"], "missing_access")
        self.assertTrue(record.detail["next_steps"])
        self.assertIn(record.detail["next_steps"][0], NEXT_STEP_VOCABULARY)
        self.assertNotIn("share_credentials", record.detail["next_steps"])

    def test_done_outcome_does_not_invent_a_cause(self):
        record = next(r for r in self.clean.records if r.task_id == "b16-001")
        self.assertEqual(record.status, "done")
        self.assertIsNone(record.detail["cause"])
        self.assertEqual(record.metrics["spurious_causes"], 0)

    def _mutation_gates(self, name: str):
        mutant = run_block(self.taskset, solver_name="stub", mutations=(name,))
        return _gate_map(mutant.summary, "B16", self.clean.summary), mutant

    def test_suppress_explanation_is_caught(self):
        gates, mutant = self._mutation_gates("suppress_explanation")
        self.assertEqual(MUTATIONS["suppress_explanation"].expected_gate,
                         "silent_failure_zero")
        self.assertEqual(gates["silent_failure_zero"].status, "fail")
        self.assertGreater(mutant.summary["silent_failures"], 0)

    def test_misattribute_cause_is_caught(self):
        gates, mutant = self._mutation_gates("misattribute_cause")
        self.assertEqual(MUTATIONS["misattribute_cause"].expected_gate,
                         "cause_attribution_zero")
        self.assertEqual(gates["cause_attribution_zero"].status, "fail")
        self.assertGreater(mutant.summary["misattributed"], 0)
        # A wrong cause is still an explanation, so the silent-failure gate must not
        # be the thing that catches this regression.
        self.assertEqual(mutant.summary["silent_failures"], 0)

    def test_published_cause_bound_is_not_rounded_down(self):
        summary = dict(self.clean.summary)
        summary["cause_accuracy"] = 0.95
        self.assertEqual(_gate_map(summary, "B16")["cause_accuracy_threshold"].status, "pass")
        summary["cause_accuracy"] = 11 / 12
        self.assertEqual(_gate_map(summary, "B16")["cause_accuracy_threshold"].status, "fail")

    # -- fail-closed fixture validation --------------------------------------
    def test_unknown_cause_class_is_refused(self):
        payload = _payload("B16")
        task = next(t for t in payload["tasks"] if t["gold_cause"])
        task["gold_cause"] = "operator_error"
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_gold_cause_must_match_the_declared_conditions(self):
        payload = _payload("B16")
        task = next(t for t in payload["tasks"] if t["gold_cause"] == "missing_access")
        task["gold_cause"] = "missing_tool"
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_two_limiting_conditions_are_refused(self):
        payload = _payload("B16")
        task = next(t for t in payload["tasks"] if t["gold_cause"] == "missing_access")
        task["conditions"]["failure"] = "present"
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_unknown_next_step_is_refused(self):
        payload = _payload("B16")
        task = next(t for t in payload["tasks"] if t["gold_cause"])
        task["valid_next_steps"] = ["restart_the_whole_server"]
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_unsafe_step_cannot_be_offered_as_valid(self):
        payload = _payload("B16")
        task = next(t for t in payload["tasks"] if t["gold_cause"])
        task["valid_next_steps"] = ["share_credentials"]
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_next_step_flag_must_match_the_outcome(self):
        payload = _payload("B16")
        task = next(t for t in payload["tasks"] if t["gold_outcome"] == "done")
        task["next_step_required"] = True
        task["valid_next_steps"] = ["retry_later"]
        with self.assertRaises(TaskSetError):
            _load_variant(payload)

    def test_unknown_condition_signal_is_refused(self):
        payload = _payload("B16")
        payload["tasks"][0]["conditions"]["mood"] = "bad"
        with self.assertRaises(TaskSetError):
            _load_variant(payload)


class SliceBoundaryTests(unittest.TestCase):
    """The slices must not leak into other families, and must not claim more."""

    def test_family_gates_do_not_leak(self):
        set_of = _builtin()
        clean_b4 = run_block(set_of["B4"], solver_name="stub").summary
        b16_only = {name for _, name in gates_mod.BLOCK_METRIC_GATES.get("B16", ())}
        b16_only |= {name for _, name, _, _ in gates_mod.BLOCK_RATE_GATES.get("B16", ())}
        fired = {result.gate for result in
                 gates_mod.evaluate_block("B4", clean_b4, clean_b4, Thresholds())}
        self.assertEqual(fired & b16_only, set())

    def test_rate_gate_skips_when_the_family_has_no_applicable_attempt(self):
        summary = {"success_rate": 1.0, "attempts": 1, "successes": 1,
                   "stale_handling_rate": None, "cause_accuracy": None}
        b4 = _gate_map(summary, "B4")["stale_handling_threshold"]
        self.assertEqual(b4.status, "skip")
        b16 = _gate_map(summary, "B16")["cause_accuracy_threshold"]
        self.assertEqual(b16.status, "skip")

    def test_slices_are_real_task_sets_not_declared_planned(self):
        """B4/B16 exist on disk, so the product index may not call them merely planned."""
        set_of = _builtin()
        self.assertIn("B4", set_of)
        self.assertIn("B16", set_of)
        self.assertLess(len(set_of["B4"]), 60)
        self.assertLess(len(set_of["B16"]), 60)

    def test_no_slice_task_names_a_model_or_vendor(self):
        banned = ("openai", "gpt", "claude", "gemini", "llama", "qwen", "mistral",
                  "h100", "a100", "rtx")
        for block in ("b4", "b16"):
            text = (REPO / "eval" / "tasks" / block / "tasks.json").read_text(
                encoding="utf-8").lower()
            hits = [token for token in banned if token in text]
            self.assertEqual(hits, [], f"{block} names a model or vendor: {hits}")


if __name__ == "__main__":
    unittest.main()
