import unittest
from pathlib import Path

from eval.harness.ledger import read_run, task_hashes, write_run
from eval.harness.runner import run_block
from eval.harness.taskset import load_tasksets


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sets = load_tasksets(Path("eval/tasks"))

    def test_record_count_matches_tasks_times_repeats(self):
        outcome = run_block(self.sets["B2"], repeats=2, git_sha="test")
        self.assertEqual(len(outcome.records), 2 * len(self.sets["B2"].tasks))
        self.assertEqual(outcome.summary["attempts"], 2 * len(self.sets["B2"].tasks))

    def test_pass_hat_k_is_reported_for_repeated_blocks(self):
        outcome = run_block(self.sets["B2"], repeats=4, git_sha="test")
        self.assertEqual(outcome.summary["pass_k"], 4)
        self.assertEqual(outcome.summary["pass_hat_k"], 1.0)
        single = run_block(self.sets["B1"], repeats=1, git_sha="test")
        self.assertIsNone(single.summary["pass_hat_k"])

    def test_rerun_is_byte_identical_outside_latency(self):
        first = run_block(self.sets["B3"], git_sha="test")
        second = run_block(self.sets["B3"], git_sha="test")
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual([r.outcome_hash for r in first.records],
                         [r.outcome_hash for r in second.records])

    def test_latency_is_excluded_from_the_hash(self):
        outcome = run_block(self.sets["B1"], git_sha="test")
        record = outcome.records[0]
        before = record.outcome_hash
        record.latency_ms += 123.0
        self.assertEqual(record.finalize().outcome_hash, before)

    def test_mutation_changes_run_identity(self):
        clean = run_block(self.sets["B2"], git_sha="test")
        mutant = run_block(self.sets["B2"], mutations=("drop_timezone",),
                          git_sha="test")
        self.assertNotEqual(clean.run_id, mutant.run_id)
        self.assertLess(mutant.summary["success_rate"],
                        clean.summary["success_rate"])

    def test_git_revision_is_recorded_and_hashed(self):
        a = run_block(self.sets["B1"], git_sha="aaaa", git_dirty=False)
        b = run_block(self.sets["B1"], git_sha="aaaa", git_dirty=True)
        self.assertEqual(a.records[0].outcome_hash, b.records[0].outcome_hash)
        self.assertNotEqual(a.run_id, b.run_id)

    def test_ledger_file_contains_meta_and_records(self):
        import tempfile

        outcome = run_block(self.sets["B1"], git_sha="test")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_run(outcome, tmp)
            run = read_run(path)
            self.assertEqual(run["meta"]["block"], "B1")
            self.assertEqual(len(run["records"]), len(outcome.records))
            hashes = task_hashes(run["records"])
            self.assertEqual(set(hashes), {t["id"] for t in self.sets["B1"].tasks})
            index = Path(tmp, "index.json")
            self.assertTrue(index.is_file())

    def test_records_never_carry_credentials(self):
        outcome = run_block(self.sets["B2"], git_sha="test")
        text = " ".join(r.to_json().__str__() for r in outcome.records)
        for token in ("sk-", "password=", "api_key"):
            self.assertNotIn(token, text)

    def test_all_blocks_run_without_error(self):
        for block in ("B1", "B2", "B3"):
            outcome = run_block(self.sets[block], repeats=1, git_sha="test")
            self.assertEqual(len(outcome.records), len(self.sets[block].tasks))
            self.assertTrue(all(r.error is None for r in outcome.records),
                            [r.error for r in outcome.records if r.error])


if __name__ == "__main__":
    unittest.main()
