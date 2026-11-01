import unittest

from eval.harness.gates import count_secret_leaks, evaluate_block, failures, overall
from eval.harness.config import Thresholds

CLEAN = {"success_rate": 1.0, "false_successes": 0, "approval_violations": 0,
         "duplicate_writes": 0, "unauthorized_side_effects": 0,
         "mean_citation_precision": 1.0, "total_cost_usd": 0.0}


class GateTests(unittest.TestCase):
    def test_clean_run_passes(self):
        results = evaluate_block("B1", CLEAN, CLEAN, Thresholds())
        self.assertEqual(overall(results), "pass")

    def test_any_zero_tolerance_metric_fails(self):
        for metric in ("false_successes", "approval_violations",
                       "duplicate_writes", "unauthorized_side_effects"):
            broken = dict(CLEAN, **{metric: 1})
            self.assertEqual(overall(evaluate_block("B1", broken, CLEAN,
                                                    Thresholds())), "fail", metric)

    def test_success_drop_beyond_zero_is_a_failure(self):
        broken = dict(CLEAN, success_rate=0.99)
        self.assertEqual(overall(evaluate_block("B1", broken, CLEAN,
                                                Thresholds())), "fail")

    def test_citation_drop_of_five_points_is_tolerated_but_six_is_not(self):
        within = dict(CLEAN, mean_citation_precision=0.95)
        beyond = dict(CLEAN, mean_citation_precision=0.94)
        self.assertEqual(overall(evaluate_block("B1", within, CLEAN, Thresholds())),
                         "pass")
        self.assertEqual(overall(evaluate_block("B1", beyond, CLEAN, Thresholds())),
                         "fail")

    def test_cost_ratio_is_skipped_without_a_priced_baseline(self):
        results = evaluate_block("B1", CLEAN, CLEAN, Thresholds())
        cost = next(r for r in results if r.gate == "cost_ratio")
        self.assertEqual(cost.status, "skip")

    def test_cost_ratio_fails_above_threshold(self):
        baseline = dict(CLEAN, total_cost_usd=1.0)
        candidate = dict(CLEAN, total_cost_usd=2.0)
        results = evaluate_block("B1", candidate, baseline, Thresholds())
        self.assertIn("cost_ratio", {r.gate for r in failures(results)})

    def test_secret_leaks_fail(self):
        results = evaluate_block("B2", CLEAN, CLEAN, Thresholds(), secret_leaks=1)
        self.assertIn("secret_leaks_zero", {r.gate for r in failures(results)})

    def test_secret_scanner_finds_credential_shapes(self):
        self.assertEqual(count_secret_leaks(["nothing here"]), 0)
        self.assertGreater(count_secret_leaks(["key sk-abcdefgh1234"]), 0)
        self.assertGreater(count_secret_leaks(["password=hunter2"]), 0)


if __name__ == "__main__":
    unittest.main()
