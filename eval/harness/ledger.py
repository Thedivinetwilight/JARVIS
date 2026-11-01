"""Append-only result ledger and frozen-baseline storage.

Layout:
  eval/results/<run_id>.jsonl   one meta line, then one line per attempt
  eval/baselines/<name>.json    frozen summaries + per-task outcome hashes

A record is never edited in place; a rerun produces a new file with a different
run_id because the run identity includes the config hash and git revision.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import HARNESS_VERSION, canonical_json, sha256_obj
from .runner import RunOutcome


class LedgerError(RuntimeError):
    """Raised when a ledger or baseline file cannot be read or trusted."""


def _index_path(root: Path) -> Path:
    return root / "index.json"


def _update_index(root: Path, entry: dict[str, Any]) -> None:
    """Keep a human- and script-readable index of every recorded run."""
    path = _index_path(root)
    payload = {"runs": []}
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
    runs = [r for r in payload.get("runs", [])
            if not (r["file"] == entry["file"])]
    runs.append(entry)
    payload["runs"] = sorted(runs, key=lambda r: r["created_at"])
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_run(outcome: RunOutcome, root: Path | str = "eval/results") -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    suffix = "-" + "+".join(outcome.mutations) if outcome.mutations else ""
    path = root / f"{outcome.started_at[:10]}-{outcome.block}-{outcome.run_id}{suffix}.jsonl"
    meta = {key: value for key, value in outcome.to_json().items() if key != "records"}
    meta["type"] = "meta"
    lines = [canonical_json(meta)]
    for record in outcome.records:
        payload = record.to_json() | {"type": "result"}
        lines.append(canonical_json(payload))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _update_index(root, {
        "file": str(path),
        "block": outcome.block,
        "run_id": outcome.run_id,
        "solver": outcome.solver,
        "mutations": list(outcome.mutations),
        "records": len(outcome.records),
        "created_at": outcome.started_at,
        "git_sha": outcome.git_sha,
        "ledger_digest": sha256_obj([r.outcome_hash for r in outcome.records]),
    })
    return path


def read_run(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    meta: dict[str, Any] | None = None
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{path}:{line_no}: invalid JSON ({exc})") from exc
        if payload.get("type") == "meta":
            meta = payload
        elif payload.get("type") == "result":
            records.append(payload)
    if meta is None:
        raise LedgerError(f"{path}: no meta line")
    return {"path": str(path), "meta": meta, "records": records}


def find_runs(root: Path | str = "eval/results") -> list[Path]:
    root = Path(root)
    return sorted(root.glob("*.jsonl")) if root.is_dir() else []


def task_hashes(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    hashes: dict[str, list[str]] = {}
    for record in records:
        hashes.setdefault(record["task_id"], []).append(record["outcome_hash"])
    for value in hashes.values():
        value.sort()
    return hashes


def freeze_baseline(outcomes: dict[str, RunOutcome], *,
                    root: Path | str = "eval/baselines",
                    name: str = "baseline-a-stub.json",
                    note: str = "",
                    required_blocks: set[str] | frozenset[str] = frozenset({"B1", "B2", "B3"}),
                    ) -> Path:
    """Freeze summaries and per-task hashes.

    `required_blocks` defaults to the Stage 0 core. A slice baseline passes its own
    family set so that freezing B4/B16 evidence does not require re-freezing B1-B3,
    which would silently restate older evidence under a new run identity.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    missing = set(required_blocks) - outcomes.keys()
    if missing:
        raise LedgerError(f"cannot freeze a baseline without blocks {sorted(missing)}")
    blocks: dict[str, Any] = {}
    for block, outcome in sorted(outcomes.items()):
        blocks[block] = {
            "run_id": outcome.run_id,
            "taskset_version": outcome.taskset_version,
            "taskset_sha": outcome.taskset_sha,
            "config_hash": outcome.config_hash,
            "repeats": outcome.repeats,
            "solver": outcome.solver,
            "summary": outcome.summary,
            "task_hashes": task_hashes([r.to_json() for r in outcome.records]),
        }
    payload = {
        "baseline_id": sha256_obj({"blocks": {b: v["taskset_sha"] for b, v in blocks.items()},
                                   "note": note})[:16],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "harness_version": HARNESS_VERSION,
        "git_sha": next(iter(outcomes.values())).git_sha,
        "git_dirty": next(iter(outcomes.values())).git_dirty,
        "note": note,
        "blocks": blocks,
    }
    path = root / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_baseline(path: Path | str = "eval/baselines/baseline-a-stub.json") -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise LedgerError(f"no frozen baseline at {path}; run 'baseline-freeze' first")
    return json.loads(path.read_text(encoding="utf-8"))


def ledger_digest(paths: list[Path | str]) -> str:
    """Stable digest of the recorded evidence, used in the scorecard header."""
    payload = []
    for path in paths:
        run = read_run(path)
        payload.append({"run_id": run["meta"]["run_id"],
                        "records": [r["outcome_hash"] for r in run["records"]]})
    return sha256_obj(payload)
