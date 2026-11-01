"""Human-readable scorecard. Every number comes from a ledger run, not memory."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .gates import GateResult, overall

BANNER = ("> Synthetic fixtures only. The Stage 0 reference solver is a keyword "
          "stub, not a model: these numbers say nothing about model capability.")


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{100.0 * float(value):.1f}%"


def _num(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2f}"


def block_table(runs: dict[str, dict[str, Any]],
                baseline: dict[str, Any] | None) -> list[str]:
    rows = ["| Block | Tasks | Attempts | Success | pass^k | False success | "
            "Zero-tolerance | Citation precision | Cost (USD) | Baseline Δ |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for block, run in sorted(runs.items()):
        summary = run["summary"]
        zero_tol = (summary["false_successes"] + summary["approval_violations"]
                    + summary["duplicate_writes"]
                    + summary["unauthorized_side_effects"])
        base = (baseline or {}).get("blocks", {}).get(block, {}).get("summary")
        delta = ("n/a" if base is None else
                 f"{100.0 * (summary['success_rate'] - base['success_rate']):+.1f} pp")
        pass_k = ("n/a" if summary.get("pass_hat_k") is None else
                  f"pass^{summary['pass_k']} = {_pct(summary['pass_hat_k'])}")
        rows.append("| {block} | {tasks} | {attempts} | {success} | {pk} | {fs} | "
                    "{zt} | {prec} | {cost} | {delta} |".format(
                        block=block, tasks=summary["tasks"],
                        attempts=summary["attempts"],
                        success=_pct(summary["success_rate"]),
                        pk=pass_k,
                        fs=summary["false_successes"], zt=zero_tol,
                        prec=_num(summary["mean_citation_precision"]),
                        cost=_num(summary["total_cost_usd"]), delta=delta))
    return rows


def gate_table(results: list[GateResult]) -> list[str]:
    rows = ["| Gate | Block | Status | Actual | Limit | Detail |",
            "| --- | --- | --- | --- | --- | --- |"]
    for result in results:
        rows.append(f"| {result.gate} | {result.block} | {result.status} | "
                    f"{result.actual} | {result.limit} | {result.detail} |")
    return rows


def failure_list(runs: dict[str, dict[str, Any]]) -> list[str]:
    rows = ["| Block | Task | Repeat | Status | Why |",
            "| --- | --- | --- | --- | --- |"]
    any_failure = False
    for block, run in sorted(runs.items()):
        for record in run["records"]:
            if record["success"]:
                continue
            any_failure = True
            why = (record["detail"].get("problems") or record["detail"].get("message")
                   or record.get("error") or record["metrics"])
            rows.append(f"| {block} | {record['task_id']} | {record['repeat']} | "
                        f"{record['status']} | "
                        f"{str(why)[:160].replace('|', '/')} |")
    if not any_failure:
        rows.append("| — | — | — | — | no failing attempts in these runs |")
    return rows


def build(runs: dict[str, dict[str, Any]], *, baseline: dict[str, Any] | None,
          gate_results: list[GateResult], ledger_paths: list[str],
          digest: str = "", title: str = "Stage 0 scorecard") -> str:
    first = next(iter(runs.values()))
    lines = [
        f"# {title}",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        BANNER,
        "",
        f"- Harness version: `{first['harness_version']}`",
        f"- Git revision: `{first['git_sha']}` (tracked changes: "
        f"`{first['git_dirty']}`, untracked files: `{first.get('git_untracked', 0)}`)",
        f"- Config hash: `{first['config_hash']}`",
        f"- Solver: `{first['solver']}`",
        f"- Mutations: `{', '.join(first['mutations']) or 'none'}`",
        f"- Seed: `{first['seed']}`",
        f"- Baseline: `{(baseline or {}).get('baseline_id', 'none')}`",
        f"- Ledger digest: `{digest[:16] if digest else 'n/a'}`",
        f"- Gate verdict: **{overall(gate_results)}**",
        "",
        "## Runs",
        "",
        "| Block | Run id | Task set | Task set sha256 | Records |",
        "| --- | --- | --- | --- | --- |",
    ]
    for block, run in sorted(runs.items()):
        lines.append(f"| {block} | `{run['run_id']}` | {run['taskset_version']} | "
                     f"`{run['taskset_sha'][:16]}` | {len(run['records'])} |")
    lines += ["", "## Blocks", ""] + block_table(runs, baseline)
    lines += ["", "## Gates", ""] + gate_table(gate_results)
    lines += ["", "## Failing attempts", ""] + failure_list(runs)
    lines += ["", "## Evidence files", ""]
    lines += [f"- `{path}`" for path in ledger_paths] or ["- none"]
    lines.append("")
    return "\n".join(lines)


def to_json(runs: dict[str, dict[str, Any]], *, gate_results: list[GateResult],
            baseline: dict[str, Any] | None, digest: str = "") -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "baseline_id": (baseline or {}).get("baseline_id"),
        "ledger_digest": digest,
        "verdict": overall(gate_results),
        "gates": [gate.to_dict() for gate in gate_results],
        "blocks": {block: {k: v for k, v in run.items() if k != "records"}
                   for block, run in runs.items()},
    }
