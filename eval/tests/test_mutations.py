import unittest
from pathlib import Path

from eval.harness.solvers import mutations
from eval.harness.taskset import load_tasksets


class MutationRegistryTests(unittest.TestCase):
    def test_every_mutation_maps_to_a_real_block(self):
        sets = load_tasksets(Path("eval/tasks"))
        for name, mutation in mutations.MUTATIONS.items():
            self.assertIn(mutation.block, sets, name)

    def test_every_mutation_has_an_expected_gate_and_rationale(self):
        known = {
            "false_successes_zero", "approval_violations_zero", "duplicate_writes_zero",
            "unauthorized_side_effects_zero", "secret_leaks_zero", "success_drop",
            "citation_precision_drop", "cost_ratio",
            # M5 slices (B4 memory metadata/expiry, B4D deletion drills, B16 explanation)
            "metadata_completeness_zero", "stale_assertion_zero", "stale_label_zero",
            "silent_failure_zero", "cause_attribution_zero",
            "deletion_residue_zero", "post_delete_hit_zero", "resurrection_zero",
            "restore_resurrection_zero", "restore_survivor_zero", "restore_residue_zero",
            "restore_recovery_zero", "restore_idempotency_zero", "restore_attempt_count_zero",
            "restore_corruption_zero", "restore_partial_write_zero", "restore_rollback_zero",
            # M5 durability slice (B4W: synthetic write-ahead log, evaluator-chosen crash bytes)
            "wal_integrity_zero", "durability_zero", "uncommitted_write_zero",
            "durability_idempotency_zero", "durability_resurrection_zero",
            "unrelated_record_zero", "recovery_complete_zero",
        }
        for name, mutation in mutations.MUTATIONS.items():
            self.assertIn(mutation.expected_gate, known, name)
            self.assertTrue(
                mutation.phase_requirement.startswith(("Phase", "Product spec C-")),
                f"{name}: requirement must cite a phase or a product-spec criterion")
            self.assertTrue(mutation.description, name)

    def test_mutation_names_are_unique_and_sorted_stable(self):
        self.assertEqual(len(mutations.names()), len(set(mutations.names())))
        self.assertEqual(mutations.names(), tuple(mutations.MUTATIONS))


if __name__ == "__main__":
    unittest.main()
