"""Long-term memory: versioned, provenance-tracked, correctable, and forgettable.

Data model (table ``memory_items``; see migrations.py)
- ``kind``: fact | preference | idea | note | decision | procedure | self
- ``epistemic``: how it is known (contracts.EpistemicStatus) — a stored memory is
  never automatically "true"; a remembered hypothesis stays a hypothesis.
- ``confidence``: ordinal, uncalibrated label (contracts.Confidence).
- provenance: ``source`` (user | tool:<name> | model:<provider> | system | import:v0.2),
  ``source_event_id`` (the observation that produced it), ``evidence_json``.
- lifecycle: ``status`` active | superseded | retracted | forgotten, with a
  supersession chain (``supersedes_id`` / ``superseded_by_id``) and validity
  interval (``valid_from`` / ``valid_to``). Corrections never overwrite rows.

Write rules (deterministic; contrast with Mem0's LLM-chosen ADD/UPDATE/DELETE/NOOP)
- No active item with the same (category, key): ADD.
- Same normalised value: NOOP (refreshes ``last_verified_at``).
- Different value, same source class, or an explicit user correction: SUPERSEDE
  (old row kept as history with ``valid_to``; new row links to it).
- Different value from a *different* source class: CONFLICT. Both stay active and
  a ``memory_conflicts`` row is opened; retrieval flags both. A model-sourced
  memory can never supersede a user-sourced one. Recency alone does not decide truth.

Retrieval: FTS5/BM25 relevance blended with recency, epistemic status and scope
(weights documented in RANK_WEIGHTS; evaluated in EXP-001). Superseded, retracted
and forgotten items are excluded from normal retrieval.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .contracts import (Confidence, EpistemicStatus, MemoryHit, MemoryKind, MemoryQuery, MemoryStatus,
                        Sensitivity)
from .erasure import backups_possibly_containing, erase_traces
from .redact import contains_secret
from .search import fts_query, like_score, tokenize
from .util import age_days, bounded, dumps, loads, normalise_key, to_iso

if TYPE_CHECKING:  # pragma: no cover
    from .db import Store

MAX_VALUE = 4000
FORGOTTEN_VALUE = "[forgotten]"
SENSITIVITY_ORDER = {"normal": 0, "personal": 1, "sensitive": 2}
SOURCE_AUTHORITY = {"user": 3, "tool": 2, "system": 2, "import": 2, "model": 1}

# Hand-set ranking weights (heuristic, not learned). EXP-001 compares them with
# relevance-only ranking on a fixed evaluation set.
RANK_WEIGHTS = {"relevance": 0.70, "recency": 0.15, "status": 0.10, "scope": 0.05}
RECENCY_HALF_LIFE_DAYS = 30.0
STATUS_WEIGHT = {"verified": 1.0, "observation": 0.85, "source_claim": 0.8, "belief": 0.8,
                 "hypothesis": 0.6, "inference": 0.5, "prediction": 0.4, "unknown": 0.2}
STALE_AFTER_DAYS = {"volatile": 7.0, "changing": 90.0}

# Allowed epistemic transitions and the evidence each target requires.
TRANSITIONS: dict[str, set[str]] = {
    "hypothesis": {"belief", "verified", "inference", "unknown"},
    "source_claim": {"belief", "verified", "unknown"},
    "inference": {"belief", "verified", "hypothesis", "unknown"},
    "observation": {"verified", "unknown"},
    "prediction": {"verified", "unknown"},
    "belief": {"verified", "hypothesis", "unknown"},
    "verified": {"belief", "unknown"},
    "unknown": {"hypothesis", "belief", "verified"},
}
VERIFYING_EVENT_KINDS = {"validation.passed", "operation.verified", "user.confirmed"}


class MemoryError_(ValueError):
    """Raised for invalid memory operations (subclass of ValueError for v0.2 compatibility)."""


class EvidenceRequired(MemoryError_):
    pass


@dataclass(frozen=True)
class WriteResult:
    action: str                 # added | superseded | unchanged | conflict
    item: dict[str, Any]
    previous_id: int | None = None
    conflict_id: int | None = None
    conflicting_ids: tuple[int, ...] = ()


def source_class(source: str) -> str:
    return (source or "user").split(":", 1)[0]


def default_epistemics(kind: str, source: str) -> tuple[str, str]:
    cls = source_class(source)
    if kind == MemoryKind.IDEA.value:
        return EpistemicStatus.HYPOTHESIS.value, Confidence.LOW.value
    if cls == "user":
        return EpistemicStatus.SOURCE_CLAIM.value, Confidence.HIGH.value
    if cls == "tool":
        return EpistemicStatus.OBSERVATION.value, Confidence.HIGH.value
    if cls == "model":
        return EpistemicStatus.INFERENCE.value, Confidence.LOW.value
    return EpistemicStatus.SOURCE_CLAIM.value, Confidence.MEDIUM.value


def _same_value(a: str, b: str) -> bool:
    return " ".join(a.lower().split()).strip(" .") == " ".join(b.lower().split()).strip(" .")


class MemoryStore:
    def __init__(self, store: "Store"):
        self.store = store

    # ------------------------------------------------------------------ writes
    def remember(self, value: str, *, key: str | None = None, category: str = "general",
                 kind: str = MemoryKind.FACT.value, source: str = "user", epistemic: str | None = None,
                 confidence: str | None = None, sensitivity: str = Sensitivity.NORMAL.value,
                 project_id: int | None = None, goal_id: int | None = None,
                 source_event_id: int | None = None, volatility: str = "stable",
                 valid_from: str | None = None, correction: bool = False,
                 session_id: str | None = None, cycle_id: str | None = None,
                 conn: sqlite3.Connection | None = None) -> WriteResult:
        value = (value or "").strip()
        if not value or len(value) > MAX_VALUE:
            raise MemoryError_(f"Memory value must contain 1-{MAX_VALUE} characters")
        if contains_secret(value) or (key and contains_secret(key)):
            raise MemoryError_("Refusing to store credential-like content in memory")
        key_norm = normalise_key(key) if key else None
        if key is not None and (not key_norm or len(key_norm) > 160):
            raise MemoryError_("Memory key must contain 1-160 characters")
        category = (category or "general").strip()[:80] or "general"
        if kind not in {k.value for k in MemoryKind}:
            raise MemoryError_(f"Unknown memory kind: {kind}")
        if sensitivity not in SENSITIVITY_ORDER:
            raise MemoryError_(f"Unknown sensitivity: {sensitivity}")
        if volatility not in {"stable", "changing", "volatile"}:
            raise MemoryError_(f"Unknown volatility: {volatility}")
        default_status, default_conf = default_epistemics(kind, source)
        epistemic = epistemic or default_status
        confidence = confidence or default_conf
        if epistemic not in {e.value for e in EpistemicStatus}:
            raise MemoryError_(f"Unknown epistemic status: {epistemic}")
        if confidence not in {c.value for c in Confidence}:
            raise MemoryError_(f"Unknown confidence label: {confidence}")
        if epistemic in {"verified", "belief"}:
            raise EvidenceRequired("New memories cannot start as belief/verified; use promote() with evidence")

        def _write(db: sqlite3.Connection) -> WriteResult:
            now = to_iso(self.store.now())
            existing = []
            if key_norm:
                existing = db.execute(
                    "SELECT * FROM memory_items WHERE category=? COLLATE NOCASE AND key=? AND status='active' "
                    "ORDER BY id", (category, key_norm)).fetchall()
            else:
                existing = [r for r in db.execute(
                    "SELECT * FROM memory_items WHERE category=? COLLATE NOCASE AND key IS NULL AND kind=? "
                    "AND status='active' ORDER BY id DESC LIMIT 200", (category, kind)).fetchall()
                    if _same_value(r["value"], value)]
            for row in existing:
                if _same_value(row["value"], value):
                    db.execute("UPDATE memory_items SET last_verified_at=?, updated_at=? WHERE id=?",
                               (now, now, row["id"]))
                    self.store.events.append("memory.unchanged", actor=source_class(source), target=f"memory:{row['id']}",
                                             memory_id=row["id"], session_id=session_id, cycle_id=cycle_id,
                                             caused_by=source_event_id, conn=db)
                    return WriteResult("unchanged", self._get(db, row["id"]))
            new_cls = source_class(source)
            supersede = [r for r in existing if source_class(r["source"]) == new_cls or (correction and new_cls == "user")]
            if new_cls == "model":  # a model can only ever revise its own earlier inferences
                supersede = [r for r in supersede if source_class(r["source"]) == "model"]
            superseded_ids = {r["id"] for r in supersede}
            conflicting = [r for r in existing if r["id"] not in superseded_ids]
            item_id = int(db.execute(
                "INSERT INTO memory_items(kind, category, key, value, epistemic, confidence, source, source_event_id, "
                "project_id, goal_id, sensitivity, status, volatility, valid_from, created_at, updated_at, "
                "last_verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind, category, key_norm, value, epistemic, confidence, source, source_event_id, project_id, goal_id,
                 sensitivity, MemoryStatus.ACTIVE.value, volatility, valid_from or now, now, now, now)).lastrowid)
            previous_id = None
            for old in supersede:
                reason = "correction" if correction else "update"
                db.execute("UPDATE memory_items SET status='superseded', superseded_by_id=?, valid_to=?, "
                           "change_reason=?, updated_at=? WHERE id=?", (item_id, now, reason, now, old["id"]))
                db.execute("UPDATE memory_items SET supersedes_id=? WHERE id=?", (old["id"], item_id))
                previous_id = old["id"]
                self.store.events.append("memory.superseded", actor=new_cls, target=f"memory:{old['id']}",
                                         memory_id=item_id, session_id=session_id, cycle_id=cycle_id,
                                         caused_by=source_event_id,
                                         payload={"old_id": old["id"], "new_id": item_id, "reason": reason}, conn=db)
            conflict_id = None
            for other in conflicting:
                conflict_id = int(db.execute(
                    "INSERT INTO memory_conflicts(item_a, item_b, detected_at) VALUES (?,?,?)",
                    (other["id"], item_id, now)).lastrowid)
                self.store.events.append("memory.conflict_detected", actor="jarvis", target=f"memory:{item_id}",
                                         memory_id=item_id, session_id=session_id, cycle_id=cycle_id,
                                         caused_by=source_event_id,
                                         payload={"conflict_id": conflict_id, "existing_id": other["id"],
                                                  "existing_source": other["source"], "new_source": source}, conn=db)
            action = "superseded" if supersede else ("conflict" if conflicting else "added")
            self.store.events.append("memory.created", actor=new_cls, target=f"memory:{item_id}", memory_id=item_id,
                                     session_id=session_id, cycle_id=cycle_id, caused_by=source_event_id,
                                     sensitivity="normal" if sensitivity == "normal" else "personal",
                                     payload={"kind": kind, "category": category, "key": key_norm,
                                              "epistemic": epistemic, "source": source, "action": action}, conn=db)
            return WriteResult(action, self._get(db, item_id), previous_id, conflict_id,
                               tuple(r["id"] for r in conflicting))

        if conn is not None:
            return _write(conn)
        with self.store.write() as db:
            return _write(db)

    def correct(self, item_id: int, new_value: str, *, reason: str = "", source: str = "user",
                source_event_id: int | None = None, session_id: str | None = None,
                cycle_id: str | None = None) -> WriteResult:
        """Explicitly supersede a specific active item, preserving it as history."""
        with self.store.write() as db:
            old = db.execute("SELECT * FROM memory_items WHERE id=?", (item_id,)).fetchone()
            if not old:
                raise MemoryError_(f"Unknown memory id: {item_id}")
            if old["status"] != "active":
                raise MemoryError_(f"Memory {item_id} is {old['status']}; only active memories can be corrected")
            if source_class(source) == "model" and source_class(old["source"]) != "model":
                raise MemoryError_("A model cannot correct a memory provided by another source")
            new_value = (new_value or "").strip()
            if not new_value or len(new_value) > MAX_VALUE:
                raise MemoryError_(f"Memory value must contain 1-{MAX_VALUE} characters")
            if contains_secret(new_value):
                raise MemoryError_("Refusing to store credential-like content in memory")
            if _same_value(old["value"], new_value):
                return WriteResult("unchanged", dict(old))
            now = to_iso(self.store.now())
            epistemic, confidence = default_epistemics(old["kind"], source)
            new_id = int(db.execute(
                "INSERT INTO memory_items(kind, category, key, value, epistemic, confidence, source, source_event_id, "
                "project_id, goal_id, sensitivity, status, supersedes_id, change_reason, volatility, valid_from, "
                "created_at, updated_at, last_verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (old["kind"], old["category"], old["key"], new_value, epistemic, confidence, source, source_event_id,
                 old["project_id"], old["goal_id"], old["sensitivity"], "active", item_id, "correction",
                 old["volatility"], now, now, now, now)).lastrowid)
            db.execute("UPDATE memory_items SET status='superseded', superseded_by_id=?, valid_to=?, "
                       "change_reason='correction', updated_at=? WHERE id=?", (new_id, now, now, item_id))
            self.store.events.append("memory.corrected", actor=source_class(source), target=f"memory:{item_id}",
                                     memory_id=new_id, session_id=session_id, cycle_id=cycle_id,
                                     caused_by=source_event_id,
                                     payload={"old_id": item_id, "new_id": new_id, "reason": bounded(reason, 300)},
                                     conn=db)
            self._resolve_conflicts_for(db, item_id, f"superseded by correction #{new_id}")
            return WriteResult("superseded", self._get(db, new_id), item_id)

    def retract(self, item_id: int, *, reason: str, session_id: str | None = None,
                cycle_id: str | None = None) -> dict[str, Any]:
        """Mark an item as wrong without a replacement (kept for history)."""
        with self.store.write() as db:
            row = db.execute("SELECT * FROM memory_items WHERE id=?", (item_id,)).fetchone()
            if not row or row["status"] != "active":
                raise MemoryError_(f"Memory {item_id} is not active")
            now = to_iso(self.store.now())
            db.execute("UPDATE memory_items SET status='retracted', valid_to=?, change_reason=?, updated_at=? "
                       "WHERE id=?", (now, bounded(reason or "retracted", 300), now, item_id))
            self.store.events.append("memory.retracted", actor="user", target=f"memory:{item_id}", memory_id=item_id,
                                     session_id=session_id, cycle_id=cycle_id, payload={"reason": bounded(reason, 300)},
                                     conn=db)
            self._resolve_conflicts_for(db, item_id, "retracted")
            return self._get(db, item_id)

    def forget(self, item_id: int, *, whole_chain: bool = True, wipe_source: bool = True,
               session_id: str | None = None, cycle_id: str | None = None) -> dict[str, Any]:
        """Irreversibly erase a memory's content and the copies JARVIS made of it.

        What is erased, redacted or retained, and what cannot be reached, is defined in
        :mod:`jarvis.erasure`. All database changes (tombstones, linked turns, journal and
        derived copies, FTS segment merge) commit in one transaction; the WAL is truncated
        afterwards. The report states what was erased and what may remain: user turns that
        merely mention the subject, backups written before the deletion, and a WAL that
        could not be truncated because another connection was open. Deletion inside the
        database is logical plus overwrite (``secure_delete``); erasure from the physical
        storage medium is not guaranteed.
        """
        with self.store.write() as db:
            row = db.execute("SELECT * FROM memory_items WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise MemoryError_(f"Unknown memory id: {item_id}")
            ids = self._chain_ids(db, item_id) if whole_chain else [item_id]
            versions = [dict(r) for r in db.execute(
                f"SELECT * FROM memory_items WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id", ids)]
            now = to_iso(self.store.now())
            for mid in ids:
                db.execute("UPDATE memory_items SET value=?, key=NULL, status='forgotten', evidence_json='[]', "
                           "change_reason='forgotten', valid_to=coalesce(valid_to, ?), updated_at=? WHERE id=?",
                           (FORGOTTEN_VALUE, now, now, mid))
                self._resolve_conflicts_for(db, mid, "forgotten")
            report = erase_traces(db, self.store.events, ids, versions, now=now, wipe_user_turns=wipe_source)
            self.store.events.append(
                "memory.forgotten", actor="user", target=f"memory:{item_id}", session_id=session_id,
                cycle_id=cycle_id, conn=db,
                payload={"ids": ids, **{k: report[k] for k in (
                    "source_events_wiped", "linked_turns_wiped", "linked_records_scrubbed",
                    "derived_records_scrubbed", "operations_scrubbed", "proposals_cancelled", "residue")}})
            self.store.optimize_search_indexes(db)
        storage = self.store.checkpoint()
        earliest = min((v["created_at"] for v in versions if v.get("created_at")), default=None)
        return {"forgotten_ids": ids, **report,
                "storage": {"fts_segments_merged": self.store.fts5, "wal_truncated": not storage["checkpoint_busy"]},
                "backups_may_contain": backups_possibly_containing(self.store.backup_dir, earliest)}

    def promote(self, item_id: int, to_status: str, *, evidence_event_ids: list[int],
                session_id: str | None = None, cycle_id: str | None = None) -> dict[str, Any]:
        """Change epistemic status, enforcing allowed transitions and evidence requirements."""
        with self.store.write() as db:
            row = db.execute("SELECT * FROM memory_items WHERE id=?", (item_id,)).fetchone()
            if not row or row["status"] != "active":
                raise MemoryError_(f"Memory {item_id} is not active")
            current = row["epistemic"]
            if to_status not in TRANSITIONS.get(current, set()):
                raise MemoryError_(f"Transition {current} -> {to_status} is not allowed")
            events = [db.execute("SELECT id, kind FROM events WHERE id=?", (e,)).fetchone() for e in evidence_event_ids]
            if any(e is None for e in events):
                raise EvidenceRequired("Evidence must reference recorded events")
            if to_status == "verified" and not any(e["kind"] in VERIFYING_EVENT_KINDS for e in events):
                raise EvidenceRequired("Promotion to 'verified' requires a validation, verified operation, "
                                       "or explicit user confirmation event")
            if to_status == "belief" and not events:
                raise EvidenceRequired("Promotion to 'belief' requires at least one evidence event")
            evidence = sorted(set(loads(row["evidence_json"], []) + list(evidence_event_ids)))
            now = to_iso(self.store.now())
            confidence = "high" if to_status == "verified" else row["confidence"]
            db.execute("UPDATE memory_items SET epistemic=?, confidence=?, evidence_json=?, last_verified_at=?, "
                       "updated_at=? WHERE id=?", (to_status, confidence, dumps(evidence), now, now, item_id))
            self.store.events.append("memory.status_changed", actor="jarvis", target=f"memory:{item_id}",
                                     memory_id=item_id, session_id=session_id, cycle_id=cycle_id,
                                     payload={"from": current, "to": to_status, "evidence": evidence}, conn=db)
            return self._get(db, item_id)

    def resolve_conflict(self, conflict_id: int, *, keep_id: int, reason: str = "") -> dict[str, Any]:
        with self.store.write() as db:
            c = db.execute("SELECT * FROM memory_conflicts WHERE id=?", (conflict_id,)).fetchone()
            if not c or c["status"] != "open":
                raise MemoryError_(f"Conflict {conflict_id} is not open")
            if keep_id not in (c["item_a"], c["item_b"]):
                raise MemoryError_("keep_id must be one of the conflicting items")
            drop_id = c["item_b"] if keep_id == c["item_a"] else c["item_a"]
            now = to_iso(self.store.now())
            db.execute("UPDATE memory_items SET status='superseded', superseded_by_id=?, valid_to=?, "
                       "change_reason='conflict_resolution', updated_at=? WHERE id=? AND status='active'",
                       (keep_id, now, now, drop_id))
            event_id = self.store.events.append("memory.conflict_resolved", actor="user", target=f"conflict:{conflict_id}",
                                                memory_id=keep_id, payload={"kept": keep_id, "superseded": drop_id,
                                                                            "reason": bounded(reason, 300)}, conn=db)
            db.execute("UPDATE memory_conflicts SET status='resolved', resolution=?, resolved_at=?, resolved_event_id=? "
                       "WHERE id=?", (f"kept {keep_id}", now, event_id, conflict_id))
            return dict(db.execute("SELECT * FROM memory_conflicts WHERE id=?", (conflict_id,)).fetchone())

    def _resolve_conflicts_for(self, db: sqlite3.Connection, item_id: int, resolution: str) -> None:
        db.execute("UPDATE memory_conflicts SET status='resolved', resolution=?, resolved_at=? "
                   "WHERE status='open' AND (item_a=? OR item_b=?)",
                   (resolution, to_iso(self.store.now()), item_id, item_id))

    def touch(self, ids: list[int]) -> None:
        """Record that items were placed in a cycle's working context (for later evaluation)."""
        if not ids:
            return
        now = to_iso(self.store.now())
        with self.store.write() as db:
            db.executemany("UPDATE memory_items SET last_accessed_at=?, access_count=access_count+1 WHERE id=?",
                           [(now, i) for i in ids])

    def purge_expired(self) -> int:
        now = to_iso(self.store.now())
        with self.store.read() as db:
            ids = [r[0] for r in db.execute(
                "SELECT id FROM memory_items WHERE expires_at IS NOT NULL AND expires_at <= ? AND status <> 'forgotten'",
                (now,))]
        for mid in ids:
            self.forget(mid, whole_chain=False)
        return len(ids)

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _get(db: sqlite3.Connection, item_id: int) -> dict[str, Any]:
        row = db.execute("SELECT * FROM memory_items WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else {}

    def get(self, item_id: int) -> dict[str, Any] | None:
        with self.store.read() as db:
            item = self._get(db, item_id)
        return item or None

    @staticmethod
    def _chain_ids(db: sqlite3.Connection, item_id: int) -> list[int]:
        """All versions linked to ``item_id`` through supersession, oldest first."""
        seen: set[int] = set()
        frontier = [item_id]
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            row = db.execute("SELECT supersedes_id, superseded_by_id FROM memory_items WHERE id=?", (current,)).fetchone()
            if not row:
                continue
            for linked in (row["supersedes_id"], row["superseded_by_id"]):
                if linked and linked not in seen:
                    frontier.append(linked)
            for r in db.execute("SELECT id FROM memory_items WHERE supersedes_id=? OR superseded_by_id=?",
                                (current, current)):
                if r["id"] not in seen:
                    frontier.append(r["id"])
        return sorted(seen)

    def history(self, item_id: int) -> list[dict[str, Any]]:
        with self.store.read() as db:
            return [self._get(db, i) for i in self._chain_ids(db, item_id)]

    def list(self, *, status: str = "active", limit: int = 100, category: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        sql, args = "SELECT * FROM memory_items WHERE status=?", [status]
        if category:
            sql += " AND category=? COLLATE NOCASE"
            args.append(category)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        with self.store.read() as db:
            return [dict(r) for r in db.execute(sql, (*args, limit))]

    def open_conflicts(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.store.read() as db:
            rows = db.execute(
                "SELECT c.*, a.value AS a_value, a.source AS a_source, b.value AS b_value, b.source AS b_source "
                "FROM memory_conflicts c JOIN memory_items a ON a.id=c.item_a JOIN memory_items b ON b.id=c.item_b "
                "WHERE c.status='open' ORDER BY c.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def find_active_by_key(self, key: str, category: str | None = None) -> list[dict[str, Any]]:
        key_norm = normalise_key(key)
        sql, args = "SELECT * FROM memory_items WHERE key=? AND status='active'", [key_norm]
        if category:
            sql += " AND category=? COLLATE NOCASE"
            args.append(category)
        with self.store.read() as db:
            return [dict(r) for r in db.execute(sql + " ORDER BY id DESC", args)]

    def preferences(self) -> dict[str, str]:
        """Active, user-approved preferences keyed by their memory key."""
        with self.store.read() as db:
            rows = db.execute("SELECT key, value FROM memory_items WHERE kind='preference' AND status='active' "
                              "AND key IS NOT NULL ORDER BY id").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def search(self, query: MemoryQuery, *, strategy: str = "ranked") -> list[MemoryHit]:
        """Retrieve relevant memories.

        strategy: "ranked" (default: relevance + recency + status + scope) or
        "relevance" (BM25/token relevance only; used by EXP-001 as a comparison).
        """
        tokens = tokenize(query.text)
        if not tokens:
            return []
        now = self.store.now()
        max_sens = SENSITIVITY_ORDER[Sensitivity(query.max_sensitivity).value]
        status_clause = "" if query.include_inactive else " AND m.status='active'"
        kind_clause, kind_args = "", []
        if query.kinds:
            kind_clause = f" AND m.kind IN ({','.join('?' * len(query.kinds))})"
            kind_args = list(query.kinds)
        with self.store.read() as db:
            if self.store.fts5:
                rows = db.execute(
                    "SELECT m.*, bm25(memory_fts, 2.0, 1.0, 0.5) AS bm FROM memory_fts "
                    "JOIN memory_items m ON m.id = memory_fts.rowid WHERE memory_fts MATCH ?"
                    f"{status_clause}{kind_clause} ORDER BY bm LIMIT 60",
                    (fts_query(tokens), *kind_args)).fetchall()
                best = min((r["bm"] for r in rows), default=-1.0) or -1.0
                candidates = [(dict(r), (r["bm"] / best) if best < 0 else 0.0) for r in rows]
                method = "fts5-bm25"
            else:
                likes = " OR ".join(["m.value LIKE ? OR coalesce(m.key,'') LIKE ?"] * len(tokens))
                args: list[Any] = []
                for t in tokens:
                    args.extend([f"%{t}%", f"%{t}%"])
                rows = db.execute(f"SELECT m.* FROM memory_items m WHERE ({likes}){status_clause}{kind_clause} "
                                  "ORDER BY m.updated_at DESC LIMIT 400", (*args, *kind_args)).fetchall()
                candidates = []
                for r in rows:
                    rel = like_score(tokens, (r["key"] or "", 2.0), (r["value"], 1.0), (r["category"], 0.5))
                    if rel > 0:
                        candidates.append((dict(r), rel))
                method = "token-like"
            conflict_rows = db.execute("SELECT item_a, item_b FROM memory_conflicts WHERE status='open'").fetchall()
        conflicts: dict[int, set[int]] = {}
        for c in conflict_rows:
            conflicts.setdefault(c["item_a"], set()).add(c["item_b"])
            conflicts.setdefault(c["item_b"], set()).add(c["item_a"])
        hits: list[MemoryHit] = []
        for item, relevance in candidates:
            if SENSITIVITY_ORDER.get(item["sensitivity"], 2) > max_sens:
                continue
            age = age_days(item["updated_at"], now) or 0.0
            recency = 0.5 ** (age / RECENCY_HALF_LIFE_DAYS)
            status_w = STATUS_WEIGHT.get(item["epistemic"], 0.2)
            scope = 1.0 if ((query.project_id and item["project_id"] == query.project_id)
                            or (query.goal_id and item["goal_id"] == query.goal_id)
                            or (query.category and item["category"].lower() == query.category.lower())) else 0.0
            if strategy == "relevance":
                score = relevance
            else:
                score = (RANK_WEIGHTS["relevance"] * relevance + RANK_WEIGHTS["recency"] * recency
                         + RANK_WEIGHTS["status"] * status_w + RANK_WEIGHTS["scope"] * scope)
            verified_age = age_days(item["last_verified_at"] or item["updated_at"], now) or 0.0
            limit_days = STALE_AFTER_DAYS.get(item["volatility"])
            stale = bool(limit_days and verified_age > limit_days)
            hits.append(MemoryHit(
                id=item["id"], kind=item["kind"], category=item["category"], key=item["key"], value=item["value"],
                epistemic=item["epistemic"], confidence=item["confidence"], source=item["source"],
                source_event_id=item["source_event_id"], created_at=item["created_at"],
                updated_at=item["updated_at"], last_verified_at=item["last_verified_at"],
                sensitivity=item["sensitivity"], score=round(score, 4),
                components={"relevance": round(relevance, 4), "recency": round(recency, 4),
                            "status": status_w, "scope": scope},
                stale=stale, stale_reason=(f"{item['volatility']} information last confirmed "
                                           f"{verified_age:.0f} days ago") if stale else "",
                conflicts_with=tuple(sorted(conflicts.get(item["id"], ()))), strategy=method))
        hits.sort(key=lambda h: (-h.score, -h.id))
        return hits[: query.limit]

    def export(self) -> list[dict[str, Any]]:
        with self.store.read() as db:
            return [dict(r) for r in db.execute("SELECT * FROM memory_items WHERE status <> 'forgotten' ORDER BY id")]
