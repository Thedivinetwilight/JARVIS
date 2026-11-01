"""Run a task block with one solver and produce immutable result records.

Record identity is `(git_sha, config_hash, task_set_version, seed, task_id,
repeat)`; the record body excludes wall-clock time so a rerun can be compared
byte for byte via `outcome_hash`.
"""
from __future__ import annotations

import copy
import platform
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import faults as faults_mod
from .config import HARNESS_VERSION, RunConfig, canonical_json, sha256_obj
from .registry import registry_sha
from .restore_integrity import apply_corruption
from .scorers import score_task
from .solvers import build_solver
from .taskset import TaskSet
from .tools import Executor, World


class RunnerError(RuntimeError):
    """Raised when a run cannot be started or completed safely."""


@dataclass
class ResultRecord:
    run_id: str
    block: str
    task_id: str
    repeat: int
    seed: int
    solver: str
    mutations: list[str]
    status: str
    success: bool
    false_success: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    cost_is_synthetic: bool = True
    outcome_hash: str = ""
    error: str | None = None

    def without_latency(self) -> dict[str, Any]:
        """The hashed view of an attempt.

        Identity fields (run id, seed) are recorded but not hashed: the hash must
        answer "did the observed outcome change?", so that the same task on the
        same configuration hashes the same across runs, seeds and processes.
        Wall-clock latency is excluded for the same reason.
        """
        payload = asdict(self)
        for derived in ("latency_ms", "outcome_hash", "run_id", "seed"):
            payload.pop(derived, None)
        return payload

    def finalize(self) -> "ResultRecord":
        self.outcome_hash = sha256_obj(self.without_latency())
        return self

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunOutcome:
    run_id: str
    block: str
    solver: str
    mutations: list[str]
    seed: int
    repeats: int
    taskset_version: str
    taskset_sha: str
    config_hash: str
    git_sha: str
    git_dirty: bool
    git_untracked: int
    registry_sha: str
    harness_version: str
    python_version: str
    started_at: str
    duration_s: float
    records: list[ResultRecord]

    @property
    def summary(self) -> dict[str, Any]:
        return summarize(self.records)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "block": self.block,
            "solver": self.solver,
            "mutations": self.mutations,
            "seed": self.seed,
            "repeats": self.repeats,
            "taskset_version": self.taskset_version,
            "taskset_sha": self.taskset_sha,
            "config_hash": self.config_hash,
            "git_sha": self.git_sha,
            "git_dirty": self.git_dirty,
            "git_untracked": self.git_untracked,
            "registry_sha": self.registry_sha,
            "harness_version": self.harness_version,
            "python_version": self.python_version,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 3),
            "summary": self.summary,
            "records": [r.to_json() for r in self.records],
        }


def summarize(records: list[ResultRecord]) -> dict[str, Any]:
    total = len(records)
    successes = sum(1 for r in records if r.success)
    false_successes = sum(1 for r in records if r.false_success)
    latencies = sorted(r.latency_ms for r in records)

    def total_of(key: str) -> float:
        return float(sum(r.metrics.get(key, 0) or 0 for r in records
                         if isinstance(r.metrics.get(key), (int, float))))

    precisions = [r.metrics["citation_precision"] for r in records
                  if r.metrics.get("citation_precision") is not None]
    by_subfamily: dict[str, dict[str, Any]] = {}
    for record in records:
        sub = record.metrics.get("subfamily")
        if not sub:
            continue
        bucket = by_subfamily.setdefault(str(sub), {"attempts": 0, "successes": 0})
        bucket["attempts"] += 1
        bucket["successes"] += 1 if record.success else 0
    for bucket in by_subfamily.values():
        bucket["success_rate"] = (bucket["successes"] / bucket["attempts"]
                                  if bucket["attempts"] else 0.0)

    stale_required = int(total_of("stale_required"))
    cause_required = int(total_of("cause_required"))
    max_repeat = max((r.repeat for r in records), default=1)
    k = min(4, max_repeat)
    return {
        "attempts": total,
        "tasks": len({r.task_id for r in records}),
        "successes": successes,
        "success_rate": (successes / total) if total else 0.0,
        "false_successes": false_successes,
        "approval_violations": int(total_of("approval_violations")),
        "duplicate_writes": int(total_of("duplicate_writes")),
        "unauthorized_side_effects": int(total_of("unauthorized_side_effects")),
        "validation_errors": int(total_of("validation_errors")),
        # M5 slices: memory metadata/expiry (B4) and limitation explanation (B16).
        "incomplete_accepts": int(total_of("incomplete_accepts")),
        "expired_served": int(total_of("expired_served")),
        "unlabelled_stale": int(total_of("unlabelled_stale")),
        "stale_required": int(total_of("stale_required")),
        "stale_correct": int(total_of("stale_correct")),
        "silent_failures": int(total_of("silent_failures")),
        "misattributed": int(total_of("misattributed")),
        "unsafe_next_steps": int(total_of("unsafe_next_steps")),
        "spurious_causes": int(total_of("spurious_causes")),
        "cause_required": int(total_of("cause_required")),
        "cause_correct": int(total_of("cause_correct")),
        # M5 slice 2: deletion drills and residue (B4D).
        "residual_copies": int(total_of("residual_copies")),
        "post_delete_hits": int(total_of("post_delete_hits")),
        "resurrections": int(total_of("resurrections")),
        "overdeleted": int(total_of("overdeleted")),
        "unreported_states": int(total_of("unreported_states")),
        # M5 slice 2 extension: restore of a pre-deletion backup (B4D).
        "restore_resurrections": int(total_of("restore_resurrections")),
        "restore_dropped": int(total_of("restore_dropped")),
        "restore_residue": int(total_of("restore_residue")),
        "restore_events": int(total_of("restores")),
        "restore_attempts": int(total_of("restore_attempts")),
        "restore_unrecovered": int(total_of("restore_unrecovered")),
        "restore_duplicate_effects": int(total_of("restore_duplicate_effects")),
        "restore_attempt_mismatch": int(total_of("restore_attempt_mismatch")),
        # M5 slice 3: evaluator-chosen interruptions and corrupt backups (B4D).
        "restore_corrupt_accepted": int(total_of("restore_corrupt_accepted")),
        "restore_injected_stops": int(total_of("restore_injected_stops")),
        "restore_corrupt_backups": int(total_of("restore_corrupt_backups")),
        "restore_rejected": int(total_of("restore_rejected")),
        "restore_repaired": int(total_of("restore_repaired")),
        # M5 slice 4: mid-write interruption and transactional recovery (B4D).
        "restore_partial_residue": int(total_of("restore_partial_residue")),
        "restore_rollback_errors": int(total_of("restore_rollback_errors")),
        "restore_mid_write_stops": int(total_of("restore_mid_write_stops")),
        "restore_rollbacks": int(total_of("restore_rollbacks")),
        "replay_complete": int(total_of("replay_complete")),
        "replay_tasks": sum(1 for r in records if r.metrics.get("replay_complete") is not None),
        "citations": int(total_of("citations")),
        "supported_citations": int(total_of("supported_citations")),
        "mean_citation_precision": (sum(precisions) / len(precisions)) if precisions else None,
        "abstentions": sum(1 for r in records if r.metrics.get("abstain_expected")
                           and not r.metrics.get("citations")),
        "median_latency_ms": (latencies[len(latencies) // 2] if latencies else 0.0),
        "pass_k": k if k > 1 else None,
        "pass_hat_k": pass_hat_k(records, k) if k > 1 else None,
        "stale_handling_rate": (total_of("stale_correct") / stale_required
                                if stale_required else None),
        "cause_accuracy": (total_of("cause_correct") / cause_required
                           if cause_required else None),
        "replay_completeness": (sum(1 for r in records
                                    if r.metrics.get("replay_complete") == 1)
                                / sum(1 for r in records
                                      if r.metrics.get("replay_complete") is not None)
                                if any(r.metrics.get("replay_complete") is not None
                                       for r in records) else None),
        "by_subfamily": by_subfamily,
        "total_cost_usd": round(sum(r.cost_usd for r in records), 6),
        "cost_is_synthetic": all(r.cost_is_synthetic for r in records),
    }


def _task_setup(task: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    policy = copy.deepcopy(task.get("policy", {}))
    faults = copy.deepcopy(task.get("faults", []))
    budget = int(task.get("budget", 50))
    return policy, faults, budget


def solver_view(task: dict[str, Any], block: str) -> dict[str, Any]:
    """The task as a solver may see it.

    For B4D the gold state, the evaluator's fault schedule and the corruption
    recipe are removed: a solver must detect damage from the data and the
    integrity digest, and must discover an interruption when the mediated step
    fails. The backup contents are replaced by their damaged view, so the digest
    it was given no longer matches -- that mismatch is the only clue.
    """
    view = copy.deepcopy(task)
    view["block"] = block
    if block != "B4D":
        return view
    view.pop("expected", None)
    view.pop("restore_faults", None)
    backup = view.get("backup")
    if isinstance(backup, dict):
        corruption = backup.pop("corruption", None)
        if corruption:
            backup["contents"] = apply_corruption(backup["contents"], corruption)
    return view


def run_task(task: dict[str, Any], *, block: str, solver_name: str,
             mutations: tuple[str, ...], seed: int, run_id: str, repeat: int,
             corpus: tuple[dict[str, Any], ...] = ()) -> ResultRecord:
    solver = build_solver(solver_name, mutations)
    policy, faults, budget = _task_setup(task)
    world = World()
    injection = faults_mod.injection(task) if block == "B4D" else None
    kind = (injection or {}).get("kind")
    executor = Executor(world, policy=policy, faults=faults, call_budget=budget,
                        restore_stops=(injection or {}).get("stops")
                        if kind == "interrupt_after_records" else None,
                        restore_writes=(injection or {}).get("stops")
                        if kind == "interrupt_mid_write" else None,
                        restore_injection=injection)

    from .solvers.base import ToolContext

    ctx = ToolContext(executor, policy, mutations, task["id"])
    task_view = copy.deepcopy(task)
    task_view["block"] = block
    if corpus:
        task_view["_corpus"] = [copy.deepcopy(doc) for doc in corpus]
    solver_task = solver_view(task_view, block)
    if corpus:
        solver_task["_corpus"] = [copy.deepcopy(doc) for doc in corpus]

    started = time.perf_counter()
    error: str | None = None
    try:
        result = solver.solve(solver_task, ctx)
    except Exception as exc:  # a crashing solver is a failed attempt, not a crash
        from .solvers.base import SolveResult
        result = SolveResult(status="failed", message=f"solver error: {exc}")
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - started) * 1000.0

    score = score_task(task_view, result, world.snapshot(), executor,
                       corpus=list(corpus))
    return ResultRecord(
        run_id=run_id, block=block, task_id=str(task["id"]),
        repeat=repeat, seed=seed, solver=solver_name, mutations=list(mutations),
        status=result.status, success=bool(score["success"]),
        false_success=bool(score["false_success"]), metrics=score["metrics"],
        detail=score["detail"], steps=result.steps, latency_ms=round(latency_ms, 3),
        cost_usd=0.0, cost_is_synthetic=True, error=error,
    ).finalize()


def run_block(taskset: TaskSet, *, solver_name: str = "stub",
              mutations: tuple[str, ...] = (), seed: int = 1729,
              repeats: int | None = None, config: RunConfig | None = None,
              git_sha: str = "unknown", git_dirty: bool = False,
              git_untracked: int = 0) -> RunOutcome:
    config = config or RunConfig(seed=seed)
    repeats = repeats if repeats is not None else config.repeats.get(taskset.block, 1)
    if repeats < 1:
        raise RunnerError("repeats must be >= 1")
    if taskset.block not in {"B1", "B2", "B3", "B8", "B9", "B10", "B4", "B4D", "B16"}:
        raise RunnerError(f"unsupported block {taskset.block!r}")

    try:
        reg_hash = globals()["registry_sha"]()
    except OSError:                     # registry absent: harness still usable
        reg_hash = "missing"
    run_id = sha256_obj({
        "block": taskset.block, "taskset": taskset.sha, "config": config.hash(),
        "solver": solver_name, "mutations": list(mutations), "git": git_sha,
        "dirty": git_dirty, "registry": reg_hash,
    })[:16]

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    started = time.perf_counter()
    records: list[ResultRecord] = []
    for repeat in range(1, repeats + 1):
        for task in taskset.tasks:
            records.append(run_task(
                task, block=taskset.block, solver_name=solver_name,
                mutations=mutations, seed=seed,
                run_id=run_id, repeat=repeat, corpus=taskset.corpus))
    duration = time.perf_counter() - started

    return RunOutcome(
        run_id=run_id, block=taskset.block, solver=solver_name,
        mutations=list(mutations), seed=seed, repeats=repeats,
        taskset_version=taskset.version, taskset_sha=taskset.sha,
        config_hash=config.hash(), git_sha=git_sha, git_dirty=git_dirty,
        git_untracked=git_untracked, registry_sha=reg_hash,
        harness_version=HARNESS_VERSION,
        python_version=platform.python_version(), started_at=started_at,
        duration_s=duration, records=records,
    )


def pass_hat_k(records: list[ResultRecord], k: int) -> float:
    """Fraction of tasks that succeeded on all of the first k attempts (B2)."""
    by_task: dict[str, list[ResultRecord]] = {}
    for record in records:
        by_task.setdefault(record.task_id, []).append(record)
    if not by_task:
        return 0.0
    passed = 0
    for attempts in by_task.values():
        attempts.sort(key=lambda r: r.repeat)
        if len(attempts) >= k and all(a.success for a in attempts[:k]):
            passed += 1
    return passed / len(by_task)


def records_to_jsonl(records: list[ResultRecord]) -> str:
    return "\n".join(canonical_json(r.to_json()) for r in records) + "\n"
