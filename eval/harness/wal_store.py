"""A tiny file-backed record store with a write-ahead log (WAL) and crash points.

This is **evaluator infrastructure**, not J.A.R.V.I.S. storage. It exists so the
B4W durability slice can crash a write at a byte offset, restart from the bytes
that actually reached the file, and check whether recovery keeps its promises.
Nothing here is a database: there is no server, no dependency, no key-value
engine and no claim that this layout is how a real store should be built.

Layout inside one temporary directory::

    wal.log        append-only frames: put/delete entries, then a commit frame
    records.log    one JSON line per record, written before the commit marker

A transaction writes three things in order: the entry frame (``wal_append``), the
record line (``record_write``), then the commit frame (``commit_marker``). Only a
frame pair -- entry plus matching commit -- makes a record durable. Recovery
reads the WAL from disk, stops at the first frame that cannot be verified, keeps
the entries whose transactions committed in that prefix, rebuilds ``records.log``
from them (``replay_write``) and reports what it found and what it threw away.

Recoverable states (recovery must finish and the store must be usable):

* a torn tail -- the last frame is a prefix of an encoded frame;
* a corrupt tail -- a complete frame whose checksum does not match;
* an entry with no commit marker, including one whose record line is already on
  disk: the record is not durable and its line is removed by the rebuild;
* the same transaction appended twice -- a retry after an ambiguous ack;
* a crash *during* recovery: recovery is idempotent and may be re-run.

States that must fail closed (recovery may not extend the committed set):

* damage in the middle of the log, i.e. a verifiable frame *after* an
  unverifiable one: nothing past the damage may be treated as committed;
* a commit frame whose transaction has no entry in the verifiable prefix;
* an entry kind the codec does not know;
* a WAL that does not start with the magic header, or whose bytes are not a
  sequence of frames at all.

Every byte the store writes goes through :func:`write_bytes`, which applies a
hidden :class:`CrashPlan`: when the plan's stage and record match, only the
prefix up to the plan's offset reaches the file and :class:`StoreCrashed` is
raised. The store object is then dead -- the crash is a *process* crash, so a
restart constructs a new :class:`WalStore` from the directory and reads nothing
but the files.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .write_state import COMMITTED, NOT_STARTED, PARTIALLY_APPLIED, ROLLED_BACK

MAGIC = b"JARVIS-WAL-1\n"
WAL_NAME = "wal.log"
DATA_NAME = "records.log"

#: Entry kinds a frame may carry. ``commit`` closes a transaction.
ENTRY_KINDS = ("put", "delete", "commit")
OP_KINDS = ("put", "delete")

HEADER_HEX = 8      # payload length, hex
TRAILER_HEX = 16    # sha256(payload)[:16], hex

#: Where a simulated crash may land.
CRASH_STAGES = ("wal_append", "record_write", "commit_marker", "replay_write",
                "corrupt_entry", "crash_after_commit")

#: Crash stages that reuse another stage's write activity but differ in what is
#: left behind: a corrupt entry is a complete frame with a bad checksum, and a
#: crash after commit lands once the commit frame has reached the file.
STAGE_ACTIVITY = {"corrupt_entry": "wal_append", "crash_after_commit": "commit_marker"}

#: Stages after which the interrupted transaction must not be durable.
UNCOMMITTED_STAGES = ("wal_append", "record_write", "commit_marker", "corrupt_entry")

STORE_PREFIX = "jarvis-b4w-"

#: How a damaged log is classified by recovery.
DAMAGE_NONE = None
DAMAGE_TORN = "torn"
DAMAGE_CORRUPT = "corrupt"
DAMAGE_MISSING_HEADER = "header"


class WalError(Exception):
    """A WAL or recovery invariant was violated."""


class StoreCrashed(WalError):
    """The simulated process died; the store object must not be used again."""

    def __init__(self, stage: str, record: str | None, offset: int) -> None:
        super().__init__(f"simulated crash during {stage} at byte {offset}")
        self.stage = stage
        self.record = record
        self.offset = offset


class StoreDead(WalError):
    """A store handle was used after the process it belonged to crashed."""


def canonical(payload: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, no spaces, UTF-8."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def encode_frame(entry: dict[str, Any]) -> bytes:
    """Encode one WAL frame: 8 hex length, payload, 16 hex checksum."""
    payload = canonical(entry)
    checksum = _checksum(payload)
    return f"{len(payload):0{HEADER_HEX}x}".encode("ascii") + payload + checksum.encode("ascii")


def _checksum(payload: bytes) -> str:
    import hashlib
    return hashlib.sha256(payload).hexdigest()[:TRAILER_HEX]


def frame_entry(kind: str, seq: int, txn: str, record: dict[str, Any] | None = None) -> dict[str, Any]:
    if kind not in ENTRY_KINDS:
        raise WalError(f"unknown entry kind {kind!r}")
    entry: dict[str, Any] = {"kind": kind, "seq": int(seq), "txn": str(txn)}
    if kind in OP_KINDS:
        if not isinstance(record, dict) or not record.get("record_id"):
            raise WalError(f"{kind} entry needs a record with an id")
        entry["record"] = record
    elif record is not None:
        raise WalError("a commit frame carries no record")
    return entry


def decode_frames(data: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Decode as much of a WAL as can be verified.

    Returns ``(entries, tail)`` where ``tail`` describes the first unverifiable
    position, if any: ``{"status": None}`` for a clean log, otherwise
    ``{"status": "torn"|"corrupt"|"header", "offset": int, "detail": str}``.
    """
    if not data.startswith(MAGIC):
        return [], {"status": DAMAGE_MISSING_HEADER, "offset": 0,
                    "detail": "log does not start with the WAL header"}
    body = data[len(MAGIC):]
    entries: list[dict[str, Any]] = []
    position = 0
    while position < len(body):
        if len(body) - position < HEADER_HEX:
            return entries, {"status": DAMAGE_TORN, "offset": len(MAGIC) + position,
                             "detail": "partial length header"}
        try:
            length = int(body[position:position + HEADER_HEX], 16)
        except ValueError:
            return entries, {"status": DAMAGE_CORRUPT, "offset": len(MAGIC) + position,
                             "detail": "length header is not hex"}
        frame_size = HEADER_HEX + length + TRAILER_HEX
        if length <= 0 or length > 1 << 20:
            return entries, {"status": DAMAGE_CORRUPT, "offset": len(MAGIC) + position,
                             "detail": f"implausible frame length {length}"}
        if len(body) - position < frame_size:
            return entries, {"status": DAMAGE_TORN, "offset": len(MAGIC) + position,
                             "detail": f"frame truncated ({len(body) - position} of {frame_size} bytes)"}
        payload = body[position + HEADER_HEX:position + HEADER_HEX + length]
        trailer = body[position + HEADER_HEX + length:position + frame_size]
        if trailer.decode("ascii", "replace") != _checksum(payload):
            return entries, {"status": DAMAGE_CORRUPT, "offset": len(MAGIC) + position,
                             "detail": "checksum does not match the payload"}
        try:
            entry = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return entries, {"status": DAMAGE_CORRUPT, "offset": len(MAGIC) + position,
                             "detail": f"payload is not JSON: {exc}"}
        problem = frame_problem(entry)
        if problem:
            return entries, {"status": DAMAGE_CORRUPT, "offset": len(MAGIC) + position,
                             "detail": problem}
        entries.append(entry)
        position += frame_size
    return entries, {"status": DAMAGE_NONE, "offset": None, "detail": "clean log"}


def frame_problem(entry: Any) -> str | None:
    """Why an entry is not a legal frame, or None."""
    if not isinstance(entry, dict):
        return "entry is not an object"
    kind = entry.get("kind")
    if kind not in ENTRY_KINDS:
        return f"unknown entry kind {kind!r}"
    if not isinstance(entry.get("seq"), int) or isinstance(entry.get("seq"), bool):
        return "entry has no integer seq"
    if not isinstance(entry.get("txn"), str) or not entry["txn"]:
        return "entry has no transaction id"
    if kind in OP_KINDS:
        record = entry.get("record")
        if not isinstance(record, dict) or not record.get("record_id"):
            return f"{kind} entry carries no record"
    elif entry.get("record") is not None:
        return "commit entry carries a record"
    return None


def validate_sequence(entries: list[dict[str, Any]]) -> list[str]:
    """Structural checks a declared WAL sequence must satisfy.

    Used by the fixture loader for planted entries and by the tests: sequence
    numbers strictly increase, every commit follows an entry of its own
    transaction, and a transaction commits at most once.
    """
    reasons: list[str] = []
    seen_seq: list[int] = []
    open_txns: dict[str, int] = {}
    committed: set[str] = set()
    for index, entry in enumerate(entries):
        problem = frame_problem(entry)
        if problem:
            reasons.append(f"entry {index}: {problem}")
            continue
        seq = entry["seq"]
        if seen_seq and seq <= seen_seq[-1]:
            reasons.append(f"entry {index}: seq {seq} does not increase past {seen_seq[-1]}")
        seen_seq.append(seq)
        txn = entry["txn"]
        if entry["kind"] == "commit":
            if txn in committed:
                reasons.append(f"entry {index}: transaction {txn} commits twice")
            if txn not in open_txns:
                reasons.append(f"entry {index}: commit for {txn} has no earlier entry")
            committed.add(txn)
        else:
            if txn in committed:
                reasons.append(f"entry {index}: {txn} writes after its commit")
            open_txns.setdefault(txn, seq)
    return reasons


@dataclass
class CrashPlan:
    """The evaluator's hidden crash decision for one stage of one record write."""

    stage: str
    record: str | None
    offset: int          # bytes of the targeted operation that reach the file
    target_bytes: int    # total bytes of the operation being interrupted
    attempt: int = 1
    #: ``corrupt_entry`` writes the whole frame and then flips one payload byte,
    #: so the frame is complete but fails its checksum -- a different failure from
    #: a truncated one, and recovery has to classify them separately.
    corrupt: bool = False

    def __post_init__(self) -> None:
        if self.stage not in CRASH_STAGES:
            raise WalError(f"unknown crash stage {self.stage!r}")
        if not 0 <= self.offset <= self.target_bytes:
            raise WalError(
                f"crash offset {self.offset} is outside the {self.target_bytes} bytes of the "
                f"{self.stage} operation")
        if self.stage == "crash_after_commit" and self.offset != self.target_bytes:
            raise WalError("a crash-after-commit lands once the commit frame is complete")
        if self.corrupt and self.offset != self.target_bytes:
            raise WalError("a corrupt-entry crash writes the whole frame before flipping a byte")

    @property
    def is_truncating(self) -> bool:
        return self.offset < self.target_bytes

    def describe(self) -> dict[str, Any]:
        return {"stage": self.stage, "record": self.record, "offset": self.offset,
                "target_bytes": self.target_bytes, "attempt": self.attempt,
                "truncated": self.is_truncating, "corrupt": self.corrupt}


@dataclass
class RecoveryReport:
    """What recovery found on disk and what it did about it."""

    committed: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    damage: str | None = DAMAGE_NONE
    damage_offset: int | None = None
    damage_detail: str = ""
    replayed: list[str] = field(default_factory=list)
    truncated_bytes: int = 0
    entries: int = 0
    refused: bool = False
    repaired: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "committed": sorted(self.committed),
            "discarded": sorted(self.discarded),
            "damage": self.damage,
            "damage_offset": self.damage_offset,
            "damage_detail": self.damage_detail,
            "replayed": sorted(self.replayed),
            "truncated_bytes": self.truncated_bytes,
            "entries": self.entries,
            "refused": self.refused,
            "repaired": self.repaired,
        }


def committed_entries(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split verified entries into (applied in seq order, discarded uncommitted).

    A transaction is committed when a ``commit`` frame for it appears after its
    entry. Entries are de-duplicated by ``(txn, seq)`` so a retried write is
    applied once, and an entry that arrives after its own commit is refused.
    """
    commits: dict[str, list[int]] = {}
    for entry in entries:
        if entry["kind"] == "commit":
            commits.setdefault(entry["txn"], []).append(entry["seq"])
    seen: set[tuple[str, int]] = set()
    applied: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for entry in entries:
        if entry["kind"] == "commit":
            continue
        key = (entry["txn"], entry["seq"])
        if key in seen:
            continue
        seen.add(key)
        commit_seqs = commits.get(entry["txn"]) or []
        if any(seq > entry["seq"] for seq in commit_seqs):
            applied.append(entry)
        else:
            discarded.append(entry)
    applied.sort(key=lambda item: item["seq"])
    return applied, discarded


def apply_entries(applied: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Rebuild (live records, tombstones) from committed entries in seq order.

    A tombstone is authoritative: once a committed ``delete`` names a record, a
    later ``put`` for the same id -- a stale retry, say -- may not resurrect it.
    That is the declared invariant this store keeps, and it is why the
    resurrection counter needs its own gate.
    """
    records: dict[str, dict[str, Any]] = {}
    tombstoned = {str(entry["record"]["record_id"]) for entry in applied
                  if entry["kind"] == "delete"}
    for entry in applied:
        rid = str(entry["record"]["record_id"])
        if entry["kind"] == "put" and rid not in tombstoned:
            records[rid] = entry["record"]
    for rid in tombstoned:
        records.pop(rid, None)
    return records, tombstoned


def write_bytes(path: Path, data: bytes, *, stage: str, record: str | None,
                plan: CrashPlan | None, written: int) -> int:
    """Write ``data`` and apply the crash plan. Returns bytes written.

    The plan counts bytes *within the targeted operation*, so ``written`` is how
    many bytes of this operation have already reached the file. When the plan
    lands inside this chunk, only the prefix is written and flushed before the
    crash: the file really holds a torn write, which is what recovery must cope
    with.
    """
    before = path.stat().st_size if path.exists() else 0
    chunk = data
    crashed = False
    corrupt = False
    # A corrupt entry and a crash-after-commit reuse another stage's write and
    # differ only in what they leave behind.
    planned_stage = (STAGE_ACTIVITY.get(plan.stage, plan.stage)
                     if plan is not None else None)
    if (plan is not None and plan.record == record and planned_stage == stage
            and written <= plan.offset <= written + len(data)):
        chunk = data[:plan.offset - written]
        crashed = True
        corrupt = plan.corrupt
    with open(path, "ab") as handle:
        handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    if crashed and corrupt:
        # Flip one payload byte in place: the frame is complete, its checksum is
        # not, and nothing in memory remembers the original.
        target = before + HEADER_HEX
        with open(path, "r+b") as handle:
            handle.seek(target)
            original = handle.read(1)
            handle.seek(target)
            handle.write(bytes([original[0] ^ 0x01]) if original else b"\x00")
            handle.flush()
            os.fsync(handle.fileno())
    if crashed:
        raise StoreCrashed(stage, record, plan.offset)
    return len(chunk)


class WalStore:
    """One process's handle on a WAL-backed store directory.

    The handle owns open state only for the duration of one "process". After
    :class:`StoreCrashed` the handle is dead: every method raises
    :class:`StoreDead`, so a restart has to construct a new handle and read the
    files, exactly as a real process would.
    """

    def __init__(self, root: Path | str, *, plan: CrashPlan | None = None,
                 crash_activities: list[dict[str, Any]] | None = None) -> None:
        self.root = _safe_root(root)
        self.wal_path = self.root / WAL_NAME
        self.data_path = self.root / DATA_NAME
        self.plan = plan
        self.dead = False
        #: transaction -> record id, so a commit frame can be matched by record.
        self.txn_records: dict[str, str] = {}
        self.crashes: list[dict[str, Any]] = []
        #: Every write this handle performed, in order, for the audit trail.
        self.activities = crash_activities if crash_activities is not None else []

    # -- lifecycle --------------------------------------------------------
    def _check_alive(self) -> None:
        if self.dead:
            raise StoreDead("this store handle belongs to a crashed process")

    def crash(self, exc: StoreCrashed) -> None:
        """Record the crash and kill the handle."""
        self.dead = True
        self.crashes.append({"stage": exc.stage, "record": exc.record,
                             "offset": exc.offset})
        self.activities.append({"event": "crash", "stage": exc.stage,
                                "record": exc.record, "offset": exc.offset})

    def close(self) -> None:
        self.dead = True

    # -- write path -------------------------------------------------------
    def append_entry(self, kind: str, seq: int, txn: str,
                     record: dict[str, Any] | None = None) -> int:
        """Append one entry frame to the WAL (stage ``wal_append``)."""
        self._check_alive()
        frame = encode_frame(frame_entry(kind, seq, txn, record))
        stage = "wal_append" if kind in OP_KINDS else "commit_marker"
        if record is not None:
            self.txn_records[txn] = str(record["record_id"])
        target = (str(record["record_id"]) if record is not None
                  else self.txn_records.get(txn, txn))
        written = write_bytes(self.wal_path, frame, stage=stage, record=target,
                              plan=self.plan, written=0)
        self.activities.append({"event": "wal_append", "kind": kind, "seq": seq,
                                "txn": txn, "bytes": written})
        return written

    def write_record(self, record: dict[str, Any]) -> int:
        """Append one record line (stage ``record_write``)."""
        self._check_alive()
        line = canonical(record) + b"\n"
        written = write_bytes(self.data_path, line, stage="record_write",
                              record=str(record["record_id"]), plan=self.plan, written=0)
        self.activities.append({"event": "record_write",
                                "record": record["record_id"], "bytes": written})
        return written

    def write_operation(self, op: dict[str, Any], seq: int) -> dict[str, Any]:
        """Write one transaction: entry frame, record line, commit frame."""
        self._check_alive()
        record = op.get("record")
        txn = str(op["txn"])
        self.append_entry(op["kind"], seq, txn, record)
        if op["kind"] == "put":
            self.write_record(record)
        self.append_entry("commit", seq + 1, txn)
        return {"txn": txn, "seq": seq, "kind": op["kind"],
                "record": (record or {}).get("record_id")}

    def plant(self, entries: list[dict[str, Any]]) -> int:
        """Write fixture-declared history frames before the run starts."""
        self._check_alive()
        written = 0
        for entry in entries:
            frame = encode_frame(entry)
            written += write_bytes(self.wal_path, frame, stage="plant",
                                   record=None, plan=None, written=0)
        self.activities.append({"event": "plant", "entries": len(entries)})
        return written

    # -- read path --------------------------------------------------------
    def read_wal(self) -> bytes:
        self._check_alive()
        return self.wal_path.read_bytes() if self.wal_path.exists() else MAGIC

    def read_records(self) -> list[dict[str, Any]]:
        self._check_alive()
        if not self.data_path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self.data_path.read_bytes().split(b"\n"):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"_unreadable": line[:40].decode("utf-8", "replace")})
        return out

    def recover(self, *, replay_entries: int = 1, forget: list[str] | None = None,
                ignore_tombstones: bool = False, rebuild: bool = True) -> RecoveryReport:
        """Rebuild the store from the WAL, then rewrite ``records.log``.

        The keyword arguments are **planted-regression knobs**, not options a
        correct engine would use: the reference path calls ``recover()`` with no
        arguments. ``replay_entries`` applies the committed entries more than
        once, ``forget`` compacts the log without the named records (losing them
        outright), ``ignore_tombstones`` lets a stale put resurrect a deleted
        record, and ``rebuild=False`` leaves the record file as the crash left it.
        """
        self._check_alive()
        data = self.read_wal()
        entries, tail = decode_frames(data)
        if forget:
            forgotten = {str(rid) for rid in forget}
            # Whole transactions: the entry frame and its commit marker go together,
            # so no orphaned commit can vouch for a record that is no longer there.
            forgotten_txns = {entry["txn"] for entry in entries
                              if str((entry.get("record") or {}).get("record_id"))
                              in forgotten}
            kept = [entry for entry in entries if entry["txn"] not in forgotten_txns]
            if len(kept) != len(entries):
                # Planted regression only: a recovery that compacts the log and
                # forgets records that were already committed, so the loss is real
                # on disk and not just missing from the rebuilt record file.
                with open(self.wal_path, "wb") as handle:
                    handle.write(MAGIC + b"".join(encode_frame(entry) for entry in kept))
                    handle.flush()
                    os.fsync(handle.fileno())
                data = self.read_wal()
                entries, tail = decode_frames(data)
                self.activities.append({"event": "compact_log", "forgot": sorted(forgotten)})
        applied, discarded = committed_entries(entries)
        if ignore_tombstones:
            # Planted regression only: replay puts without honouring deletions, so
            # a stale write resurrects a record the store had removed.
            tombstones = {str(entry["record"]["record_id"]) for entry in applied
                          if entry["kind"] == "delete"}
            records, _ = apply_entries([entry for entry in applied if entry["kind"] != "delete"])
            deleted = tombstones
        else:
            records, deleted = apply_entries(applied)
        report = RecoveryReport(
            committed=sorted(records),
            discarded=sorted({str(entry["record"]["record_id"]) for entry in discarded}),
            damage=tail["status"], damage_offset=tail["offset"],
            damage_detail=tail["detail"], entries=len(entries),
            truncated_bytes=len(data) - (tail["offset"] or len(data))
            if tail["status"] else 0,
            refused=tail["status"] in (DAMAGE_MISSING_HEADER,),
        )

        # Discard the unverifiable tail so the log can be appended to again. This is
        # the repair step: the bytes after the last verifiable frame are dropped,
        # and nothing that followed them is trusted. A log that does not even start
        # with the header is refused instead -- there is nothing to cut back to.
        if tail["status"] in (DAMAGE_TORN, DAMAGE_CORRUPT):
            good_end = len(MAGIC) + sum(len(encode_frame(entry)) for entry in entries)
            with open(self.wal_path, "r+b") as handle:
                handle.truncate(good_end)
                handle.flush()
                os.fsync(handle.fileno())
            report.truncated_bytes = len(data) - good_end
            report.repaired = True
            self.activities.append({"event": "truncate_tail", "bytes": report.truncated_bytes,
                                    "kept_frames": len(entries)})

        # The rebuild is itself crashable: it rewrites records.log from the map.
        if not rebuild:
            self.activities.append({"event": "recover_no_rebuild"})
            return report
        self.data_path.write_bytes(b"")
        for _ in range(max(1, int(replay_entries))):
            for rid in sorted(records):
                line = canonical(records[rid]) + b"\n"
                write_bytes(self.data_path, line, stage="replay_write", record=rid,
                            plan=self.plan, written=0)
                report.replayed.append(rid)
        self.activities.append({"event": "recover", "committed": sorted(records),
                                "replay_entries": int(replay_entries),
                                "forgot": sorted(forget or []),
                                "ignore_tombstones": bool(ignore_tombstones),
                                "discarded": report.discarded,
                                "damage": report.damage, "entries": len(entries),
                                "replay_entries": int(replay_entries)})
        report.replayed = sorted(set(report.replayed))
        return report


def _safe_root(root: Path | str) -> Path:
    """Resolve a store directory and refuse anything that is not a plain temp dir."""
    path = Path(root)
    if ".." in path.parts:
        raise WalError(f"{path}: store directories may not traverse upward")
    if not path.exists():
        raise WalError(f"{path}: store directory does not exist")
    if path.is_symlink():
        raise WalError(f"{path}: store root may not be a symlink")
    if not path.is_dir():
        raise WalError(f"{path}: store root is not a directory")
    resolved = path.resolve()
    if not resolved.name.startswith(STORE_PREFIX):
        raise WalError(
            f"{resolved.name}: a store directory must be created by the harness "
            f"(name prefix {STORE_PREFIX!r})")
    return resolved


def create_store(root: Path | str, *, plan: CrashPlan | None = None) -> WalStore:
    """Initialise a fresh store directory: header frame, empty record file."""
    path = Path(root)
    if path.exists():
        raise WalError(f"{path}: a store directory already exists here")
    path.mkdir(parents=True, exist_ok=False)
    if not path.name.startswith(STORE_PREFIX):
        raise WalError(f"{path.name}: a store directory must be created by the harness")
    path.joinpath(WAL_NAME).write_bytes(MAGIC)
    path.joinpath(DATA_NAME).write_bytes(b"")
    store = WalStore(path, plan=plan)
    store.activities.append({"event": "create", "root": path.name})
    return store


def store_audit(root: Path | str) -> dict[str, Any]:
    """Read a store directory from disk and describe it, without a live handle.

    This is what the scorer looks at: the files on disk after the run, not the
    solver's account of them.
    """
    path = _safe_root(root)
    wal = path.joinpath(WAL_NAME)
    data = wal.read_bytes() if wal.exists() else b""
    entries, tail = decode_frames(data)
    applied, discarded = committed_entries(entries)
    records, deleted = apply_entries(applied)
    lines = []
    if path.joinpath(DATA_NAME).exists():
        for raw in path.joinpath(DATA_NAME).read_bytes().split(b"\n"):
            if raw.strip():
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError:
                    lines.append({"_unreadable": raw[:40].decode("utf-8", "replace")})
    return {
        "root_name": path.name,
        "wal_bytes": len(data),
        "entries": len(entries),
        "damage": tail["status"],
        "damage_offset": tail["offset"],
        "damage_detail": tail["detail"],
        "committed": sorted(records),
        "tombstoned": sorted(deleted),
        "discarded": sorted({str(entry["record"]["record_id"]) for entry in discarded}),
        "lines": lines,
        "line_ids": [str(line.get("record_id")) for line in lines
                     if isinstance(line, dict) and line.get("record_id")],
        "unreadable_lines": len([line for line in lines if "_unreadable" in line]),
        "commit_frames": len([entry for entry in entries if entry["kind"] == "commit"]),
    }


def remove_store(root: Path | str) -> bool:
    """Delete a store directory that this module created. Refuses anything else."""
    path = Path(root)
    resolved = path.resolve()
    if resolved.name != path.name or not path.name.startswith(STORE_PREFIX):
        raise WalError(f"{path}: refusing to remove a directory the harness did not create")
    if path.is_symlink():
        raise WalError(f"{path}: refusing to remove through a symlink")
    if path.exists():
        shutil.rmtree(path)
        return True
    return False


def record_state_in_store(record_id: str, audit: dict[str, Any]) -> str:
    """Where one record ended up, in the four write states plus absence."""
    if record_id in audit["tombstoned"]:
        return ROLLED_BACK if record_id in audit["discarded"] else COMMITTED
    if record_id in audit["line_ids"] and record_id not in audit["committed"]:
        return PARTIALLY_APPLIED
    if record_id in audit["committed"]:
        return COMMITTED
    return NOT_STARTED
