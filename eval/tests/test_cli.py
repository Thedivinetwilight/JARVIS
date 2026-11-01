import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "eval.harness", *args],
                          capture_output=True, text=True, cwd=REPO, check=False)


class CliTests(unittest.TestCase):
    def test_selftest_passes(self):
        proc = cli("selftest")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("16/16 checks passed", proc.stdout)

    def test_x10_detects_every_planted_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = cli("x10", "--results", tmp, "--report",
                       str(Path(tmp, "report.json")),
                       "--baseline", str(Path(tmp, "missing.json")))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("X-10 PASSED", proc.stdout)
            report = json.loads(Path(tmp, "report.json").read_text())
            self.assertTrue(all(row["detected"] for row in report["rows"]))
            self.assertEqual(len(report["rows"]), 9)
            for row in report["rows"]:
                # Detection must come from the gate that protects the intended
                # invariant, not from an unrelated failure.
                self.assertIn(row["expected_gate"], row["gates_failed"])

    def test_gates_reject_a_known_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = cli("run", "--block", "B2",
                      "--mutations", "retry_without_idempotency",
                      "--results", tmp)
            self.assertEqual(run.returncode, 0, run.stderr)
            path = cli("find", "--block", "B2", "--mutated",
                       "retry_without_idempotency", "--results", tmp).stdout.strip()
            gates = cli("gates", "--run", path,
                        "--baseline", str(Path(tmp, "none.json")))
            self.assertEqual(gates.returncode, 1, gates.stdout)
            self.assertIn("duplicate_writes_zero", gates.stdout)

    def test_scorecard_is_written_with_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli("run", "--block", "B1", "--results", tmp)
            path = cli("find", "--block", "B1", "--clean", "--results", tmp
                       ).stdout.strip()
            out = str(Path(tmp, "scorecard.md"))
            proc = cli("scorecard", "--run", path, "--out", out,
                       "--baseline", str(Path(tmp, "none.json")))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            text = Path(out).read_text()
            for needle in ("Synthetic fixtures only", "Harness version",
                           "Ledger digest", "Gate verdict"):
                self.assertIn(needle, text)
            self.assertTrue(Path(tmp, "scorecard.json").is_file())

    def test_unknown_mutation_is_rejected(self):
        proc = cli("run", "--block", "B1", "--mutations", "nonsense",
                   "--results", tempfile.mkdtemp())
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown mutations", proc.stderr)


if __name__ == "__main__":
    unittest.main()
