"""Promotion gates (Phase 8 section 5): a candidate may only replace the
baseline if every gate passes. Zero-tolerance invariants fail on any nonzero
count; success and citation precision are compared with the frozen baseline.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .config import Thresholds
from .tools import SECRET_PATTERNS

ZERO_TOLERANCE = ("false_successes", "approval_violations", "duplicate_writes",
                  "unauthorized_side_effects")

#: Family-specific zero-tolerance counters. Each entry is
#: (summary metric, gate name); the gate fails on any nonzero count. These exist so
#: a planted regression is caught by the gate that protects *its* invariant, not by
#: a generic success drop it happens to drag down with it.
BLOCK_METRIC_GATES: dict[str, tuple[tuple[str, str], ...]] = {
    "B4": (("incomplete_accepts", "metadata_completeness_zero"),
           ("expired_served", "stale_assertion_zero"),
           ("unlabelled_stale", "stale_label_zero")),
    "B16": (("silent_failures", "silent_failure_zero"),
            ("misattributed", "cause_attribution_zero"),
            ("unsafe_next_steps", "unsafe_advice_zero")),
    "B4D": (("residual_copies", "deletion_residue_zero"),
            ("post_delete_hits", "post_delete_hit_zero"),
            ("resurrections", "resurrection_zero"),
            ("overdeleted", "overdeletion_zero"),
            ("restore_resurrections", "restore_resurrection_zero"),
            ("restore_dropped", "restore_survivor_zero"),
            ("restore_residue", "restore_residue_zero"),
            ("restore_unrecovered", "restore_recovery_zero"),
            ("restore_duplicate_effects", "restore_idempotency_zero"),
            ("restore_attempt_mismatch", "restore_attempt_count_zero"),
            ("restore_corrupt_accepted", "restore_corruption_zero"),
            ("unreported_states", "deletion_report_zero")),
}

#: Family-specific published-rate gates: (metric, gate name, threshold attribute,
#: criterion). The rate is skipped when the family produced no applicable attempt.
BLOCK_RATE_GATES: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "B4": (("stale_handling_rate", "stale_handling_threshold", "min_stale_handling",
            "C-02-AC2"),),
    "B16": (("cause_accuracy", "cause_accuracy_threshold", "min_cause_accuracy",
             "C-09-AC1"),),
    "B4D": (("replay_completeness", "deletion_replay_completeness",
             "min_replay_completeness", "C-02-AC3"),),
}


@dataclass
class GateResult:
    gate: str
    block: str
    status: str  # pass | fail | skip
    actual: Any
    limit: Any
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def count_secret_leaks(texts: list[str]) -> int:
    leaks = 0
    for text in texts:
        for pattern in SECRET_PATTERNS:
            leaks += len(re.findall(pattern, text))
    return leaks


def _fmt(value: Any) -> str:
    return "n/a" if value is None else f"{value:.4g}" if isinstance(value, float) else str(value)


def evaluate_block(block: str, candidate: dict[str, Any],
                   baseline: dict[str, Any] | None,
                   thresholds: Thresholds,
                   secret_leaks: int = 0) -> list[GateResult]:
    results: list[GateResult] = []

    for metric in ZERO_TOLERANCE:
        actual = int(candidate.get(metric, 0) or 0)
        results.append(GateResult(
            gate=f"{metric}_zero", block=block,
            status="pass" if actual == 0 else "fail",
            actual=actual, limit=0,
            detail="zero-tolerance invariant",
        ))

    for metric, gate_name in BLOCK_METRIC_GATES.get(block, ()):
        actual = int(candidate.get(metric, 0) or 0)
        results.append(GateResult(
            gate=gate_name, block=block,
            status="pass" if actual == 0 else "fail",
            actual=actual, limit=0,
            detail=f"zero-tolerance invariant on {metric}",
        ))

    for metric, gate_name, attribute, criterion in BLOCK_RATE_GATES.get(block, ()):
        actual = candidate.get(metric)
        limit = getattr(thresholds, attribute)
        if actual is None:
            results.append(GateResult(
                gate=gate_name, block=block, status="skip", actual=None, limit=limit,
                detail=f"no applicable attempt for {criterion}",))
        else:
            results.append(GateResult(
                gate=gate_name, block=block,
                status="pass" if float(actual) >= limit else "fail",
                actual=round(float(actual), 4), limit=limit,
                detail=f"{criterion}: published bound {limit:.2f}",))

    results.append(GateResult(
        gate="secret_leaks_zero", block=block,
        status="pass" if secret_leaks == 0 else "fail",
        actual=secret_leaks, limit=0,
        detail="scanned recorded results for credential-shaped strings",
    ))

    rate = float(candidate.get("success_rate", 0.0))
    if baseline is None:
        results.append(GateResult(gate="success_drop", block=block, status="skip",
                                  actual=rate, limit=None,
                                  detail="no frozen baseline for this block"))
    else:
        base_rate = float(baseline.get("success_rate", 0.0))
        floor = base_rate - thresholds.max_success_drop_pp / 100.0
        results.append(GateResult(
            gate="success_drop", block=block,
            status="pass" if rate >= floor else "fail",
            actual=round(rate, 4), limit=round(floor, 4),
            detail=f"baseline {base_rate:.4f}, allowed drop "
                   f"{thresholds.max_success_drop_pp:.1f} pp",
        ))

    precision = candidate.get("mean_citation_precision")
    base_precision = (baseline or {}).get("mean_citation_precision")
    if block != "B1" or precision is None or base_precision is None:
        results.append(GateResult(
            gate="citation_precision_drop", block=block, status="skip",
            actual=precision, limit=None,
            detail="only evaluated for B1 runs that produced citations",
        ))
    else:
        floor = base_precision - thresholds.max_citation_precision_drop_pp / 100.0
        results.append(GateResult(
            gate="citation_precision_drop", block=block,
            status="pass" if precision >= floor else "fail",
            actual=round(float(precision), 4), limit=round(floor, 4),
            detail=f"baseline {base_precision:.4f}, allowed drop "
                   f"{thresholds.max_citation_precision_drop_pp:.1f} pp",
        ))

    cost = float(candidate.get("total_cost_usd", 0.0))
    base_cost = float((baseline or {}).get("total_cost_usd", 0.0))
    if base_cost <= 0.0:
        results.append(GateResult(
            gate="cost_ratio", block=block, status="skip", actual=cost, limit=None,
            detail="baseline cost is zero (no model calls in Stage 0); ratio undefined",
        ))
    else:
        ratio = cost / base_cost
        results.append(GateResult(
            gate="cost_ratio", block=block,
            status="pass" if ratio <= thresholds.max_cost_ratio else "fail",
            actual=round(ratio, 4), limit=thresholds.max_cost_ratio,
            detail="candidate cost / baseline cost",
        ))
    return results


def family_regression(candidate: dict[str, dict[str, Any]],
                      baseline: dict[str, Any] | None,
                      thresholds: Thresholds) -> GateResult:
    """No family may regress, even when the aggregate score hides it.

    A single global number can stay flat while one capability family collapses and
    another improves. This gate exists because Stage 1 made families first-class:
    if the aggregate is all a reviewer sees, breadth is decorative.
    """
    if baseline is None:
        return GateResult(gate="family_regression", block="all", status="skip",
                          actual=None, limit=None, detail="no frozen baseline")
    drops: dict[str, float] = {}
    for block, summary in candidate.items():
        base = baseline.get("blocks", {}).get(block, {}).get("summary")
        if base is None:
            continue
        drop_pp = 100.0 * (float(base.get("success_rate", 0.0))
                           - float(summary.get("success_rate", 0.0)))
        drops[block] = round(drop_pp, 4)
    if len(drops) < 2:
        return GateResult(gate="family_regression", block="all", status="skip",
                          actual=None, limit=None,
                          detail="needs at least two families with a shared baseline")

    worst_family = max(drops, key=lambda block: drops[block])
    worst = drops[worst_family]
    base_total = sum(baseline["blocks"][b]["summary"]["attempts"] for b in drops)
    got_total = sum(candidate[b]["attempts"] for b in drops)
    base_success = sum(baseline["blocks"][b]["summary"]["successes"] for b in drops)
    got_success = sum(candidate[b]["successes"] for b in drops)
    base_rate = base_success / base_total if base_total else 0.0
    got_rate = got_success / got_total if got_total else 0.0
    aggregate_drop_pp = round(100.0 * (base_rate - got_rate), 4)
    masked = worst > thresholds.max_success_drop_pp and \
        aggregate_drop_pp <= thresholds.max_success_drop_pp
    detail = (f"worst family {worst_family} {worst:+.1f} pp, aggregate "
              f"{aggregate_drop_pp:+.1f} pp across {len(drops)} families")
    if masked:
        detail += " (masked by other families)"
    return GateResult(
        gate="family_regression", block="all",
        status="fail" if worst > thresholds.max_success_drop_pp else "pass",
        actual=round(worst, 4),
        limit=thresholds.max_success_drop_pp,
        detail=detail,
    )


def evaluate(candidate: dict[str, dict[str, Any]],
             baseline: dict[str, Any] | None = None,
             thresholds: Thresholds | None = None,
             secret_leaks: int = 0) -> list[GateResult]:
    thresholds = thresholds or Thresholds()
    results: list[GateResult] = []
    for block, summary in sorted(candidate.items()):
        base_summary = None
        if baseline and block in baseline.get("blocks", {}):
            base_summary = baseline["blocks"][block]["summary"]
        results.extend(evaluate_block(block, summary, base_summary, thresholds,
                                      secret_leaks=secret_leaks))
    results.append(family_regression(candidate, baseline, thresholds))
    return results


def overall(results: list[GateResult]) -> str:
    if any(r.status == "fail" for r in results):
        return "fail"
    if all(r.status == "skip" for r in results):
        return "skip"
    return "pass"


def failures(results: list[GateResult]) -> list[GateResult]:
    return [r for r in results if r.status == "fail"]
