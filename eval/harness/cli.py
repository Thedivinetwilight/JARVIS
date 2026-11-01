"""Command line entry point: `python -m eval.harness <command>`.

Commands are deliberately small and explicit so every reported number maps to a
ledger file on disk.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import gates as gates_mod
from .config import (DEFAULT_REPEATS, HARNESS_VERSION, Thresholds, git_dirty,
                     git_revision, git_untracked)
from .ledger import (freeze_baseline, ledger_digest, load_baseline, read_run,
                     write_run)
from .runner import run_block
from .scorecard import build as build_scorecard
from .scorecard import to_json as scorecard_json
from .solvers.ladder import LADDER_ORDER
from .solvers.mutations import MUTATIONS, blocks as mutation_blocks
from .taskset import load_tasksets

BLOCKS = ("B1", "B2", "B3", "B8", "B9", "B10")
LADDER = LADDER_ORDER

MUTATION_BLOCKS = mutation_blocks()


def _outcomes(root: Path, blocks: tuple[str, ...], solver: str,
              mutations: tuple[str, ...], repeats: int | None,
              seed: int) -> dict[str, Any]:
    sets = load_tasksets(Path("eval/tasks"))
    sha, dirty, untracked = git_revision(), git_dirty(), git_untracked()
    outcomes = {}
    for block in blocks:
        outcomes[block] = run_block(
            sets[block], solver_name=solver, mutations=mutations, seed=seed,
            repeats=repeats if repeats is not None else DEFAULT_REPEATS.get(block, 1),
            git_sha=sha, git_dirty=dirty, git_untracked=untracked)
    return outcomes


def _load_runs(paths: list[str]) -> tuple[dict[str, dict[str, Any]], list[str], str]:
    runs: dict[str, dict[str, Any]] = {}
    texts: list[str] = []
    for path in paths:
        run = read_run(path)
        payload = dict(run["meta"] | {"records": run["records"]})
        if payload["block"] in runs:
            raise SystemExit(f"two runs for block {payload['block']}; combine only "
                             "mutually exclusive runs")
        runs[payload["block"]] = payload
        texts.append(Path(path).read_text(encoding="utf-8"))
    digest = ledger_digest(paths)
    return runs, texts, digest


def cmd_list(args: argparse.Namespace) -> int:
    sets = load_tasksets(Path("eval/tasks"))
    print(f"harness {HARNESS_VERSION}, tasks root eval/tasks")
    for block, ts in sorted(sets.items()):
        print(f"  {block}: {len(ts)} tasks, version {ts.version}, "
              f"sha256 {ts.sha[:16]}, repeats {DEFAULT_REPEATS.get(block)}")
    print("mutations (each must be caught by its expected gate):")
    for name, mutation in MUTATIONS.items():
        print(f"  {name:26s} {mutation.block}  -> {mutation.expected_gate:32s} "
              f"{mutation.description}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    mutations = tuple(m for m in (args.mutations or "").split(",") if m)
    unknown = set(mutations) - set(MUTATIONS)
    if unknown:
        return _fail(f"unknown mutations: {sorted(unknown)}")
    blocks = tuple(args.block) if args.block else BLOCKS
    outcomes = _outcomes(Path(args.results), blocks, args.solver, mutations,
                         args.repeats, args.seed)
    for block, outcome in outcomes.items():
        path = write_run(outcome, args.results)
        summary = outcome.summary
        print(f"{block} run {outcome.run_id}: "
              f"{summary['successes']}/{summary['attempts']} successful, "
              f"false_success={summary['false_successes']} -> {path}")
    return 0


def cmd_baseline_freeze(args: argparse.Namespace) -> int:
    blocks = tuple(args.block) if args.block else BLOCKS
    outcomes = _outcomes(Path(args.results), blocks, "stub", (), None, args.seed)
    paths = [write_run(outcome, args.results) for outcome in outcomes.values()]
    path = freeze_baseline(outcomes, root=args.baselines, name=args.name, note=args.note)
    print(f"froze baseline {path}")
    for item in paths:
        print(f"  evidence {item}")
    return 0


def cmd_gates(args: argparse.Namespace) -> int:
    runs, texts, _ = _load_runs(args.run)
    baseline = None
    if args.baseline and Path(args.baseline).is_file():
        baseline = load_baseline(args.baseline)
    leaks = gates_mod.count_secret_leaks(texts)
    summaries = {block: run["summary"] for block, run in runs.items()}
    results = gates_mod.evaluate(summaries, baseline, Thresholds(), secret_leaks=leaks)
    for result in results:
        print(f"{result.status:4s} {result.block} {result.gate:26s} "
              f"actual={result.actual} limit={result.limit} ({result.detail})")
    verdict = gates_mod.overall(results)
    print(f"verdict: {verdict}")
    return 0 if verdict == "pass" else 1


def cmd_scorecard(args: argparse.Namespace) -> int:
    runs, texts, digest = _load_runs(args.run)
    baseline = None
    if args.baseline and Path(args.baseline).is_file():
        baseline = load_baseline(args.baseline)
    leaks = gates_mod.count_secret_leaks(texts)
    summaries = {block: run["summary"] for block, run in runs.items()}
    results = gates_mod.evaluate(summaries, baseline, Thresholds(), secret_leaks=leaks)
    markdown = build_scorecard(runs, baseline=baseline, gate_results=results,
                               ledger_paths=args.run, digest=digest, title=args.title)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    json_path = out.with_suffix(".json")
    json_path.write_text(json.dumps(
        scorecard_json(runs, gate_results=results, baseline=baseline, digest=digest),
        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} and {json_path} (verdict {gates_mod.overall(results)})")
    return 0


def cmd_x10(args: argparse.Namespace) -> int:
    """Planted-regression test: every known regression must be caught."""
    sets = load_tasksets(Path("eval/tasks"))
    sha, dirty, untracked = git_revision(), git_dirty(), git_untracked()
    baseline_path = Path(args.baseline)
    baseline = load_baseline(baseline_path) if baseline_path.is_file() else None

    needed = sorted(set(MUTATION_BLOCKS.values()))
    clean = {block: run_block(sets[block], solver_name="stub", git_sha=sha,
                              git_dirty=dirty, git_untracked=untracked,
                              repeats=DEFAULT_REPEATS.get(block, 1))
             for block in needed}
    rows = []
    undetected = []
    for name in MUTATIONS:
        block = MUTATION_BLOCKS[name]
        mutant = run_block(sets[block], solver_name="stub", mutations=(name,),
                           git_sha=sha, git_dirty=dirty, git_untracked=untracked,
                           repeats=DEFAULT_REPEATS.get(block, 1))
        mutant_path = write_run(mutant, args.results)
        clean_summary = clean[block].summary
        mutant_summary = mutant.summary
        base_summary = (baseline["blocks"][block]["summary"] if baseline
                        and block in baseline.get("blocks") else clean_summary)
        results = gates_mod.evaluate_block(block, mutant_summary, base_summary,
                                           Thresholds())
        failed = gates_mod.failures(results)
        expected_gate = MUTATIONS[name].expected_gate
        detected = expected_gate in {gate.gate for gate in failed}
        if not detected:
            undetected.append(name)
        rows.append({
            "mutation": name, "block": block,
            "clean_success": round(clean_summary["success_rate"], 4),
            "mutant_success": round(mutant_summary["success_rate"], 4),
            "expected_gate": expected_gate,
            "gates_failed": ", ".join(f"{g.gate}={g.actual}" for g in failed) or "-",
            "detected": detected,
            "evidence": str(mutant_path),
        })
    print(f"{'mutation':26s} {'block':5s} {'clean':>6s} {'mutant':>6s}  detected  "
          f"gates")
    for row in rows:
        print(f"{row['mutation']:26s} {row['block']:5s} {row['clean_success']:6.3f} "
              f"{row['mutant_success']:6.3f}  {'yes' if row['detected'] else 'NO ':7s}  "
              f"{row['gates_failed']}")
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps({"rows": rows}, indent=2) + "\n",
                                     encoding="utf-8")
        print(f"wrote {args.report}")
    if undetected:
        return _fail("X-10 FAILED: regressions not caught by their expected gate: "
                     f"{undetected}")
    print("X-10 PASSED: every planted regression was caught by its expected gate")
    return 0


def cmd_x13(args: argparse.Namespace) -> int:
    """Solver ladder: can the suite separate weaker behaviour from stronger?"""
    sets = load_tasksets(Path("eval/tasks"))
    sha, dirty, untracked = git_revision(), git_dirty(), git_untracked()
    blocks = tuple(args.block) if args.block else BLOCKS
    rungs = tuple(args.rungs.split(",")) if args.rungs else LADDER

    matrix: dict[str, dict[str, Any]] = {}
    evidence: list[str] = []
    for rung in rungs:
        matrix[rung] = {}
        for block in blocks:
            outcome = run_block(sets[block], solver_name=rung, git_sha=sha,
                                git_dirty=dirty, git_untracked=untracked,
                                repeats=DEFAULT_REPEATS.get(block, 1))
            evidence.append(str(write_run(outcome, args.results)))
            matrix[rung][block] = {"successes": outcome.summary["successes"],
                                   "attempts": outcome.summary["attempts"],
                                   "success_rate": round(outcome.summary["success_rate"], 4),
                                   "false_successes": outcome.summary["false_successes"],
                                   "run_id": outcome.run_id}

    # Adjacent rungs: the spread a suite must show somewhere to be discriminative.
    spreads: list[dict[str, Any]] = []
    for upper, lower in zip(rungs, rungs[1:]):
        for block in blocks:
            delta = round(100.0 * (matrix[upper][block]["success_rate"]
                                   - matrix[lower][block]["success_rate"]), 2)
            spreads.append({"upper": upper, "lower": lower, "block": block,
                            "delta_pp": delta})
    best = max(spreads, key=lambda row: row["delta_pp"]) if spreads else None
    families_over = sorted({row["block"] for row in spreads
                            if row["delta_pp"] >= args.min_spread})
    passed = best is not None and best["delta_pp"] >= args.min_spread

    legend = {
        "oracle_ceiling": "gold-reading probe: bounds what the suite can award (not a system)",
        "stub": "reference profile: retrieval+citations, policy, verify+repair, "
                "schema extraction, majority reconciliation, backtracking, "
                "trust-ordered memory",
        "stub_naive": "shallow profile: last-span retrieval, no conflict detection, "
                      "greedy planning, recency-only memory, skills ignored",
        "stub_no_verify": "reference logic without read-back verification or citations",
    }

    header = ["family"] + [f"{rung}" for rung in rungs]
    lines = ["# Experiment X-13 — model-free solver ladder", "",
             f"Tasks root `eval/tasks`; blocks {', '.join(blocks)}; rungs "
             f"{', '.join(rungs)}; threshold {args.min_spread:.0f} pp on at least one "
             "family.", "",
             "| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for block in blocks:
        cells = [f"{matrix[rung][block]['successes']}/"
                 f"{matrix[rung][block]['attempts']} "
                 f"({100 * matrix[rung][block]['success_rate']:.0f}%)" for rung in rungs]
        lines.append(f"| {block} | " + " | ".join(cells) + " |")
    lines += ["", "## Rungs", "", "| rung | what it is |", "|---|---|"]
    lines += [f"| {rung} | {legend.get(rung, 'unknown rung')} |" for rung in rungs]
    lines += ["", "## Adjacent-rung spread (percentage points)", "",
              "| upper | lower | family | spread |", "|---|---|---|---|"]
    for row in spreads:
        lines.append(f"| {row['upper']} | {row['lower']} | {row['block']} | "
                     f"{row['delta_pp']:+.1f} |")
    verdict = ("PASSED" if passed else "FAILED")
    lines += ["", f"**X-13 {verdict}.** Largest adjacent spread "
              f"{best['delta_pp']:+.1f} pp ({best['upper']} vs {best['lower']} on "
              f"{best['block']}); families with a >= {args.min_spread:.0f} pp spread: "
              f"{', '.join(families_over) or 'none'}.", "",
              "The ladder is model-free: these are hand-written programs. A rung's score "
              "measures the harness, not J.A.R.V.I.S., and the oracle rung bounds what the "
              "suite can award rather than representing a system. Where the oracle and the "
              "reference rung tie, the reference program is already at this suite's ceiling: "
              "the family cannot show headroom above it, only below it."]
    if best is not None and best["delta_pp"] < args.min_spread:
        lines += ["", "Per the Stage 1 exit criteria this is a **fail**: no adjacent rungs "
                  "separate by the threshold anywhere, so the difficulty of the families "
                  "must be fixed before any model is evaluated on them."]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(
        {"rungs": list(rungs), "blocks": list(blocks), "matrix": matrix,
         "spreads": spreads, "min_spread_pp": args.min_spread,
         "passed": passed, "evidence": evidence,
         "git": {"sha": sha, "dirty": dirty, "untracked": untracked}},
        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"X-13 {'PASSED' if passed else 'FAILED'}: best spread "
          f"{best['delta_pp'] if best else 'n/a'} pp -> {out} and {out.with_suffix('.json')}")
    for row in spreads:
        if row["delta_pp"] >= args.min_spread:
            print(f"  {row['upper']:14s} vs {row['lower']:12s} on {row['block']:4s} "
                  f"{row['delta_pp']:+.1f} pp")
    return 0 if passed else 1


def cmd_find(args: argparse.Namespace) -> int:
    """Print the path of the newest recorded run matching the filter."""
    index = Path(args.results) / "index.json"
    if not index.is_file():
        return _fail(f"no ledger index at {index}; run a block first")
    runs = json.loads(index.read_text(encoding="utf-8")).get("runs", [])
    matches = [r for r in runs if r["block"] == args.block]
    if args.clean:
        matches = [r for r in matches if not r["mutations"]]
    if args.mutated:
        matches = [r for r in matches if args.mutated in r["mutations"]]
    if not matches:
        return _fail(f"no run for block {args.block} with the requested filter")
    print(matches[-1]["file"])
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    from .selftest import run_selftest

    return run_selftest(verbose=True)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eval.harness",
                                     description="J.A.R.V.I.S. evaluation harness (Stage 0 families + Stage 1 breadth)")
    parser.add_argument("--seed", type=int, default=1729)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="show task sets and mutations")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("run", help="run one or more blocks and write ledger files")
    p.add_argument("--block", action="append", choices=BLOCKS)
    p.add_argument("--solver", default="stub")
    p.add_argument("--mutations", default="")
    p.add_argument("--repeats", type=int, default=None)
    p.add_argument("--results", default="eval/results")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("baseline-freeze", help="freeze the clean stub as the baseline")
    p.add_argument("--results", default="eval/results")
    p.add_argument("--baselines", default="eval/baselines")
    p.add_argument("--block", action="append", choices=BLOCKS,
                   help="repeatable; default is every block")
    p.add_argument("--name", default="baseline-b-stage1.json")
    p.add_argument("--note", default="Stage 1 reference baseline (synthetic fixtures, "
                                     "deterministic reference solver, no model calls)")
    p.set_defaults(func=cmd_baseline_freeze)

    p = sub.add_parser("gates", help="evaluate gates for recorded runs")
    p.add_argument("--run", action="append", required=True)
    p.add_argument("--baseline", default="eval/baselines/baseline-a-stub.json")
    p.set_defaults(func=cmd_gates)

    p = sub.add_parser("scorecard", help="write a Markdown scorecard from recorded runs")
    p.add_argument("--run", action="append", required=True)
    p.add_argument("--baseline", default="eval/baselines/baseline-a-stub.json")
    p.add_argument("--out", default="eval/results/scorecard.md")
    p.add_argument("--title", default="Stage 0 scorecard")
    p.set_defaults(func=cmd_scorecard)

    p = sub.add_parser("x10", help="planted-regression test (must detect all regressions)")
    p.add_argument("--results", default="eval/results")
    p.add_argument("--baseline", default="eval/baselines/baseline-a-stub.json")
    p.add_argument("--report", default="eval/results/x10-report.json")
    p.set_defaults(func=cmd_x10)

    p = sub.add_parser("x13", help="solver ladder: does the suite separate weaker from "
                                   "stronger behaviour?")
    p.add_argument("--results", default="eval/results")
    p.add_argument("--out", default="eval/results/x13-ladder.md")
    p.add_argument("--rungs", default="")
    p.add_argument("--block", action="append", choices=BLOCKS)
    p.add_argument("--min-spread", type=float, default=10.0)
    p.set_defaults(func=cmd_x13)

    p = sub.add_parser("find", help="print the newest recorded run for a block")
    p.add_argument("--block", required=True, choices=BLOCKS)
    p.add_argument("--clean", action="store_true",
                   help="only runs without mutations")
    p.add_argument("--mutated", default=None,
                   help="only runs containing this mutation")
    p.add_argument("--results", default="eval/results")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("selftest", help="run harness self-checks")
    p.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
