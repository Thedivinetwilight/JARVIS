"""M5 slice 2: deletion and derived-surface residue (C-02-AC3).

C-02-AC3 asks for three things at once, and the slice is built so that each one
can fail on its own:

* deletion completes within one cycle -- measured on the store *after* the three
  deletion drills (residual copies, post-delete retrieval hits);
* nothing comes back during replay -- measured on the seven daily record sets,
  against the post-deletion state, so residue is not silently counted twice;
* nothing unrelated is destroyed -- measured by the surviving probes and by the
  surviving record set the solver reports.

The fixture is eight synthetic scenarios. Twenty-four drill executions and
fifty-six day evaluations are deterministic re-executions of those eight, not
independent samples; these tests never turn them into a rate with a confidence
interval.
"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from eval.harness import gates as gates_mod
from eval.harness.config import Thresholds
from eval.harness.runner import run_block
from eval.harness.scorers import score_b4d
from eval.harness.solvers.base import SolveResult
from eval.harness.solvers.mutations import MUTATIONS
from eval.harness.taskset import (DELETION_DRILLS, DERIVED_SURFACES, REPLAY_DAYS,
                                  TaskSetError, entry_matches_probe, load_taskset,
                                  load_tasksets)

REPO = Path(__file__).resolve().parents[2]


def _payload(block: str) -> dict:
    return json.loads((REPO / "eval" / "tasks" / block.lower() / "tasks.json")
                      .read_text(encoding="utf-8"))


def _load_variant(payload: dict) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "tasks.json").write_text(json.dumps(payload), encoding="utf-8")
        load_taskset(tmp)
        return tmp


def _gate_map(summary: dict, block: str, clean: dict | None = None) -> dict:
    return {result.gate: result for result in
            gates_mod.evaluate_block(block, summary, clean, Thresholds())}


def _notes(**overrides) -> dict:
    """A reference-shaped set of notes for direct scorer tests."""
    notes = {"records": ["r2"],
             "surfaces": {name: [] for name in DERIVED_SURFACES},
             "present_after_delete": ["r2"],
             "drills": [{"drill": i, "residue": 0} for i in range(1, DELETION_DRILLS + 1)],
             "replay": [{"day": day, "present": ["r2"]} for day in range(1, REPLAY_DAYS + 1)],
             "deleted": ["r1"]}
    notes.update(overrides)
    return notes


class B4DFixtureTests(unittest.TestCase):
    """The fixture must describe a deletion scenario that can actually fail."""

    @classmethod
    def setUpClass(cls):
        cls.sets = load_tasksets(REPO / "eval" / "tasks")
        cls.taskset = cls.sets["B4D"]
        cls.clean = run_block(cls.taskset, solver_name="stub")
        cls.by_id = {record.task_id: record for record in cls.clean.records}

    def test_fixture_is_synthetic_and_preregistered(self):
        payload = _payload("B4D")
        self.assertTrue(payload["synthetic"])
        self.assertEqual(payload["data_class"], "synthetic")
        self.assertEqual(payload["block"], "B4D")
        prereg = payload["preregistration"]
        for field in ("spec", "derived_from", "sample_counts", "scoring", "thresholds",
                      "mutations", "provenance"):
            self.assertIn(field, prereg)
        self.assertIn("not independent samples", prereg["sample_counts"]["note"])

    def test_published_sample_counts_match_the_fixture(self):
        payload = _payload("B4D")
        counts = payload["preregistration"]["sample_counts"]
        tasks = payload["tasks"]
        self.assertEqual(counts["tasks"], len(tasks))
        self.assertEqual(counts["drill_executions"],
                         sum(task["drills"] for task in tasks))
        self.assertEqual(counts["replay_day_evaluations"],
                         sum(task["replay_days"] for task in tasks))
        self.assertEqual(counts["probes"], sum(len(task["probes"]) for task in tasks))
        self.assertEqual(counts["deleted_records"],
                         sum(len(task["delete"]) for task in tasks))
        self.assertEqual(counts["surface_entries"],
                         sum(len(entries) for task in tasks
                             for entries in task["surfaces"].values()))

    def test_every_scenario_deletes_something_and_keeps_something(self):
        for task in self.taskset.tasks:
            self.assertTrue(task["delete"], task["id"])
            survivors = set(record["record_id"] for record in task["records"])
            survivors -= set(task["delete"])
            self.assertTrue(survivors, f"{task['id']} deletes every record")
            self.assertEqual(sorted(survivors), sorted(task["expected"]["surviving_records"]))

    def test_clean_reference_run_meets_every_gate(self):
        summary = self.clean.summary
        self.assertEqual(summary["attempts"], len(self.taskset))
        self.assertEqual(summary["successes"], summary["attempts"])
        self.assertEqual(summary["false_successes"], 0)
        for key in ("residual_copies", "post_delete_hits", "resurrections",
                    "overdeleted", "unreported_states"):
            self.assertEqual(summary[key], 0, key)
        self.assertEqual(summary["replay_completeness"], 1.0)
        for gate, result in _gate_map(summary, "B4D").items():
            self.assertNotEqual(result.status, "fail", f"{gate} failed on the clean run")

    def test_deleted_record_is_gone_from_every_derived_surface(self):
        for task in self.taskset.tasks:
            record = self.by_id[task["id"]]
            residue = record.detail["residue_by_surface"]
            self.assertEqual(set(residue), set(DERIVED_SURFACES))
            for surface, entry_ids in residue.items():
                self.assertEqual(entry_ids, [], f"{task['id']} left {surface} residue")
            self.assertEqual(record.metrics["residual_copies"], 0)

    def test_post_delete_probes_separate_absent_from_present(self):
        for task in self.taskset.tasks:
            record = self.by_id[task["id"]]
            self.assertEqual(record.metrics["post_delete_hits"], 0)
            self.assertEqual(record.metrics["overdeleted"], 0)
            self.assertTrue(any(p["expect"] == "absent" for p in task["probes"]))
        tasks_with_survivor_probe = [task for task in self.taskset.tasks
                                     if any(p["expect"] == "present" for p in task["probes"])]
        self.assertTrue(tasks_with_survivor_probe,
                        "no scenario checks that a surviving record still answers")

    def test_replay_reports_every_required_day(self):
        for task in self.taskset.tasks:
            record = self.by_id[task["id"]]
            self.assertEqual(record.detail["drills_reported"], DELETION_DRILLS)
            self.assertEqual(record.detail["replay_days_reported"], REPLAY_DAYS)
            self.assertEqual(record.metrics["replay_complete"], 1)


class B4DScoringTests(unittest.TestCase):
    """Residue, retrieval hits, resurrections and over-deletion are separate."""

    @classmethod
    def setUpClass(cls):
        cls.taskset = load_tasksets(REPO / "eval" / "tasks")["B4D"]
        cls.clean = run_block(cls.taskset, solver_name="stub")
        cls.task = next(t for t in cls.taskset.tasks if t["id"] == "b4d-001")

    # -- planted regressions -------------------------------------------------
    def _mutation(self, name: str):
        mutant = run_block(self.taskset, solver_name="stub", mutations=(name,))
        return _gate_map(mutant.summary, "B4D", self.clean.summary), mutant

    def test_primary_only_delete_leaves_residue(self):
        gates, mutant = self._mutation("delete_primary_only")
        self.assertEqual(MUTATIONS["delete_primary_only"].expected_gate,
                         "deletion_residue_zero")
        self.assertEqual(gates["deletion_residue_zero"].status, "fail")
        self.assertGreater(mutant.summary["residual_copies"], 0)
        # The copies were never removed, so they are residue, not resurrections.
        self.assertEqual(mutant.summary["resurrections"], 0)

    def test_keep_derived_copy_is_caught_by_post_delete_retrieval(self):
        gates, mutant = self._mutation("keep_derived_copy")
        self.assertEqual(MUTATIONS["keep_derived_copy"].expected_gate,
                         "post_delete_hit_zero")
        self.assertEqual(gates["post_delete_hit_zero"].status, "fail")
        self.assertGreater(mutant.summary["post_delete_hits"], 0)
        self.assertEqual(mutant.summary["resurrections"], 0)

    def test_resurrect_on_replay_is_caught_by_the_replay_window(self):
        gates, mutant = self._mutation("resurrect_on_replay")
        self.assertEqual(MUTATIONS["resurrect_on_replay"].expected_gate,
                         "resurrection_zero")
        self.assertEqual(gates["resurrection_zero"].status, "fail")
        self.assertGreater(mutant.summary["resurrections"], 0)
        # Isolation: the deletion itself was clean, so the residue gate must pass.
        # That is what makes the seven-day replay a measurement and not a duplicate
        # of the deletion check.
        self.assertEqual(mutant.summary["residual_copies"], 0)
        self.assertEqual(mutant.summary["post_delete_hits"], 0)
        self.assertEqual(gates["deletion_residue_zero"].status, "pass")
        self.assertEqual(gates["post_delete_hit_zero"].status, "pass")

    # -- direct scorer behaviour --------------------------------------------
    def test_one_residual_copy_is_enough_to_fail(self):
        notes = _notes()
        notes["surfaces"]["cache"] = [{"entry_id": "ans-1", "record_id": "r1",
                                      "text": "cached answer"}]
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertFalse(scored["success"])
        self.assertEqual(scored["metrics"]["residual_copies"], 1)

    def test_unreported_store_state_fails_closed(self):
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes={}))
        self.assertFalse(scored["success"])
        self.assertTrue(scored["false_success"])
        self.assertEqual(scored["metrics"]["unreported_states"], 1)
        self.assertEqual(scored["metrics"]["replay_complete"], 0)

    def test_over_deletion_destroys_a_survivor_probe(self):
        task = dict(self.task)
        task["probes"] = list(self.task["probes"]) + [
            {"probe_id": "p-survivor", "record_id": "r2", "text": "survivor text",
             "expect": "present"}]
        notes = _notes()
        scored = score_b4d(task, SolveResult(status="done", message="", notes=notes))
        self.assertGreaterEqual(scored["metrics"]["overdeleted"], 1)
        self.assertFalse(scored["success"])

    def test_a_surviving_record_missing_from_the_report_is_not_a_success(self):
        notes = _notes(records=[])
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertGreaterEqual(scored["metrics"]["overdeleted"], 1)
        self.assertFalse(scored["success"])

    def test_five_replay_days_is_not_a_seven_day_replay(self):
        notes = _notes()
        notes["replay"] = notes["replay"][:5]
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["replay_complete"], 0)
        self.assertFalse(scored["success"])

    def test_three_drills_are_required(self):
        notes = _notes()
        notes["drills"] = notes["drills"][:2]
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["replay_complete"], 0)

    def test_a_record_that_returns_after_deletion_is_a_resurrection(self):
        notes = _notes()
        notes["replay"][3]["present"] = ["r1", "r2"]
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["resurrections"], 1)


class B4DGatesTests(unittest.TestCase):
    """Gate wiring: names, thresholds, isolation."""

    @classmethod
    def setUpClass(cls):
        cls.sets = load_tasksets(REPO / "eval" / "tasks")
        cls.taskset = cls.sets["B4D"]
        cls.clean = run_block(cls.taskset, solver_name="stub")

    def _gates(self):
        return _gate_map(self.clean.summary, "B4D")

    def test_every_deletion_metric_has_a_zero_tolerance_gate(self):
        metrics = {metric for metric, _ in gates_mod.BLOCK_METRIC_GATES["B4D"]}
        self.assertEqual(metrics, {"residual_copies", "post_delete_hits",
                                   "resurrections", "overdeleted", "unreported_states"})
        for _, gate in gates_mod.BLOCK_METRIC_GATES["B4D"]:
            self.assertTrue(gate.endswith("_zero"), gate)
            self.assertEqual(self._gates()[gate].status, "pass")

    def test_replay_completeness_is_all_or_nothing(self):
        self.assertEqual(Thresholds().min_replay_completeness, 1.0)
        summary = dict(self.clean.summary)
        summary["replay_completeness"] = 1.0
        self.assertEqual(_gate_map(summary, "B4D")["deletion_replay_completeness"].status,
                         "pass")
        # Seven of eight tasks fully replayed is a partial replay, and C-02-AC3
        # does not allow partial coverage of the fixed schedule.
        summary["replay_completeness"] = 7 / 8
        self.assertEqual(_gate_map(summary, "B4D")["deletion_replay_completeness"].status,
                         "fail")

    def test_deletion_gates_do_not_fire_on_other_families(self):
        deletion_only = {name for _, name in gates_mod.BLOCK_METRIC_GATES["B4D"]}
        deletion_only |= {name for _, name, _, _ in gates_mod.BLOCK_RATE_GATES["B4D"]}
        other = deletion_only & {name for family in ("B4", "B16", "B2")
                                 for _, name in gates_mod.BLOCK_METRIC_GATES.get(family, ())}
        self.assertEqual(other, set())
        b4_clean = run_block(self.sets["B4"], solver_name="stub").summary
        fired = {result.gate for result in
                 gates_mod.evaluate_block("B4", b4_clean, b4_clean, Thresholds())}
        self.assertEqual(fired & deletion_only, set())

    def test_success_drop_gate_catches_the_deletion_regressions(self):
        for name in ("delete_primary_only", "keep_derived_copy", "resurrect_on_replay"):
            mutant = run_block(self.taskset, solver_name="stub", mutations=(name,))
            gates = _gate_map(mutant.summary, "B4D", self.clean.summary)
            self.assertEqual(gates["success_drop"].status, "fail", name)


class B4DMutationRegistryTests(unittest.TestCase):
    def test_the_three_required_regressions_are_registered(self):
        expected = {"delete_primary_only": "deletion_residue_zero",
                    "keep_derived_copy": "post_delete_hit_zero",
                    "resurrect_on_replay": "resurrection_zero"}
        for name, gate in expected.items():
            self.assertIn(name, MUTATIONS)
            self.assertEqual(MUTATIONS[name].block, "B4D")
            self.assertEqual(MUTATIONS[name].expected_gate, gate)
            self.assertIn(gate, {name for _, name in gates_mod.BLOCK_METRIC_GATES["B4D"]})

    def test_no_mutation_targets_a_gate_the_family_does_not_publish(self):
        published = {name for _, name in gates_mod.BLOCK_METRIC_GATES["B4D"]}
        published |= {name for _, name, _, _ in gates_mod.BLOCK_RATE_GATES["B4D"]}
        for name, mutation in MUTATIONS.items():
            if mutation.block != "B4D":
                continue
            self.assertIn(mutation.expected_gate, published, name)


class B4DCliTests(unittest.TestCase):
    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "eval.harness", *args],
                              capture_output=True, text=True, cwd=REPO, check=False)

    def test_list_shows_the_deletion_family(self):
        proc = self._cli("list")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("B4D", proc.stdout)
        self.assertIn("delete_primary_only", proc.stdout)

    def test_scoped_x10_covers_the_deletion_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp, "x10.json")
            proc = self._cli("x10", "--block", "B4D", "--results", tmp,
                             "--report", str(report),
                             "--baseline", str(Path(tmp, "missing.json")))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["scope"], ["B4D"])
            names = {row["mutation"] for row in payload["rows"]}
            self.assertEqual(names, {"delete_primary_only", "keep_derived_copy",
                                     "resurrect_on_replay"})
            for row in payload["rows"]:
                self.assertTrue(row["detected"], row)
                self.assertIn(row["expected_gate"], row["gates_failed"])
                self.assertTrue((REPO / row["evidence"]).is_file()
                                or Path(row["evidence"]).is_file())

    def test_run_writes_a_b4d_ledger_with_the_published_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._cli("run", "--block", "B4D", "--results", tmp)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            ledgers = sorted(Path(tmp).glob("*-B4D-*.jsonl"))
            self.assertEqual(len(ledgers), 1)
            rows = [json.loads(line) for line in
                    ledgers[0].read_text(encoding="utf-8").splitlines() if line.strip()]
            records = [row for row in rows if row.get("task_id")]
            self.assertEqual(len(records), len(load_tasksets(
                REPO / "eval" / "tasks")["B4D"]))
            self.assertTrue(all(row["success"] for row in records))
            drills = sum(row["detail"]["drills_reported"] for row in records)
            days = sum(row["detail"]["replay_days_reported"] for row in records)
            self.assertEqual(drills, 24)
            self.assertEqual(days, 56)


class B4DDocBookkeepingTests(unittest.TestCase):
    """Published gate names and sample counts must match the harness."""

    @classmethod
    def setUpClass(cls):
        cls.backlog = json.loads((REPO / "docs" / "product" / "family-backlog.json")
                                 .read_text(encoding="utf-8"))
        cls.family = next(f for f in cls.backlog["families"] if f["id"] == "B4")
        cls.coverage = json.loads((REPO / "docs" / "product" / "ac-coverage.json")
                                  .read_text(encoding="utf-8"))

    def test_backlog_gate_names_match_the_harness_exactly(self):
        published = set(self.family["scoring"]["gates"])
        harness = {name for _, name in gates_mod.BLOCK_METRIC_GATES["B4D"]}
        harness |= {name for _, name, _, _ in gates_mod.BLOCK_RATE_GATES["B4D"]}
        self.assertTrue(harness <= published,
                        f"harness gates missing from backlog: {sorted(harness - published)}")
        for name in ("deletion_residue_zero", "post_delete_hit_zero", "resurrection_zero",
                     "overdeletion_zero", "deletion_report_zero",
                     "deletion_replay_completeness"):
            self.assertIn(name, published)
        # The reverse direction: every published zero-tolerance gate for the slice
        # is implemented, so the document cannot promise a gate that does not exist.
        slice_gates = set(self.family["implemented_slice_2"]["gates"])
        self.assertEqual(slice_gates, harness)

    def test_backlog_records_the_sample_counts_without_implying_confidence(self):
        samples = self.family["implemented_slice_2"]["sample_counts"]
        self.assertEqual(samples["tasks"], 8)
        self.assertEqual(samples["drill_executions"], 24)
        self.assertEqual(samples["replay_day_evaluations"], 56)
        self.assertEqual(samples["probes"], 19)
        self.assertIn("Deterministic executions", samples["note"])
        self.assertIn("not independent samples", samples["note"])
        self.assertIn("o confidence interval", samples["note"])

    def test_backlog_lists_both_b4_slices_without_completing_the_family(self):
        self.assertEqual(self.family["status"], "planned")
        self.assertFalse(self.family["implemented_slice"]["family_complete"])
        self.assertFalse(self.family["implemented_slice_2"]["family_complete"])
        self.assertEqual(self.family["implemented_slice_2"]["suite"], "eval/tasks/b4d")
        self.assertEqual(self.backlog["slices_built"]["count"],
                         len(self.backlog["slices_built"]["detail"]))

    def test_only_c02_ac3_is_marked_slice_built(self):
        entry = next(e for e in self.coverage["entries"] if e["ac"] == "C-02-AC3")
        self.assertEqual(entry["status"], "slice_built")
        self.assertEqual(entry["slice"]["suite"], "eval/tasks/b4d")
        self.assertEqual(entry["slice"]["tasks"], 8)
        self.assertTrue(entry["slice"]["not_covered"].strip())
        for ac in ("C-02-AC1", "C-02-AC2"):
            other = next(e for e in self.coverage["entries"] if e["ac"] == ac)
            self.assertEqual(other["status"], "slice_built")
        self.assertEqual(next(e for e in self.coverage["entries"]
                              if e["ac"] == "C-02-AC4")["status"], "planned_suite")
        by_status = self.coverage["summary"]["by_status"]
        self.assertEqual(by_status["slice_built"], 5)
        self.assertEqual(by_status["planned_suite"], 27)
        self.assertNotIn("validated", self.coverage["status_vocabulary"])
        self.assertEqual(sum(by_status.values()), 50)

    def test_slice_does_not_claim_a_capability(self):
        doc = json.loads((REPO / "docs" / "product" / "capabilities.json")
                         .read_text(encoding="utf-8"))
        entries = doc.get("capabilities") or doc.get("evaluation_backlog", {}).get(
            "capabilities", [])
        if isinstance(entries, dict):
            entries = list(entries.values())
        statuses = {entry.get("status") for entry in entries if isinstance(entry, dict)}
        self.assertTrue(statuses)
        self.assertNotIn("implemented", statuses)
        self.assertIn("specified \u00b7 harness-only", statuses)
        self.assertEqual(doc["evaluation_backlog"]["families"]["implemented"], 0)

    def test_no_slice_task_names_a_model_or_vendor(self):
        banned = ("openai", "gpt", "claude", "gemini", "llama", "qwen", "mistral",
                  "h100", "a100", "rtx")
        text = (REPO / "eval" / "tasks" / "b4d" / "tasks.json").read_text(
            encoding="utf-8").lower()
        self.assertEqual([token for token in banned if token in text], [])


class B4DProbeHelperTests(unittest.TestCase):
    def test_probe_matching_ignores_case_and_punctuation(self):
        entry = {"entry_id": "i1", "record_id": "r1", "tokens": ["Sample", "Parcel-CO"]}
        self.assertTrue(entry_matches_probe(entry, "sample parcel co"))
        self.assertFalse(entry_matches_probe(entry, "parcel x"))

    def test_probe_helper_uses_cache_text_too(self):
        entry = {"entry_id": "c1", "record_id": "r1",
                 "text": "cached answer: courier is Sample Freight"}
        self.assertTrue(entry_matches_probe(entry, "sample freight"))


if __name__ == "__main__":
    unittest.main()
