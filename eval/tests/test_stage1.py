"""Stage 1 tests: B8-B10 fixtures, the capability registry, the ladder, the gate."""
import json
import tempfile
import unittest
from pathlib import Path

from eval.harness import gates as gates_mod
from eval.harness.config import Thresholds
from eval.harness.registry import (RegistryError, evaluator_digest, load_registry,
                                   registry_sha, tools_used_by_fixtures,
                                   validate_registry)
from eval.harness.runner import run_block
from eval.harness.solvers import build_solver
from eval.harness.solvers.ladder import LADDER_ORDER, OracleSolver
from eval.harness.solvers.mutations import MUTATIONS
from eval.harness.taskset import TaskSetError, load_taskset, load_tasksets

REPO = Path(__file__).resolve().parents[2]


def _write(tmp: str, payload: dict) -> str:
    Path(tmp, "tasks.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp


def _b8_extraction(**overrides) -> dict:
    task = {
        "id": "e1", "subfamily": "extraction", "instruction": "extract",
        "documents": [{"doc_id": "d1", "spans": [
            {"span_id": "s1", "text": "Invoice INV-2031 total 12.00"}]}],
        "expected": {"fields": {"invoice_id": "INV-2031"},
                     "evidence_span_ids": ["s1"]},
    }
    task.update(overrides)
    return task


def _builtin_sets():
    return load_tasksets(Path("eval/tasks"))


class RegistryTests(unittest.TestCase):
    def test_registry_is_valid_and_covers_the_fixtures(self):
        registry = load_registry()
        self.assertEqual(registry.version, "1.0.0")
        self.assertEqual(validate_registry(registry, list(_builtin_sets().values())), [])

    def test_registry_requires_synthetic_and_evaluator_owned(self):
        payload = json.loads(Path("eval/registry/capabilities.json").read_text())
        payload["evaluator_owned"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "caps.json")
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RegistryError):
                load_registry(path)

    def test_forbidden_capability_authority_is_rejected(self):
        registry = load_registry()
        broken = dict(registry.capabilities)
        first = next(iter(broken))
        cap = broken[first]
        broken[first] = type(cap)(**{**cap.__dict__, "capability_id": "gate.promote"})
        forged = type(registry)(path=registry.path, version=registry.version,
                                sha=registry.sha, capabilities=broken)
        problems = validate_registry(forged)
        self.assertTrue(any("evaluator or policy authority" in p for p in problems))

    def test_unregistered_tool_used_by_a_fixture_is_reported(self):
        registry = load_registry()
        sets = _builtin_sets()
        broken = dict(registry.capabilities)
        broken.pop("schedule.assign")
        forged = type(registry)(path=registry.path, version=registry.version,
                                sha=registry.sha, capabilities=broken)
        problems = validate_registry(forged, list(sets.values()))
        self.assertTrue(any("schedule.assign" in p for p in problems))

    def test_fixture_tool_discovery_covers_the_new_families(self):
        used = tools_used_by_fixtures(list(_builtin_sets().values()))
        self.assertLessEqual(
            {"document.extract_fields", "source.reconcile", "schedule.assign",
             "skill.apply", "memory.consolidate", "state.verify",
             "files.write", "calendar.create_event", "corpus.retrieve"}, used)


class NewFixtureValidationTests(unittest.TestCase):
    def test_b8_extraction_without_a_citation_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = _b8_extraction(expected={"fields": {"invoice_id": "INV-2031"},
                                            "evidence_span_ids": []})
            payload = {"version": "t", "block": "B8", "synthetic": True,
                       "data_class": "synthetic", "tasks": [task]}
            with self.assertRaises(TaskSetError):
                load_taskset(_write(tmp, payload))

    def test_b9_without_acceptance_checks_is_refused(self):
        task = {"id": "t1", "instruction": "write the bundle",
                "skill": {"name": "s", "steps": [
                    {"id": "s1", "tool": "files.write",
                     "args": {"path": "bundle/index.txt", "content": "x"}}]},
                "fallback": {"tool": "files.write",
                             "args": {"path": "bundle/index.txt", "content": "x"}},
                "verify": {"files": []}}
        payload = {"version": "t", "block": "B9", "synthetic": True,
                   "data_class": "synthetic", "tasks": [task]}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TaskSetError):
                load_taskset(_write(tmp, payload))

    def test_b10_rejects_a_fact_in_two_outcome_sets(self):
        task = {"id": "t1", "now": "2026-11-01T00:00:00Z", "question": "q",
                "seed_memory": [{"fact_id": "f1", "claim": "c", "trust": "verified",
                                 "valid_from": "2026-01-01"}],
                "expected": {"answer_keywords": ["c"], "surviving": ["f1"],
                             "superseded": ["f1"], "expired": [], "forbidden": []}}
        payload = {"version": "t", "block": "B10", "synthetic": True,
                   "data_class": "synthetic", "tasks": [task]}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TaskSetError):
                load_taskset(_write(tmp, payload))

    def test_b10_rejects_an_unknown_trust_label(self):
        task = {"id": "t1", "now": "2026-11-01T00:00:00Z", "question": "q",
                "seed_memory": [{"fact_id": "f1", "claim": "c", "trust": "probably",
                                 "valid_from": "2026-01-01"}],
                "expected": {"answer_keywords": ["c"], "surviving": ["f1"],
                             "superseded": [], "expired": [], "forbidden": []}}
        payload = {"version": "t", "block": "B10", "synthetic": True,
                   "data_class": "synthetic", "tasks": [task]}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TaskSetError):
                load_taskset(_write(tmp, payload))


class LadderTests(unittest.TestCase):
    def test_ladder_rungs_build_and_oracle_is_a_probe(self):
        for name in LADDER_ORDER:
            self.assertTrue(build_solver(name).solve is not None)
        self.assertIsInstance(build_solver("oracle_ceiling"), OracleSolver)

    def test_reference_separates_from_naive_on_b8(self):
        sets = _builtin_sets()
        reference = run_block(sets["B8"], solver_name="stub", git_sha="test")
        naive = run_block(sets["B8"], solver_name="stub_naive", git_sha="test")
        spread = 100 * (reference.summary["success_rate"]
                        - naive.summary["success_rate"])
        self.assertGreaterEqual(spread, 10.0)

    def test_no_verify_fails_extraction_but_not_state_tasks(self):
        sets = _builtin_sets()
        outcome = run_block(sets["B8"], solver_name="stub_no_verify", git_sha="test")
        subs = outcome.summary["by_subfamily"]
        self.assertEqual(subs["extraction"]["successes"], 0)
        self.assertEqual(subs["planning"]["successes"], subs["planning"]["attempts"])

    def test_new_mutations_target_the_new_families(self):
        self.assertEqual(MUTATIONS["skip_conflict_detection"].block, "B8")
        self.assertEqual(MUTATIONS["disable_skill_reuse"].block, "B9")
        self.assertEqual(MUTATIONS["accept_poisoned_memory"].block, "B10")
        self.assertEqual(MUTATIONS["accept_poisoned_memory"].expected_gate,
                         "false_successes_zero")


class FamilyGateTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {"blocks": {
            "B1": {"summary": {"success_rate": 1.0, "attempts": 10, "successes": 10}},
            "B8": {"summary": {"success_rate": 1.0, "attempts": 10, "successes": 10}}}}

    def test_clean_candidate_passes(self):
        candidate = {"B1": {"success_rate": 1.0, "attempts": 10, "successes": 10},
                     "B8": {"success_rate": 1.0, "attempts": 10, "successes": 10}}
        result = gates_mod.family_regression(candidate, self.baseline, Thresholds())
        self.assertEqual(result.status, "pass")

    def test_a_single_collapsing_family_fails(self):
        candidate = {"B1": {"success_rate": 1.0, "attempts": 10, "successes": 10},
                     "B8": {"success_rate": 0.3, "attempts": 10, "successes": 3}}
        result = gates_mod.family_regression(candidate, self.baseline, Thresholds())
        self.assertEqual(result.status, "fail")
        self.assertIn("B8", result.detail)

    def test_gain_in_one_family_cannot_mask_loss_in_another(self):
        baseline = {"blocks": {
            "B1": {"summary": {"success_rate": 0.5, "attempts": 100, "successes": 50}},
            "B8": {"summary": {"success_rate": 1.0, "attempts": 10, "successes": 10}}}}
        candidate = {"B1": {"success_rate": 1.0, "attempts": 100, "successes": 100},
                     "B8": {"success_rate": 0.2, "attempts": 10, "successes": 2}}
        result = gates_mod.family_regression(candidate, baseline, Thresholds())
        self.assertEqual(result.status, "fail")
        self.assertIn("masked", result.detail)

    def test_skips_without_a_shared_baseline(self):
        result = gates_mod.family_regression({"B1": {"success_rate": 1.0}},
                                             self.baseline, Thresholds())
        self.assertEqual(result.status, "skip")


class EvaluatorBoundaryTests(unittest.TestCase):
    def test_registry_and_fixtures_are_unchanged_by_a_run(self):
        before, before_sha = evaluator_digest(REPO), registry_sha()
        sets = _builtin_sets()
        for block in ("B8", "B9", "B10"):
            run_block(sets[block], solver_name="stub", git_sha="test")
        self.assertEqual(evaluator_digest(REPO), before)
        self.assertEqual(registry_sha(), before_sha)


if __name__ == "__main__":
    unittest.main()
