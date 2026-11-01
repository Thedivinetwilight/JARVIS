"""Harness self-checks.

These prove properties of the *harness*, not of any solver: that runs are
deterministic and reproducible across processes, that a mutation is caught, that
each gate actually fails when its invariant is broken, and that the harness
cannot reach the network.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from . import gates as gates_mod
from .config import Thresholds, git_dirty, git_revision
from .ledger import read_run, write_run
from .registry import (evaluator_digest, load_registry, registry_sha,
                       validate_registry)
from .runner import run_block
from .solvers.ladder import LADDER_ORDER
from .taskset import TaskSetError, load_taskset, load_tasksets
from .solvers.mutations import MUTATIONS
from .tools import Executor, World

FORBIDDEN_IMPORTS = ("urllib", "requests", "httpx", "socket", "http.client",
                     "openai", "anthropic", "inspect_ai")

MUTATION_BLOCKS = {name: mutation.block for name, mutation in MUTATIONS.items()}


class Checker:
    def __init__(self, verbose: bool = True) -> None:
        self.verbose = verbose
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn: Callable[[], str]) -> None:
        try:
            detail = fn()
            self.results.append((name, True, detail))
            if self.verbose:
                print(f"PASS {name}: {detail}")
        except Exception as exc:  # noqa: BLE001 - a failing check is a reported result
            self.results.append((name, False, f"{type(exc).__name__}: {exc}"))
            if self.verbose:
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")

    @property
    def ok(self) -> bool:
        return all(passed for _, passed, _ in self.results)


def check_taskset_sets_b9() -> Any:
    return load_tasksets(Path("eval/tasks"))["B9"]


def _hashes(outcome: Any) -> list[str]:
    return [record.outcome_hash for record in outcome.records]


def run_selftest(verbose: bool = True) -> int:
    checker = Checker(verbose)
    sha, dirty = git_revision(), git_dirty()

    def check_tasksets() -> str:
        sets = load_tasksets(Path("eval/tasks"))
        assert set(sets) == {"B1", "B2", "B3", "B8", "B9", "B10", "B4", "B4D", "B16",
                             "B4W"}, \
            f"unexpected blocks {sorted(sets)}"
        for block, ts in sets.items():
            assert ts.tasks, f"{block} has no tasks"
        return f"{len(sets)} blocks: " + ", ".join(
            f"{b}={len(ts)} tasks sha {ts.sha[:8]}" for b, ts in sorted(sets.items()))

    check_tasksets_sets: dict[str, Any] = {}

    def check_load() -> str:
        check_tasksets_sets.update(load_tasksets(Path("eval/tasks")))
        return check_tasksets()

    checker.check("task_sets_load", check_load)

    def check_deterministic() -> str:
        detail = []
        for block in ("B1", "B2", "B3", "B8", "B9", "B10", "B4", "B4D", "B16", "B4W"):
            first = run_block(check_tasksets_sets[block], git_sha=sha, git_dirty=dirty)
            second = run_block(check_tasksets_sets[block], git_sha=sha, git_dirty=dirty)
            assert _hashes(first) == _hashes(second), f"{block} hashes differ between runs"
            assert first.run_id == second.run_id, f"{block} run ids differ"
            detail.append(f"{block}={len(first.records)} records")
        return "identical outcome hashes on rerun (" + ", ".join(detail) + ")"

    checker.check("deterministic_rerun", check_deterministic)

    def check_seed_invariance() -> str:
        def outcomes(outcome: Any) -> list[tuple[Any, ...]]:
            return [(r.task_id, r.repeat, r.status, r.success,
                     tuple(sorted(r.metrics.items()))) for r in outcome.records]

        a = run_block(check_tasksets_sets["B2"], seed=1729, git_sha=sha, git_dirty=dirty)
        b = run_block(check_tasksets_sets["B2"], seed=7, git_sha=sha, git_dirty=dirty)
        assert outcomes(a) == outcomes(b), "stub unexpectedly depends on the seed"
        assert _hashes(a) == _hashes(b), "outcome hash must not depend on the seed"
        assert a.run_id != b.run_id, "seed must still change the run identity"
        return ("same observed outcomes and same outcome hashes under seeds 1729 "
                "and 7; only run identity differs")

    checker.check("seed_invariance", check_seed_invariance)

    def check_cross_process() -> str:
        with tempfile.TemporaryDirectory() as tmp:
            digests = []
            for name in ("a", "b"):
                out = Path(tmp) / name
                proc = subprocess.run(
                    [sys.executable, "-m", "eval.harness", "run", "--block", "B2",
                     "--results", str(out)],
                    capture_output=True, text=True, check=False)
                assert proc.returncode == 0, f"cli failed: {proc.stderr.strip()}"
                run = read_run(next(out.glob("*.jsonl")))
                digests.append([r["outcome_hash"] for r in run["records"]])
            assert digests[0] == digests[1], "two processes produced different hashes"
            return f"{len(digests[0])} records reproduced identically in two processes"

    checker.check("cross_process_reproducibility", check_cross_process)

    def check_mutations() -> str:
        caught = []
        for name, block in MUTATION_BLOCKS.items():
            clean = run_block(check_tasksets_sets[block], git_sha=sha, git_dirty=dirty)
            mutant = run_block(check_tasksets_sets[block], mutations=(name,),
                               git_sha=sha, git_dirty=dirty)
            results = gates_mod.evaluate_block(block, mutant.summary,
                                               clean.summary, Thresholds())
            failed = {result.gate for result in gates_mod.failures(results)}
            expected = MUTATIONS[name].expected_gate
            assert expected in failed, \
                f"{name} was not caught by {expected}; gates that fired: {sorted(failed)}"
            caught.append(f"{name}->{expected}")
        return (f"all {len(MUTATION_BLOCKS)} regressions caught by their expected gate: "
                + ", ".join(caught))

    checker.check("mutation_detection", check_mutations)


    def check_family_gate_isolation() -> str:
        clean = run_block(check_tasksets_sets["B4"], git_sha=sha, git_dirty=dirty)
        b16_gates = {name for _, name in
                     gates_mod.BLOCK_METRIC_GATES.get("B16", ())} | \
                    {name for _, name, _, _ in gates_mod.BLOCK_RATE_GATES.get("B16", ())}
        leaked = [result.gate for result in gates_mod.evaluate_block(
            "B4", clean.summary, clean.summary, Thresholds()) if result.gate in b16_gates]
        assert not leaked, f"B16 gates fired on a B4 run: {leaked}"
        b4 = [result for result in gates_mod.evaluate_block(
            "B4", clean.summary, clean.summary, Thresholds())]
        assert any(result.gate == "metadata_completeness_zero" for result in b4), \
            "B4 run did not evaluate its own metadata gate"
        durability = run_block(check_tasksets_sets["B4W"], git_sha=sha, git_dirty=dirty)
        foreign = {name for block in ("B4", "B4D", "B16")
                   for _, name in gates_mod.BLOCK_METRIC_GATES.get(block, ())}
        on_b4w = [result for result in gates_mod.evaluate_block(
            "B4W", durability.summary, durability.summary, Thresholds())]
        durability_gates = {
            "wal_integrity_zero", "durability_zero", "uncommitted_write_zero",
            "durability_idempotency_zero", "durability_resurrection_zero",
            "unrelated_record_zero", "recovery_complete_zero"}
        fired = {result.gate for result in on_b4w}
        assert durability_gates <= fired, \
            f"B4W gates missing from a B4W run: {sorted(durability_gates - fired)}"
        assert all(result.status != "fail" for result in on_b4w
                   if result.gate in durability_gates), \
            f"a clean B4W run failed its own gate: " \
            f"{[r.gate for r in on_b4w if r.gate in durability_gates and r.status == 'fail']}"
        assert not [result.gate for result in on_b4w if result.gate in foreign], \
            "a B4/B4D/B16 gate fired on a B4W run"
        b4d = run_block(check_tasksets_sets["B4D"], git_sha=sha, git_dirty=dirty)
        back = [result.gate for result in gates_mod.evaluate_block(
            "B4D", b4d.summary, b4d.summary, Thresholds())
            if result.gate in durability_gates]
        assert not back, f"B4W gates fired on a B4D run: {back}"
        return (f"{len(b4)} B4 gates evaluated; no B16 gate on a B4 run; "
                f"the {len(durability_gates)} durability gates pass on a B4W run and "
                "stay out of B4D")

    checker.check("family_gate_isolation", check_family_gate_isolation)

    def check_registry() -> str:
        registry = load_registry()
        problems = validate_registry(registry, list(check_tasksets_sets.values()))
        assert not problems, f"registry problems: {problems[:5]}"
        return (f"registry v{registry.version} sha {registry.sha[:8]}: "
                f"{len(registry)} capabilities, all covered by the fixtures")

    checker.check("capability_registry", check_registry)

    def check_evaluator_isolation() -> str:
        before = evaluator_digest(".")
        before_sha = registry_sha()
        for block in ("B1", "B8", "B9", "B10", "B4", "B4D", "B16", "B4W"):
            run_block(check_tasksets_sets[block], git_sha=sha, git_dirty=dirty)
        run_block(check_taskset_sets_b9(), mutations=("disable_skill_reuse",),
                  git_sha=sha, git_dirty=dirty)
        run_block(check_tasksets_sets["B4"], mutations=("serve_expired_fact",),
                  git_sha=sha, git_dirty=dirty)
        assert evaluator_digest(".") == before, "a run modified evaluator-owned files"
        assert registry_sha() == before_sha, "a run modified the capability registry"
        return ("evaluator-owned files (registry, tasks, baselines) unchanged by "
                "full-family runs, including a mutated one")

    checker.check("evaluator_isolation", check_evaluator_isolation)

    def check_ladder_separates() -> str:
        results = {}
        for name in LADDER_ORDER:
            results[name] = {
                block: run_block(check_tasksets_sets[block], solver_name=name,
                                 git_sha=sha, git_dirty=dirty).summary["success_rate"]
                for block in ("B1", "B8")}
        spread = 100 * (results["stub"]["B8"] - results["stub_naive"]["B8"])
        assert spread >= 10, f"ladder spread only {spread:.1f} pp on B8"
        assert results["oracle_ceiling"]["B8"] >= results["stub"]["B8"], \
            "the ceiling probe must not score below the reference solver"
        return (f"reference vs naive on B8 = {spread:+.1f} pp; oracle ceiling "
                f"{results['oracle_ceiling']['B8']:.2f} >= reference "
                f"{results['stub']['B8']:.2f}")

    checker.check("ladder_separates", check_ladder_separates)

    def check_family_gate_masks() -> str:
        baseline = {"blocks": {
            "B1": {"summary": {"success_rate": 0.5, "attempts": 100, "successes": 50}},
            "B8": {"summary": {"success_rate": 1.0, "attempts": 10, "successes": 10}}}}
        candidate = {
            "B1": {"success_rate": 1.0, "attempts": 100, "successes": 100},
            "B8": {"success_rate": 0.2, "attempts": 10, "successes": 2}}
        result = gates_mod.family_regression(candidate, baseline, Thresholds())
        assert result.status == "fail", "family regression was not detected"
        assert "masked" in result.detail, f"aggregate masking not reported: {result.detail}"
        return f"a collapsing family is caught even when the aggregate improves ({result.detail})"

    checker.check("family_regression_masking", check_family_gate_masks)

    def check_gate_negative_controls() -> str:
        clean = run_block(check_tasksets_sets["B1"], git_sha=sha, git_dirty=dirty)
        baseline_summary = clean.summary
        broken = dict(baseline_summary)
        broken["false_successes"] = 1
        broken["duplicate_writes"] = 2
        broken["unauthorized_side_effects"] = 1
        broken["approval_violations"] = 3
        broken["success_rate"] = baseline_summary["success_rate"] - 0.5
        broken["mean_citation_precision"] = 0.5
        results = gates_mod.evaluate_block("B1", broken, baseline_summary, Thresholds(),
                                           secret_leaks=2)
        failed = {r.gate for r in gates_mod.failures(results)}
        expected = {"false_successes_zero", "duplicate_writes_zero",
                    "unauthorized_side_effects_zero", "approval_violations_zero",
                    "success_drop", "citation_precision_drop", "secret_leaks_zero"}
        assert expected <= failed, f"gates that did not fire: {sorted(expected - failed)}"
        return f"all {len(expected)} gates fire on planted breakage"

    checker.check("gate_negative_controls", check_gate_negative_controls)

    def check_cost_gate_skip() -> str:
        clean = run_block(check_tasksets_sets["B1"], git_sha=sha, git_dirty=dirty)
        results = gates_mod.evaluate_block("B1", clean.summary, clean.summary,
                                           Thresholds())
        cost = next(r for r in results if r.gate == "cost_ratio")
        assert cost.status == "skip", f"cost gate should skip, got {cost.status}"
        return "cost_ratio skips explicitly when baseline cost is zero"

    checker.check("cost_gate_skip", check_cost_gate_skip)

    def check_ledger_roundtrip() -> str:
        outcome = run_block(check_tasksets_sets["B3"], git_sha=sha, git_dirty=dirty)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_run(outcome, tmp)
            run = read_run(path)
            assert run["meta"]["run_id"] == outcome.run_id
            assert [r["outcome_hash"] for r in run["records"]] == _hashes(outcome)
            return f"{len(run['records'])} records round-tripped through {path.name}"

    checker.check("ledger_roundtrip", check_ledger_roundtrip)

    def check_unverified_write_rejected() -> str:
        payload = {
            "version": "test", "block": "B3", "synthetic": True,
            "data_class": "synthetic",
            "tasks": [{"id": "t1", "goal": "g", "budget": 3,
                       "plan": [{"id": "s1", "tool": "files.write",
                                 "args": {"path": "a.txt", "content": "x"}}],
                       "verify": {"files": []}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "tasks.json").write_text(json.dumps(payload), encoding="utf-8")
            try:
                load_taskset(tmp)
            except TaskSetError as exc:
                return f"rejected as designed: {exc}"
        raise AssertionError("unverified write was accepted")

    checker.check("unverified_write_rejected", check_unverified_write_rejected)

    def check_synthetic_only() -> str:
        payload = {
            "version": "test", "block": "B2", "synthetic": False,
            "data_class": "real", "tasks": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "tasks.json").write_text(json.dumps(payload), encoding="utf-8")
            try:
                load_taskset(tmp)
            except TaskSetError as exc:
                return f"non-synthetic task set refused: {exc}"
        raise AssertionError("non-synthetic task set was accepted")

    checker.check("synthetic_only", check_synthetic_only)

    def check_redaction() -> str:
        world = World()
        executor = Executor(world)
        executor.call("files.write", {"path": "notes/leak.txt",
                                      "content": "token=abcdef123456"})
        text = json.dumps(executor.audit)
        assert "abcdef123456" not in text, "secret survived redaction"
        assert "[REDACTED]" in text, "redaction marker missing"
        return "credential-shaped content redacted in the audit trail"

    checker.check("secret_redaction", check_redaction)

    def check_no_network() -> str:
        offenders = []
        root = Path("eval/harness")
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_IMPORTS:
                for line in source.splitlines():
                    stripped = line.strip()
                    if stripped.startswith(("import ", "from ")) and \
                            token in stripped:
                        offenders.append(f"{path.relative_to(root)}: {stripped}")
        assert not offenders, f"network-capable imports found: {offenders}"
        return "no network or model-client imports anywhere in eval/harness"

    checker.check("no_network_imports", check_no_network)

    total = len(checker.results)
    passed = sum(1 for _, ok, _ in checker.results if ok)
    print(f"\n{passed}/{total} checks passed")
    return 0 if checker.ok else 1
