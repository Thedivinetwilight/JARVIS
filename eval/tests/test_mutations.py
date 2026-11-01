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
        }
        for name, mutation in mutations.MUTATIONS.items():
            self.assertIn(mutation.expected_gate, known, name)
            self.assertTrue(mutation.phase_requirement.startswith("Phase"), name)
            self.assertTrue(mutation.description, name)

    def test_mutation_names_are_unique_and_sorted_stable(self):
        self.assertEqual(len(mutations.names()), len(set(mutations.names())))
        self.assertEqual(mutations.names(), tuple(mutations.MUTATIONS))


if __name__ == "__main__":
    unittest.main()
