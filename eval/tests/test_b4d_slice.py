"""M5 slice 2: deletion and derived-surface residue (C-02-AC3).

C-02-AC3 asks for several things at once, and the slice is built so that each one
can fail on its own:

* deletion completes within one cycle -- measured on the store *after* the three
  deletion drills (residual copies, post-delete retrieval hits);
* nothing comes back during replay -- measured on the seven daily record sets,
  against the post-deletion state, so residue is not silently counted twice;
* a backup taken before the deletion, restored during replay, drops what was
  deleted since and keeps what was not -- measured on the store immediately
  before and after the restore, so an unchanged pre-restore state is never
  scored as a restore resurrection;
* nothing unrelated is destroyed -- measured by the surviving probes and by the
  surviving record set the solver reports;
* a restore that stops part way can be retried on the same day -- measured per
  attempt, so an interrupted first attempt is incomplete rather than failed, and
  what is scored is the recovered final state: no resurrected record, no lost
  survivor, no duplicated entry, no repeated effect.

The fixture is fifteen synthetic scenarios. Forty-five drill executions,
one hundred and five day evaluations, seven restore events and eleven restore
attempts are deterministic re-executions, not independent samples; these tests
never turn them into a rate with a confidence interval.
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
from eval.harness.solvers import build_solver
from eval.harness.solvers.base import SolveResult, ToolContext
from eval.harness.solvers.mutations import MUTATIONS
from eval.harness.taskset import (B4D_ZERO_COUNTERS, DELETION_DRILLS, DERIVED_SURFACES,
                                  REPLAY_DAYS, TaskSetError, entry_matches_probe,
                                  load_taskset, load_tasksets, parse_timestamp)
from eval.harness.tools import Executor, World

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

    def test_backup_scenarios_declare_a_backup_before_the_deletion(self):
        backed = [task for task in self.taskset.tasks if task.get("backup")]
        self.assertEqual(len(backed), 7, "expected seven restore scenarios")
        for task in backed:
            backup = task["backup"]
            taken = parse_timestamp(backup["taken_at"])
            deleted = parse_timestamp(task["deleted_at"])
            self.assertIsNotNone(taken, task["id"])
            self.assertIsNotNone(deleted, task["id"])
            self.assertLess(taken, deleted, f"{task['id']} backup does not predate the deletion")
            self.assertGreaterEqual(backup["restore_day"], 1)
            self.assertLessEqual(backup["restore_day"], task["replay_days"])
            held = set(backup["contents"]["records"])
            self.assertTrue(held & set(task["delete"]),
                            f"{task['id']} backup holds nothing deleted")
            self.assertTrue(held - set(task["delete"]),
                            f"{task['id']} backup holds nothing surviving")

    def test_backup_scenarios_cover_filtering_preservation_and_multi_record(self):
        backed = [task for task in self.taskset.tasks if task.get("backup")]
        self.assertTrue(any(len(task["backup"]["contents"]["records"])
                            - len(task["delete"]) >= 2 for task in backed),
                        "no restore scenario has to preserve two unrelated records")
        self.assertTrue(any(len(set(task["backup"]["contents"]["records"]) & set(task["delete"])) >= 2
                            for task in backed),
                        "no restore scenario has to filter two deleted records")

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
                    "overdeleted", "unreported_states", "restore_resurrections",
                    "restore_dropped", "restore_residue", "restore_unrecovered",
                    "restore_duplicate_effects", "restore_attempt_mismatch"):
            self.assertEqual(summary[key], 0, key)
        self.assertEqual(summary["replay_completeness"], 1.0)
        self.assertEqual(summary["restore_events"], 7)
        self.assertEqual(summary["restore_attempts"], 11)
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

    def test_restore_is_reported_before_and_after_on_its_declared_day(self):
        for task in self.taskset.tasks:
            if not task.get("backup"):
                continue
            record = self.by_id[task["id"]]
            self.assertEqual(record.detail["backup_id"], task["backup"]["backup_id"])
            self.assertEqual(record.detail["restore_day"], task["backup"]["restore_day"])
            self.assertTrue(record.detail["restore_applied"])
            self.assertEqual(record.metrics["restore_resurrections"], 0)
            self.assertEqual(record.metrics["restore_dropped"], 0)
            self.assertEqual(record.metrics["restore_residue"], 0)
            # Every surviving record the backup holds is back in the store afterwards.
            self.assertTrue(set(task["expected"]["restore_restored_records"])
                            <= set(record.detail["restore_records_after"]))

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
                                   "resurrections", "overdeleted", "restore_resurrections",
                                   "restore_dropped", "restore_residue", "restore_unrecovered",
                                   "restore_duplicate_effects", "restore_attempt_mismatch",
                                   "unreported_states"})
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


def _attempt(before: list[str], after: list[str], *, number: int = 1,
             complete: bool = True, surfaces_before: dict | None = None,
             surfaces_after: dict | None = None) -> dict:
    """One restore attempt as the scorer audits it."""
    return {"attempt": number, "complete": complete,
            "records_before": list(before), "records_after": list(after),
            "surfaces_before": (surfaces_before if surfaces_before is not None
                                else {name: [] for name in DERIVED_SURFACES}),
            "surfaces_after": (surfaces_after if surfaces_after is not None
                               else {name: [] for name in DERIVED_SURFACES})}


def _restore_notes(**overrides) -> dict:
    """Reference-shaped notes for a backup task (b4d-009: r1 deleted, r2 survives)."""
    restore = {"backup_id": "bkp-009", "day": 4,
               "attempts": [_attempt(["r2"], ["r2"])]}
    notes = {"records": ["r2"],
             "surfaces": {name: [] for name in DERIVED_SURFACES},
             "present_after_delete": ["r2"],
             "drills": [{"drill": i, "residue": 0} for i in range(1, DELETION_DRILLS + 1)],
             "replay": [{"day": day, "present": ["r2"]} for day in range(1, REPLAY_DAYS + 1)],
             "deleted": ["r1"], "restore": restore}
    notes.update(overrides)
    return notes


class B4DBackupRestoreTests(unittest.TestCase):
    """Restore scenarios: validation, scoring and the three restore regressions."""

    @classmethod
    def setUpClass(cls):
        cls.taskset = load_tasksets(REPO / "eval" / "tasks")["B4D"]
        cls.clean = run_block(cls.taskset, solver_name="stub")
        cls.task = next(t for t in cls.taskset.tasks if t["id"] == "b4d-009")
        cls.by_id = {record.task_id: record for record in cls.clean.records}

    # -- fail-closed validation ---------------------------------------------
    def _variant(self, mutate) -> dict:
        payload = copy.deepcopy(_payload("B4D"))
        target = next(t for t in payload["tasks"] if t["id"] == "b4d-009")
        mutate(payload, target)
        return payload

    def _assert_refused(self, payload: dict, needle: str | None = None):
        with self.assertRaises(TaskSetError) as caught:
            _load_variant(payload)
        if needle:
            self.assertIn(needle, str(caught.exception))

    def test_backup_without_a_timestamp_is_refused(self):
        def mutate(payload, task):
            del task["backup"]["taken_at"]

        self._assert_refused(self._variant(mutate), "taken_at")

    def test_unparseable_backup_timestamp_is_refused(self):
        def mutate(payload, task):
            task["backup"]["taken_at"] = "last tuesday"

        self._assert_refused(self._variant(mutate), "ISO timestamp")

    def test_backup_taken_after_the_deletion_is_refused(self):
        def mutate(payload, task):
            task["backup"]["taken_at"] = "2026-09-30"

        self._assert_refused(self._variant(mutate), "on or after the deletion")

    def test_backup_without_a_deletion_time_is_refused(self):
        def mutate(payload, task):
            del task["deleted_at"]

        self._assert_refused(self._variant(mutate), "deleted_at")

    def test_restore_day_outside_the_replay_is_refused(self):
        def mutate(payload, task):
            task["backup"]["restore_day"] = 9

        self._assert_refused(self._variant(mutate), "outside the")

    def test_restore_day_zero_is_refused(self):
        def mutate(payload, task):
            task["backup"]["restore_day"] = 0

        self._assert_refused(self._variant(mutate), "outside the")

    def test_backup_holding_nothing_deleted_is_refused(self):
        def mutate(payload, task):
            task["backup"]["contents"]["records"] = ["r2"]
            task["backup"]["contents"]["surfaces"]["index"] = [
                {"entry_id": "i-r2", "record_id": "r2", "tokens": ["sample", "glass"]}]
            task["backup"]["contents"]["surfaces"]["summary"] = [
                {"entry_id": "s-r2", "record_id": "r2", "text": "weekly summary: sample glass"}]
            task["expected"]["restore_filtered_records"] = []

        self._assert_refused(self._variant(mutate), "no deleted record")

    def test_backup_holding_nothing_surviving_is_refused(self):
        def mutate(payload, task):
            task["backup"]["contents"]["records"] = ["r1"]
            task["backup"]["contents"]["surfaces"]["index"] = [
                {"entry_id": "i-r1", "record_id": "r1", "tokens": ["sample", "metals"]}]
            task["backup"]["contents"]["surfaces"]["cache"] = []
            task["backup"]["contents"]["surfaces"]["summary"] = [
                {"entry_id": "s-r1", "record_id": "r1", "text": "weekly summary: sample metals"}]
            task["expected"]["restore_restored_records"] = []

        self._assert_refused(self._variant(mutate), "no surviving record")

    def test_backup_missing_a_surface_is_refused(self):
        def mutate(payload, task):
            del task["backup"]["contents"]["surfaces"]["cache"]

        self._assert_refused(self._variant(mutate), "missing surface")

    def test_backup_entry_naming_an_unheld_record_is_refused(self):
        def mutate(payload, task):
            task["backup"]["contents"]["surfaces"]["cache"].append(
                {"entry_id": "c-ghost", "record_id": "r9", "text": "cached answer: ghost"})

        self._assert_refused(self._variant(mutate), "which the backup does not hold")

    def test_backup_without_a_derived_copy_of_a_deleted_record_is_refused(self):
        def mutate(payload, task):
            task["backup"]["contents"]["surfaces"]["index"] = [
                entry for entry in task["backup"]["contents"]["surfaces"]["index"]
                if entry["record_id"] == "r2"]
            task["backup"]["contents"]["surfaces"]["summary"] = [
                entry for entry in task["backup"]["contents"]["surfaces"]["summary"]
                if entry["record_id"] == "r2"]

        self._assert_refused(self._variant(mutate), "no derived copy of the deleted")

    def test_gold_restore_lists_must_match_the_backup(self):
        def mutate(payload, task):
            task["expected"]["restore_restored_records"] = ["r1"]

        self._assert_refused(self._variant(mutate), "restore_restored_records disagree")

    def test_tolerated_restore_residue_is_refused(self):
        def mutate(payload, task):
            task["expected"]["restore_residue"] = 1

        self._assert_refused(self._variant(mutate), "zero-tolerance")

    def test_scenario_without_a_backup_may_not_score_restore_counters(self):
        payload = copy.deepcopy(_payload("B4D"))

        def mutate(payload, task):
            plain = next(t for t in payload["tasks"] if t["id"] == "b4d-002")
            plain["expected"]["restore_resurrections"] = 1

        mutate(payload, None)
        self._assert_refused(payload, "without a backup")

    def test_backup_holding_an_unknown_record_is_refused(self):
        def mutate(payload, task):
            task["backup"]["contents"]["records"].append("r9")

        self._assert_refused(self._variant(mutate), "unknown records")

    def test_non_synthetic_b4d_fixture_is_refused(self):
        payload = copy.deepcopy(_payload("B4D"))
        payload["synthetic"] = False
        payload["data_class"] = "real"
        self._assert_refused(payload, "synthetic")

    # -- scoring ------------------------------------------------------------
    def test_unfiltered_restore_counts_a_restore_resurrection(self):
        notes = _restore_notes(records=["r1", "r2"])
        notes["restore"]["attempts"][0]["records_after"] = ["r1", "r2"]
        notes["replay"][3]["present"] = ["r1", "r2"]
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["restore_resurrections"], 1)
        self.assertEqual(scored["metrics"]["resurrections"], 1)
        self.assertFalse(scored["success"])

    def test_unchanged_pre_restore_state_is_not_a_restore_resurrection(self):
        # r1 is already in the store before the restore (live residue). The restore
        # changed nothing, so it did not resurrect anything.
        notes = _restore_notes(records=["r1", "r2"])
        notes["surfaces"]["index"] = [{"entry_id": "i-r1", "record_id": "r1",
                                       "tokens": ["sample", "metals"]}]
        notes["restore"]["attempts"][0]["records_before"] = ["r1", "r2"]
        notes["restore"]["attempts"][0]["records_after"] = ["r1", "r2"]
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["restore_resurrections"], 0)
        self.assertEqual(scored["metrics"]["residual_copies"], 1)  # residue, not resurrection
        self.assertFalse(scored["success"])

    def test_restore_that_drops_an_unrelated_record_is_counted(self):
        notes = _restore_notes(records=[])
        notes["restore"]["attempts"][0]["records_after"] = []
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["restore_dropped"], 1)
        self.assertFalse(scored["success"])

    def test_restore_residue_counts_only_copies_the_restore_added(self):
        pre_existing = {"entry_id": "i-r1", "record_id": "r1", "tokens": ["sample", "metals"]}
        notes = _restore_notes()
        notes["restore"]["attempts"][0]["surfaces_before"] = {
            "index": [pre_existing], "cache": [], "summary": []}
        notes["restore"]["attempts"][0]["surfaces_after"] = {
            "index": [pre_existing], "cache": [], "summary": [],
        }
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["restore_residue"], 0)
        notes["restore"]["attempts"][0]["surfaces_after"]["summary"] = [
            {"entry_id": "s-r1", "record_id": "r1", "text": "weekly summary: sample metals"}]
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["restore_residue"], 1)
        self.assertFalse(scored["success"])

    def test_missing_restore_report_fails_closed(self):
        notes = _restore_notes(restore=None)
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["unreported_states"], 1)
        self.assertEqual(scored["metrics"]["restore_applied"], 0)
        self.assertFalse(scored["success"])

    def test_a_restore_reported_on_the_wrong_day_leaves_the_run_incomplete(self):
        notes = _restore_notes()
        notes["restore"]["day"] = 6
        scored = score_b4d(self.task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["restore_applied"], 0)
        self.assertEqual(scored["metrics"]["replay_complete"], 0)
        self.assertFalse(scored["success"])

    # -- planted regressions ------------------------------------------------
    def _gates(self, name: str) -> dict:
        mutant = run_block(self.taskset, solver_name="stub", mutations=(name,))
        return _gate_map(mutant.summary, "B4D", self.clean.summary), mutant

    def test_unfiltered_restore_is_caught(self):
        gates, mutant = self._gates("restore_without_filter")
        self.assertEqual(MUTATIONS["restore_without_filter"].expected_gate,
                         "restore_resurrection_zero")
        self.assertEqual(gates["restore_resurrection_zero"].status, "fail")
        self.assertGreater(mutant.summary["restore_resurrections"], 0)

    def test_restore_that_deletes_unrelated_records_is_caught(self):
        gates, mutant = self._gates("restore_drops_unrelated")
        self.assertEqual(MUTATIONS["restore_drops_unrelated"].expected_gate,
                         "restore_survivor_zero")
        self.assertEqual(gates["restore_survivor_zero"].status, "fail")
        self.assertGreater(mutant.summary["restore_dropped"], 0)
        # Correlated, and honestly so: dropping an unrelated record is also over-deletion.
        self.assertEqual(gates["overdeletion_zero"].status, "fail")

    def test_restore_that_bypasses_tombstones_for_derived_copies_is_caught(self):
        gates, mutant = self._gates("restore_skips_tombstones")
        self.assertEqual(MUTATIONS["restore_skips_tombstones"].expected_gate,
                         "restore_residue_zero")
        self.assertEqual(gates["restore_residue_zero"].status, "fail")
        self.assertGreater(mutant.summary["restore_residue"], 0)
        # The primary record was filtered correctly, so the restore-resurrection gate
        # stays clean: that is what makes the two failures distinguishable.
        self.assertEqual(mutant.summary["restore_resurrections"], 0)
        self.assertEqual(gates["restore_resurrection_zero"].status, "pass")

    def test_restore_mutations_leave_non_restore_scenarios_alone(self):
        mutant = run_block(self.taskset, solver_name="stub",
                           mutations=("restore_without_filter",))
        backed = {task["id"] for task in self.taskset.tasks if task.get("backup")}
        plain = [record for record in mutant.records if record.task_id not in backed]
        self.assertTrue(plain)
        for record in plain:
            self.assertEqual(record.metrics["restores"], 0)
            self.assertEqual(record.metrics["restore_resurrections"], 0)
            self.assertEqual(record.metrics["restore_residue"], 0)

    def test_replay_resurrection_mutation_is_still_caught_with_backups_present(self):
        gates, mutant = self._gates("resurrect_on_replay")
        self.assertEqual(gates["resurrection_zero"].status, "fail")
        self.assertEqual(mutant.summary["restore_resurrections"], 0)


class B4DRestoreGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.taskset = load_tasksets(REPO / "eval" / "tasks")["B4D"]
        cls.clean = run_block(cls.taskset, solver_name="stub")

    def test_restore_metrics_have_zero_tolerance_gates(self):
        pairs = dict(gates_mod.BLOCK_METRIC_GATES["B4D"])
        self.assertEqual(pairs["restore_resurrections"], "restore_resurrection_zero")
        self.assertEqual(pairs["restore_dropped"], "restore_survivor_zero")
        self.assertEqual(pairs["restore_residue"], "restore_residue_zero")
        self.assertEqual(pairs["restore_unrecovered"], "restore_recovery_zero")
        self.assertEqual(pairs["restore_duplicate_effects"], "restore_idempotency_zero")
        self.assertEqual(pairs["restore_attempt_mismatch"], "restore_attempt_count_zero")
        for gate in ("restore_resurrection_zero", "restore_survivor_zero",
                     "restore_residue_zero", "restore_recovery_zero",
                     "restore_idempotency_zero", "restore_attempt_count_zero"):
            self.assertEqual(_gate_map(self.clean.summary, "B4D")[gate].status, "pass")

    def test_no_restore_counters_are_invited_by_the_fixture(self):
        fixture = _payload("B4D")
        for task in fixture["tasks"]:
            for key in ("restore_resurrections", "restore_dropped", "restore_residue"):
                self.assertEqual(task["expected"].get(key, 0), 0, f"{task['id']} {key}")
        self.assertIn("restore_residue", B4D_ZERO_COUNTERS)

    def test_restore_gates_do_not_fire_on_other_families(self):
        restore_only = {"restore_resurrection_zero", "restore_survivor_zero",
                        "restore_residue_zero", "restore_recovery_zero",
                        "restore_idempotency_zero", "restore_attempt_count_zero"}
        b4_clean = run_block(load_tasksets(REPO / "eval" / "tasks")["B4"],
                             solver_name="stub").summary
        fired = {result.gate for result in
                 gates_mod.evaluate_block("B4", b4_clean, b4_clean, Thresholds())}
        self.assertEqual(fired & restore_only, set())


class B4DRetryRestoreTests(unittest.TestCase):
    """Interrupted restores and retries: state transitions, gates, failures.

    A retry is scored on the final state. An interrupted first attempt is an
    incomplete state, not a failure; abandoning it is a failure; completing it
    while duplicating every entry is a different failure again.
    """

    @classmethod
    def setUpClass(cls):
        cls.taskset = load_tasksets(REPO / "eval" / "tasks")["B4D"]
        cls.clean = run_block(cls.taskset, solver_name="stub")
        cls.retried = [task for task in cls.taskset.tasks
                       if len(task.get("backup", {}).get("attempts") or []) > 1]
        cls.by_id = {record.task_id: record for record in cls.clean.records}

    # -- fixture and gold state ---------------------------------------------
    def test_four_scenarios_retry_an_interrupted_restore(self):
        self.assertEqual(len(self.retried), 4)
        for task in self.retried:
            attempts = task["backup"]["attempts"]
            self.assertGreaterEqual(len(attempts), 2, task["id"])
            self.assertFalse(attempts[0]["expect_complete"], task["id"])
            self.assertTrue(attempts[-1]["expect_complete"], task["id"])
            self.assertEqual(task["expected"]["restore_attempts"], len(attempts))
            self.assertTrue(all(attempt["attempt"] == index
                                for index, attempt in enumerate(attempts, start=1)))

    def test_interrupted_attempts_leave_declared_records_missing(self):
        with_missing = [task for task in self.retried
                        if task["expected"]["restore_interrupted_missing"]]
        self.assertTrue(with_missing,
                        "no retried scenario leaves a record missing until the retry")
        for task in with_missing:
            missing = set(task["expected"]["restore_interrupted_missing"])
            self.assertLessEqual(missing, set(task["expected"]["restore_restored_records"]))
            # Every such record is a survivor the backup holds: nothing deleted
            # may be waited for, since the retry must never bring it back.
            self.assertFalse(missing & set(task["delete"]), task["id"])

    def test_retried_scenarios_declare_recovery_probes(self):
        for task in self.retried:
            probes = task.get("recovery_probes") or []
            self.assertTrue(probes, task["id"])
            self.assertTrue(any(probe["expect"] == "absent" for probe in probes), task["id"])
            self.assertTrue(any(probe["expect"] == "present" for probe in probes), task["id"])

    def test_pre_restore_loss_declares_a_recovery_probe_for_every_lost_record(self):
        lost_scenarios = [task for task in self.taskset.tasks if task.get("pre_restore_missing")]
        self.assertTrue(lost_scenarios, "no scenario loses a record before the restore")
        for task in lost_scenarios:
            probed = {probe["record_id"] for probe in task.get("recovery_probes") or []
                      if probe["expect"] == "present"}
            self.assertLessEqual(set(task["pre_restore_missing"]), probed, task["id"])
            # The loss happens after the deletion cycle, so it is not residue.
            self.assertFalse(set(task["pre_restore_missing"]) & set(task["delete"]))

    def test_clean_retry_reports_incomplete_then_complete(self):
        for task in self.retried:
            record = self.by_id[task["id"]]
            self.assertTrue(record.success, task["id"])
            self.assertEqual(record.metrics["restore_attempts"], len(task["backup"]["attempts"]))
            self.assertEqual(record.metrics["restore_unrecovered"], 0)
            self.assertEqual(record.metrics["restore_duplicate_effects"], 0)
            self.assertEqual(record.metrics["restore_attempt_mismatch"], 0)
            states = record.detail["restore_attempts"]
            self.assertEqual([state["complete"] for state in states],
                             [attempt["expect_complete"]
                              for attempt in task["backup"]["attempts"]])

    # -- direct scoring of notes --------------------------------------------
    @staticmethod
    def _notes(task, *mutations) -> dict:
        solver = build_solver("stub", mutations)
        ctx = ToolContext(Executor(World()), {}, mutations, task["id"])
        view = copy.deepcopy(task)
        view["block"] = "B4D"
        notes = solver.solve(view, ctx).notes
        assert notes is not None
        return notes

    def test_an_interrupted_first_attempt_is_not_a_failure(self):
        task = next(t for t in self.retried if t["id"] == "b4d-012")
        notes = self._notes(task)
        self.assertFalse(notes["restore"]["attempts"][0]["complete"])
        self.assertTrue(notes["restore"]["attempts"][-1]["complete"])
        scored = score_b4d(task, SolveResult(status="done", message="", notes=notes))
        self.assertTrue(scored["success"], scored["metrics"])
        self.assertEqual(scored["detail"]["restore_attempts"][0]["complete"], False)

    def test_abandoning_the_partial_restore_fails_recovery(self):
        # b4d-014 lost a survivor before the restore, so an abandoned first
        # attempt leaves it missing -- recovery, not residue, is what fails.
        task = next(t for t in self.retried if t["id"] == "b4d-014")
        notes = self._notes(task, "abandon_partial_restore")
        self.assertEqual(len(notes["restore"]["attempts"]), 1)
        scored = score_b4d(task, SolveResult(status="done", message="", notes=notes))
        self.assertFalse(scored["success"])
        self.assertGreater(scored["metrics"]["restore_unrecovered"], 0)
        # The report claims completion the fixture says did not happen.
        self.assertEqual(scored["metrics"]["restore_attempt_mismatch"], 1)

    def test_retrying_from_the_beginning_duplicates_effects(self):
        task = next(t for t in self.retried if t["id"] == "b4d-012")
        notes = self._notes(task, "retry_from_scratch")
        self.assertTrue(all(not attempt["complete"] for attempt in
                            notes["restore"]["attempts"][:1]))
        scored = score_b4d(task, SolveResult(status="done", message="", notes=notes))
        self.assertFalse(scored["success"])
        self.assertGreater(scored["metrics"]["restore_duplicate_effects"], 0)
        # Every record is present and nothing was resurrected: this is purely an
        # idempotency failure, which is why it needs its own counter.
        self.assertEqual(scored["metrics"]["restore_unrecovered"], 0)
        self.assertEqual(scored["metrics"]["restore_resurrections"], 0)

    def test_retrying_without_filtering_resurrects_the_deleted_record(self):
        task = next(t for t in self.retried if t["id"] == "b4d-012")
        notes = self._notes(task, "retry_without_filter")
        scored = score_b4d(task, SolveResult(status="done", message="", notes=notes))
        self.assertFalse(scored["success"])
        self.assertGreaterEqual(scored["metrics"]["restore_resurrections"], 1)

    def test_a_retry_that_reports_fewer_attempts_is_a_mismatch(self):
        task = next(t for t in self.retried if t["id"] == "b4d-012")
        notes = self._notes(task)
        notes["restore"]["attempts"] = notes["restore"]["attempts"][:1]
        notes["restore"]["attempts"][0]["complete"] = True
        scored = score_b4d(task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["restore_attempt_mismatch"], 1)
        self.assertFalse(scored["success"])

    def test_recovery_probe_that_never_resolves_is_unrecovered(self):
        task = next(t for t in self.retried if t["id"] == "b4d-014")
        notes = self._notes(task, "abandon_partial_restore")
        notes["restore"]["attempts"][0]["complete"] = True
        scored = score_b4d(task, SolveResult(status="done", message="", notes=notes))
        self.assertGreater(scored["metrics"]["restore_unrecovered"], 0)
        self.assertFalse(scored["success"])

    def test_a_restore_reported_on_the_wrong_day_is_not_recovery(self):
        task = next(t for t in self.retried if t["id"] == "b4d-012")
        notes = self._notes(task)
        notes["restore"]["day"] = task["backup"]["restore_day"] + 1
        scored = score_b4d(task, SolveResult(status="done", message="", notes=notes))
        self.assertEqual(scored["metrics"]["restore_attempt_mismatch"], 1)
        self.assertEqual(scored["metrics"]["restore_applied"], 0)
        self.assertEqual(scored["metrics"]["replay_complete"], 0)
        self.assertFalse(scored["success"])

    # -- fail-closed fixture validation -------------------------------------
    def _variant(self, mutate) -> dict:
        payload = copy.deepcopy(_payload("B4D"))
        target = next(t for t in payload["tasks"] if t["id"] == "b4d-012")
        mutate(payload, target)
        return payload

    def _assert_refused(self, payload: dict, needle: str | None = None):
        with self.assertRaises(TaskSetError) as caught:
            _load_variant(payload)
        if needle:
            self.assertIn(needle, str(caught.exception))

    def test_empty_attempt_list_is_refused(self):
        def mutate(payload, task):
            task["backup"]["attempts"] = []

        self._assert_refused(self._variant(mutate), "non-empty list")

    def test_duplicate_attempt_number_is_refused(self):
        def mutate(payload, task):
            task["backup"]["attempts"][1]["attempt"] = 1

        self._assert_refused(self._variant(mutate), "twice")

    def test_attempt_numbering_with_a_gap_is_refused(self):
        def mutate(payload, task):
            task["backup"]["attempts"][1]["attempt"] = 3

        self._assert_refused(self._variant(mutate), "ordered 1..n")

    def test_interruption_point_that_leaves_nothing_undone_is_refused(self):
        def mutate(payload, task):
            task["backup"]["attempts"][0]["stop_after_records"] = 3

        self._assert_refused(self._variant(mutate), "leave work undone")

    def test_zero_record_interruption_point_is_refused(self):
        def mutate(payload, task):
            task["backup"]["attempts"][0]["stop_after_records"] = 0
            task["expected"]["restore_interrupted_missing"] = ["r1", "r2", "r3"]

        self._assert_refused(self._variant(mutate), "leave work undone")

    def test_non_integer_interruption_point_is_refused(self):
        def mutate(payload, task):
            task["backup"]["attempts"][0]["stop_after_records"] = "half"

        self._assert_refused(self._variant(mutate), "neither null nor an integer")

    def test_complete_flag_contradicting_the_interruption_point_is_refused(self):
        def mutate(payload, task):
            task["backup"]["attempts"][0]["expect_complete"] = True

        self._assert_refused(self._variant(mutate), "expect_complete=True")

    def test_sequence_ending_on_an_interrupted_attempt_is_refused(self):
        def mutate(payload, task):
            task["backup"]["attempts"][1]["stop_after_records"] = 2
            task["backup"]["attempts"][1]["expect_complete"] = False

        self._assert_refused(self._variant(mutate), "ends on an interrupted attempt")

    def test_retry_after_a_completed_attempt_is_refused(self):
        def mutate(payload, task):
            task["backup"]["attempts"][0]["stop_after_records"] = None
            task["backup"]["attempts"][0]["expect_complete"] = True

        self._assert_refused(self._variant(mutate), "retries after a completed attempt")

    def test_interrupted_missing_that_disagrees_with_the_attempts_is_refused(self):
        def mutate(payload, task):
            task["expected"]["restore_interrupted_missing"] = ["r3"]

        self._assert_refused(self._variant(mutate), "restore_interrupted_missing")

    def test_gold_attempt_count_must_match_the_declared_sequence(self):
        def mutate(payload, task):
            task["expected"]["restore_attempts"] = 1

        self._assert_refused(self._variant(mutate), "declares 2 attempts")

    def test_pre_restore_loss_must_name_a_surviving_record(self):
        def mutate(payload, task):
            task["pre_restore_missing"] = ["r1"]

        self._assert_refused(self._variant(mutate), "surviving records")

    def test_pre_restore_loss_without_a_recovery_probe_is_refused(self):
        payload = copy.deepcopy(_payload("B4D"))
        target = next(t for t in payload["tasks"] if t["id"] == "b4d-014")
        target["pre_restore_missing"] = ["r3"]
        target["recovery_probes"] = [probe for probe in target["recovery_probes"]
                                     if probe["record_id"] != "r3"]
        self._assert_refused(payload, "without a recovery probe that must resolve")

    def test_recovery_probe_expecting_a_deleted_record_is_refused(self):
        def mutate(payload, task):
            task["recovery_probes"].append(
                {"probe_id": "q9", "record_id": "r1", "text": "sample brass",
                 "expect": "present"})

        self._assert_refused(self._variant(mutate), "deleted record")

    def test_recovery_probe_that_matches_nothing_is_refused(self):
        def mutate(payload, task):
            task["recovery_probes"].append(
                {"probe_id": "q9", "record_id": "r2", "text": "sample tungsten",
                 "expect": "present"})

        self._assert_refused(self._variant(mutate), "matches no entry")

    def test_duplicate_recovery_probe_id_is_refused(self):
        def mutate(payload, task):
            task["recovery_probes"].append(dict(task["recovery_probes"][0]))

        self._assert_refused(self._variant(mutate), "missing or duplicated")

    def test_non_synthetic_retry_scenario_is_refused(self):
        payload = copy.deepcopy(_payload("B4D"))
        payload["synthetic"] = False
        payload["data_class"] = "real"
        self._assert_refused(payload, "synthetic")

    # -- planted regressions, each on its intended gate ----------------------
    def _gates(self, name: str) -> tuple[dict, object]:
        mutant = run_block(self.taskset, solver_name="stub", mutations=(name,))
        return _gate_map(mutant.summary, "B4D", self.clean.summary), mutant

    def test_retry_from_scratch_is_caught_by_idempotency(self):
        gates, mutant = self._gates("retry_from_scratch")
        self.assertEqual(MUTATIONS["retry_from_scratch"].expected_gate,
                         "restore_idempotency_zero")
        self.assertEqual(gates["restore_idempotency_zero"].status, "fail")
        self.assertGreater(mutant.summary["restore_duplicate_effects"], 0)
        # Nothing was lost and nothing resurrected: the failure must be isolated.
        self.assertEqual(mutant.summary["restore_unrecovered"], 0)
        self.assertEqual(mutant.summary["restore_resurrections"], 0)

    def test_retry_without_filter_is_caught_by_deleted_record_filtering(self):
        gates, mutant = self._gates("retry_without_filter")
        self.assertEqual(MUTATIONS["retry_without_filter"].expected_gate,
                         "restore_resurrection_zero")
        self.assertEqual(gates["restore_resurrection_zero"].status, "fail")
        self.assertGreater(mutant.summary["restore_resurrections"], 0)
        self.assertEqual(mutant.summary["restore_duplicate_effects"], 0)

    def test_abandoned_partial_restore_claiming_success_is_caught(self):
        gates, mutant = self._gates("abandon_partial_restore")
        self.assertEqual(MUTATIONS["abandon_partial_restore"].expected_gate,
                         "restore_recovery_zero")
        self.assertEqual(gates["restore_recovery_zero"].status, "fail")
        self.assertGreater(mutant.summary["restore_unrecovered"], 0)
        self.assertEqual(mutant.summary["false_successes"], 4)

    def test_retry_mutations_leave_single_attempt_scenarios_alone(self):
        single = {task["id"] for task in self.taskset.tasks
                  if len(task.get("backup", {}).get("attempts") or []) <= 1}
        for name in ("retry_from_scratch", "retry_without_filter", "abandon_partial_restore"):
            mutant = run_block(self.taskset, solver_name="stub", mutations=(name,))
            for record in mutant.records:
                if record.task_id not in single:
                    continue
                self.assertEqual(record.metrics["restore_duplicate_effects"], 0, name)
                self.assertEqual(record.metrics["restore_unrecovered"], 0, name)
                self.assertTrue(record.success, f"{name} broke {record.task_id}")


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

    def test_the_restore_regressions_are_registered(self):
        expected = {"restore_without_filter": "restore_resurrection_zero",
                    "restore_drops_unrelated": "restore_survivor_zero",
                    "restore_skips_tombstones": "restore_residue_zero"}
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
                                     "resurrect_on_replay", "restore_without_filter",
                                     "restore_drops_unrelated", "restore_skips_tombstones",
                                     "retry_from_scratch", "retry_without_filter",
                                     "abandon_partial_restore"})
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
            fixture = _payload("B4D")["preregistration"]["sample_counts"]
            drills = sum(row["detail"]["drills_reported"] for row in records)
            days = sum(row["detail"]["replay_days_reported"] for row in records)
            self.assertEqual(drills, fixture["drill_executions"])
            self.assertEqual(days, fixture["replay_day_evaluations"])
            restores = sum(row["metrics"]["restores"] for row in records)
            self.assertEqual(restores, fixture["restore_events"])


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
        fixture = _payload("B4D")["preregistration"]["sample_counts"]
        for key in ("tasks", "drill_executions", "replay_day_evaluations", "probes",
                    "deleted_records", "surface_entries", "backups", "restore_events"):
            self.assertEqual(samples[key], fixture[key], key)
        self.assertEqual(samples["tasks"], 15)
        self.assertEqual(samples["drill_executions"], 45)
        self.assertEqual(samples["replay_day_evaluations"], 105)
        self.assertEqual(samples["backups"], 7)
        self.assertEqual(samples["restore_events"], 7)
        self.assertEqual(samples["restore_attempts"], 11)
        self.assertEqual(samples["retried_restore_scenarios"], 4)
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
        self.assertEqual(entry["slice"]["tasks"], 15)
        self.assertEqual(entry["slice"]["sample_counts"]["restore_attempts"], 11)
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
