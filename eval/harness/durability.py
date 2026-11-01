"""The evaluator's hidden crash schedule for the B4W durability slice.

The fixture declares *which* stages may be interrupted and which operation is
interrupted. It does not say where in the bytes the crash lands, and it does not
say which stage the evaluator will pick: this module derives both from a digest
of the task id, the fixture's salt and the crash attempt number.

Three properties matter, and each has a test:

* **deterministic** -- the same task always crashes at the same byte, so a run is
  reproducible and comparable to a frozen baseline;
* **hidden** -- :func:`eval.harness.runner.solver_view` removes the whole
  ``durability`` block (candidates, salt, crash operation, plan) from the task a
  solver sees, so a solver cannot look up where it will be interrupted and has to
  survive an interruption wherever it lands;
* **outcome-invariant** -- the gold state is the same for every candidate stage
  and every legal offset inside the truncated operation. The loader recomputes
  the gold from the operations and refuses a fixture whose declared expectation
  depends on the choice.

The stages are the four places a write can die plus the recovery replay itself:

``wal_append``     the entry frame is truncated inside the WAL;
``record_write``   the record line is truncated inside ``records.log``;
``commit_marker``  the commit frame is truncated, so the entry never commits;
``corrupt_entry``  the entry frame is truncated and then one written byte is
                   flipped, so the frame is complete but fails its checksum;
``replay_write``   recovery is interrupted while rewriting ``records.log``.

Only ``replay_write`` happens after the interrupted transaction: in the other
four the interrupted transaction must not be durable, and recovery must remove
whatever the attempt already left on disk.
"""
from __future__ import annotations

import hashlib
from typing import Any

from pathlib import Path

from . import wal_store
from .wal_store import CrashPlan

#: Stages that leave the interrupted transaction uncommitted.
UNCOMMITTED_STAGES = wal_store.UNCOMMITTED_STAGES

#: How many crashes one scenario may inject.
CRASH_LIMIT = 3


def _digest(task: dict[str, Any], attempt: int) -> str:
    salt = str((task.get("durability") or {}).get("salt", task.get("id", "")))
    return hashlib.sha256(f"{task.get('id')}:{salt}:{attempt}".encode("utf-8")).hexdigest()


def _target_bytes(task: dict[str, Any], stage: str) -> tuple[int, str, int, int]:
    """Bytes the crash can land in, the record it belongs to, its op index and seq."""
    spec = task.get("durability") or {}
    ops = task.get("operations") or []
    index = int(spec.get("crash_op", len(ops) - 1))
    op = ops[index]
    record = str(op.get("record_id"))
    body = record_for(task, op.get("record_id"))
    seq = seq_for(task, index)

    if stage in ("wal_append", "corrupt_entry"):
        entry = wal_store.frame_entry(op["kind"], seq, str(op["txn"]), body)
        return len(wal_store.encode_frame(entry)), record, index, seq
    if stage == "record_write":
        line = wal_store.canonical(body) + b"\n"
        return len(line), record, index, seq
    if stage in ("commit_marker", "crash_after_commit"):
        entry = wal_store.frame_entry("commit", seq + 1, str(op["txn"]))
        return len(wal_store.encode_frame(entry)), record, index, seq
    if stage == "replay_write":
        # The replay rewrites the committed records in id order; the first one is
        # the bytes the crash lands in.
        name = _committed_before_crash(task)
        line = wal_store.canonical({"record_id": name}) + b"\n"
        return len(line), name, index, seq
    raise wal_store.WalError(f"unknown crash stage {stage!r}")


def _committed_before_crash(task: dict[str, Any]) -> str:
    """The lowest-id record that is durable before the interrupted operation."""
    ops = task.get("operations") or []
    index = int((task.get("durability") or {}).get("crash_op", len(ops) - 1))
    names = sorted(str(op.get("record_id")) for op in ops[:index] if op["kind"] == "put")
    planted = [str(entry["record"]["record_id"]) for entry in task.get("planted_entries") or []
               if entry["kind"] == "put"]
    names.extend(planted)
    return names[0] if names else "r0"


def plans(task: dict[str, Any]) -> list[CrashPlan]:
    """The hidden crash plan for one task: zero or more (stage, offset) decisions."""
    spec = task.get("durability") or {}
    crashes = int(spec.get("crashes", 0))
    if crashes <= 0:
        return []
    fixed = list(spec.get("crash_stages") or [])
    candidates = list(spec.get("crash_candidates") or [])
    if not candidates and not fixed:
        raise wal_store.WalError(f"{task.get('id')}: a crash scenario declares no candidates")
    if fixed and len(fixed) != crashes:
        raise wal_store.WalError(
            f"{task.get('id')}: {len(fixed)} declared crash stages for {crashes} crashes")
    out: list[CrashPlan] = []
    for attempt in range(1, crashes + 1):
        digest = _digest(task, attempt)
        if fixed:
            stage = fixed[attempt - 1]
        else:
            stage = candidates[int(digest[:8], 16) % len(candidates)]
        if attempt == 1 and stage == "replay_write" and crashes > 1:
            # With more than one crash the first one has to start the recovery that
            # the second one interrupts; a lone replay crash is fine, because the
            # reference path verifies the store from disk once at the end.
            raise wal_store.WalError(
                f"{task.get('id')}: a {crashes}-crash scenario may not start with a "
                "recovery replay")
        size, record, _index, _seq = _target_bytes(task, stage)
        # Strictly inside the operation: 1..size-1. Never 0 (nothing on disk) and
        # never size (a complete write is not an interruption).
        offset = 1 + int(digest[8:24], 16) % (size - 1)
        if stage in ("corrupt_entry", "crash_after_commit"):
            # A complete frame: with a flipped payload byte, or committed before
            # the simulated process died.
            offset = size
        out.append(CrashPlan(stage=stage, record=record, offset=offset,
                             target_bytes=size, attempt=attempt,
                             corrupt=(stage == "corrupt_entry")))
    return out


def injection(task: dict[str, Any] | None) -> dict[str, Any] | None:
    """The evaluator's durability schedule for one task, or None."""
    if not task:
        return None
    spec = task.get("durability")
    if not spec:
        return None
    chosen = plans(task)
    return {
        "kind": "simulated_crash",
        "salt": str(spec.get("salt", task.get("id", ""))),
        "crash_candidates": [str(stage) for stage in spec.get("crash_candidates") or []],
        "crash_op": int(spec.get("crash_op", -1)),
        "crash_record": str((spec.get("crash_record") or "")),
        "crash_count": len(chosen),
        "plans": [plan.describe() for plan in chosen],
        "recovery_limit": CRASH_LIMIT,
    }


def summarise(injection_value: dict[str, Any] | None) -> dict[str, Any]:
    """The part of an injection a ledger may record after the run."""
    if not injection_value:
        return {}
    return {
        "kind": injection_value["kind"],
        "salt": injection_value["salt"],
        "crash_candidates": list(injection_value["crash_candidates"]),
        "crash_op": injection_value["crash_op"],
        "crash_count": injection_value["crash_count"],
        "plans": list(injection_value["plans"]),
    }


# -- gold -------------------------------------------------------------------

def apply_planted(task: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    """The store state implied by the entries a fixture plants before the run."""
    planted = task.get("planted_entries") or []
    applied: list[dict[str, Any]] = []
    commits = {entry["txn"] for entry in planted if entry["kind"] == "commit"}
    for entry in planted:
        if entry["kind"] == "commit":
            continue
        if entry["txn"] in commits:
            applied.append(entry)
    applied.sort(key=lambda item: item["seq"])
    return wal_store.apply_entries(applied)


def expected_outcome(task: dict[str, Any], *, stage: str | None = None) -> dict[str, Any]:
    """The final store a correct engine reaches, computed from the fixture alone.

    ``stage`` is the crash stage; when it is one of the uncommitted stages the
    interrupted operation never commits. ``None`` means "no crash".
    """
    spec = task.get("durability") or {}
    ops = task.get("operations") or []
    index = int(spec.get("crash_op", len(ops) - 1)) if spec.get("crashes") else None
    # Damage a fixture plants before the run sits *before* every operation, so
    # nothing this process writes can be committed: recovery must stop at it.
    planted_damage = bool(spec.get("planted_corruption") or task.get("planted_corruption"))
    if planted_damage:
        index = 0
        stage = stage or "wal_append"
    records, deleted = apply_planted(task)
    durable_before = set(records)
    planted_deleted = set(deleted)
    committed: list[str] = []
    for position, op in enumerate(ops):
        rid = str(op.get("record_id"))
        reached = (position >= index) if planted_damage else (position == index)
        interrupted = (index is not None and reached
                       and (stage is None or stage in UNCOMMITTED_STAGES))
        if interrupted:
            continue
        if op["kind"] == "put" and rid not in deleted:
            records[rid] = record_for(task, rid)
            committed.append(rid)
        elif op["kind"] == "delete":
            records.pop(rid, None)
            deleted.add(rid)
    durable = sorted(records)
    absent = sorted({str(op.get("record_id")) for op in ops if op["kind"] == "put"}
                    - set(durable) - deleted)
    unrelated = sorted(set(durable_before) & set(durable)
                       - {str(op.get("record_id")) for op in ops})
    return {
        "durable": durable,
        "absent": absent,
        "deleted": sorted(deleted),
        "unrelated": unrelated,
        "planted": sorted(durable_before),
        "planted_deleted": sorted(planted_deleted),
        "interrupted_record": (str(spec.get("crash_record")) if spec.get("crash_record") else None),
        "interrupted_op": index,
        "crashed": bool(spec.get("crashes")),
        "recovery_required": bool(spec.get("crashes")),
        "replay_required": int(spec.get("crashes", 0)) >= 1,
    }


# -- the engine the harness drives -----------------------------------------

class DurabilityDriver:
    """Owns one scenario's temporary store directory and its hidden crash plan.

    The solver never sees this object's internals: it may write an operation,
    recover, or report, and the driver decides whether the write dies. The hidden
    plan is a queue: each crash consumes one plan and the next store handle gets
    the next one, so a second crash (during recovery) is possible but bounded.

    The directory is created under ``tempfile.gettempdir()`` with the harness
    prefix, is never reused across tasks, and is removed by :meth:`close`.
    """

    def __init__(self, task: dict[str, Any], *, root_parent: str | None = None) -> None:
        self.task = task
        self.plan_queue = list(plans(task))
        self.injection = injection(task)
        self.root: Path | None = None
        self.store: wal_store.WalStore | None = None
        self.activities: list[dict[str, Any]] = []
        self.crashes: list[dict[str, Any]] = []
        self.recoveries: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.report: dict[str, Any] | None = None
        self._parent = root_parent
        self._closed = False

    # -- lifecycle --------------------------------------------------------
    def open(self) -> dict[str, Any]:
        """Create the store directory and write the fixture's planted history."""
        import tempfile

        parent = Path(self._parent) if self._parent else Path(tempfile.gettempdir())
        self.root = Path(tempfile.mkdtemp(prefix=wal_store.STORE_PREFIX, dir=str(parent)))
        # mkdtemp already created the directory; initialise its files in place.
        self.root.joinpath(wal_store.WAL_NAME).write_bytes(wal_store.MAGIC)
        self.root.joinpath(wal_store.DATA_NAME).write_bytes(b"")
        self.store = self._new_handle()
        planted = self.task.get("planted_entries") or []
        if planted:
            self.store.plant(planted)
        corruption = ((self.task.get("durability") or {}).get("planted_corruption")
                      or self.task.get("planted_corruption"))
        if corruption:
            self._plant_corruption(corruption)
        self.activities.append({"event": "open", "planted": len(planted),
                                "root": self.root.name})
        return {"root": self.root.name, "planted": len(planted),
                "operations": len(self.task.get("operations") or [])}

    def _plant_corruption(self, spec: dict[str, Any]) -> None:
        """Append damage a fixture declares: a torn frame of ``bytes`` length."""
        count = int(spec.get("bytes", 24))
        length = int(spec.get("frame_bytes", count))
        chunk = ("%08x" % length).encode("ascii") + b"x" * max(0, count - 8)
        with open(self.root / wal_store.WAL_NAME, "ab") as handle:
            handle.write(chunk)
            handle.flush()
            import os
            os.fsync(handle.fileno())
        self.activities.append({"event": "planted_corruption", "bytes": len(chunk)})

    def _new_handle(self) -> wal_store.WalStore:
        """A fresh process handle. The next plan stays queued until it fires."""
        plan = self.plan_queue[0] if self.plan_queue else None
        return wal_store.WalStore(self.root, plan=plan, crash_activities=self.activities)

    def _note_crash(self, store: wal_store.WalStore, exc: wal_store.StoreCrashed,
                    operation: int | None) -> None:
        """Kill the handle, retire the plan that fired, record the crash."""
        store.crash(exc)
        if self.plan_queue:
            self.plan_queue.pop(0)
        self.crashes.append({"stage": exc.stage, "record": exc.record,
                             "offset": exc.offset, "operation": operation})

    def close(self) -> None:
        """Close the handle and delete the directory. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        if self.store is not None:
            try:
                self.store.close()
            except wal_store.WalError:
                pass
        if self.root is not None:
            try:
                wal_store.remove_store(self.root)
            except wal_store.WalError:
                pass
        self.activities.append({"event": "close"})

    # -- solver-facing operations ----------------------------------------
    def _handle(self) -> wal_store.WalStore:
        """A live handle on the directory: a new one if the last died."""
        if self.store is None or self.store.dead:
            self.store = self._new_handle()
        return self.store

    def write(self, index: int) -> dict[str, Any]:
        """Write one declared operation, or die at the hidden crash point."""
        if self.root is None:
            raise wal_store.WalError("store is not open")
        store = self._handle()
        ops = self.task.get("operations") or []
        op = ops[int(index)]
        record = record_for(self.task, op.get("record_id"))
        seq = seq_for(self.task, int(index))
        payload = {"kind": op["kind"], "txn": str(op["txn"]),
                   "record": record}
        try:
            result = store.write_operation(payload, seq)
        except wal_store.StoreCrashed as exc:
            self._note_crash(store, exc, int(index))
            raise
        self.writes.append({"operation": int(index), **result})
        return result

    def recover(self, **knobs: Any) -> dict[str, Any]:
        """Restart from disk and recover; the recovery itself may crash."""
        if self.root is None:
            raise wal_store.WalError("store directory does not exist")
        self.store = self._new_handle()
        store = self.store
        try:
            report = store.recover(**knobs)
        except wal_store.StoreCrashed as exc:
            self._note_crash(store, exc, None)
            raise
        store.close()
        self.store = None
        payload = report.to_dict()
        payload["knobs"] = {key: (list(value) if isinstance(value, list) else value)
                            for key, value in knobs.items()}
        self.recoveries.append(payload)
        return payload

    def audit(self) -> dict[str, Any]:
        """Read the store from disk -- what the scorer scores, not the report."""
        if self.root is None:
            return {"error": "no store"}
        return wal_store.store_audit(self.root)

    def apply_uncommitted(self, record_id: str) -> dict[str, Any]:
        """Planted-regression knob: leave a record line that no commit covers."""
        store = self._handle()
        record = record_for(self.task, record_id)
        if record is None:
            raise wal_store.WalError(f"unknown record {record_id!r}")
        written = store.write_record(record)
        self.activities.append({"event": "apply_uncommitted", "record": record_id,
                                "bytes": written})
        return {"record": record_id, "bytes": written}

    def finish(self, report: dict[str, Any]) -> dict[str, Any]:
        """Record the solver's final account (kept for scoring, never trusted alone)."""
        self.report = dict(report)
        return self.report


def record_for(task: dict[str, Any], record_id: str | None) -> dict[str, Any] | None:
    if record_id is None:
        return None
    for record in task.get("records") or []:
        if record.get("record_id") == record_id:
            return dict(record)
    raise wal_store.WalError(f"{task.get('id')}: operations name unknown record {record_id!r}")


def seq_for(task: dict[str, Any], index: int) -> int:
    base = int((task.get("durability") or {}).get("seq_base", 10))
    return base + 2 * int(index)
