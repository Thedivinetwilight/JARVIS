"""SQLite persistence facade. Uses only the standard library.

The ``Store`` owns the single local database file and composes the persistence
components of the cognitive kernel:

- ``events``      experience log (unified audit + episodic records)   -> events.py
- ``memory``      versioned long-term memory with provenance           -> memory.py
- ``goals``       goals, tasks, dependencies, evidence-based completion -> goals.py
- ``state``       identity, runtime history, sessions, focus           -> state.py
- ``operations``  write-ahead operation journal                        -> operations.py

Connection contract (fixes v0.2 defects found in the audit):
- every connection is closed deterministically (v0.2 leaked connections, which
  Python 3.13 reports as ResourceWarnings);
- writes run in explicit ``BEGIN IMMEDIATE`` transactions, and the event that
  describes a change is written in the same transaction as the change (v0.2 wrote
  the audit row in a separate transaction, so a crash could lose it);
- nested write transactions on separate connections are rejected immediately
  instead of dead-locking until the busy timeout;
- ``secure_delete`` is on so erased content is overwritten in the database file.

The v0.2 public methods (``add_project``, ``add_task``, ``remember``, ...) are kept
for compatibility and delegate to the new components.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .events import EventLog
from .goals import GoalStore
from .memory import MemoryStore
from .migrations import LATEST_VERSION, MigrationReport, apply_migrations
from .operations import OperationJournal
from .state import StateStore
from .util import PRIVATE_DIR_MODE, Clock, loads, make_private, to_iso, utc_now


def now_iso() -> str:
    """Kept from v0.2."""
    return to_iso(utc_now())


_LEGACY_CONFIDENCE = ((0.8, "high"), (0.5, "medium"), (0.0, "low"))


def _confidence_label(value: float) -> str:
    if not 0 <= value <= 1:
        raise ValueError("Confidence must be between 0 and 1")
    return next(label for threshold, label in _LEGACY_CONFIDENCE if value >= threshold)


class Store:
    def __init__(self, path: Path, *, clock: Clock | None = None, backup_dir: Path | None = None,
                 use_fts5: bool | None = None):
        path = Path(path)
        path.parent.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        self.path = path
        self.clock: Clock = clock or utc_now
        self._lock = threading.RLock()
        self._local = threading.local()
        self.backup_dir = Path(backup_dir) if backup_dir else path.parent / "backups"
        self.migration: MigrationReport = apply_migrations(
            path, backup_dir=self.backup_dir, clock=self.clock, use_fts5=use_fts5)
        self.fts5 = self.migration.fts5
        # Personal data: owner-only. SQLite gives the -wal/-shm files the database file's mode.
        for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            if candidate.exists():
                make_private(candidate)
        self.schema_version = LATEST_VERSION
        self.events = EventLog(self)
        self.memory = MemoryStore(self)
        self.goals = GoalStore(self)
        self.state = StateStore(self)
        self.operations = OperationJournal(self)

    # ------------------------------------------------------------ connections
    def now(self):
        return self.clock()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA secure_delete=ON")
        return db

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        if getattr(self._local, "writing", False):
            raise RuntimeError("Nested write transaction on a separate connection; pass conn= instead")
        db = self._connect()
        self._local.writing = True
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
        finally:
            self._local.writing = False
            db.close()

    def optimize_search_indexes(self, db: sqlite3.Connection) -> None:
        """Merge FTS5 segments inside the caller's transaction.

        Deleting a row from an FTS5 table (without the ``secure-delete`` option, which needs
        SQLite >= 3.42) only adds a delete marker; the old tokens stay in earlier segments
        until they are merged. ``optimize`` rewrites the index without them.
        """
        if self.fts5:
            db.execute("INSERT INTO memory_fts(memory_fts) VALUES('optimize')")
            db.execute("INSERT INTO episode_fts(episode_fts) VALUES('optimize')")

    def checkpoint(self, *, wait_ms: int = 250) -> dict[str, Any]:
        """Copy the WAL into the main file and truncate it (must run outside a transaction).

        ``checkpoint_busy`` is reported honestly: another open connection can prevent the
        WAL from being truncated, in which case superseded page images may remain in the
        ``-wal`` file until the last connection closes. A TRUNCATE checkpoint waits on the
        busy handler for readers, so the wait is bounded here (a forget must not stall for
        the full busy timeout and then be misreported as an operation of unknown outcome).
        """
        with self.read() as db:
            db.execute(f"PRAGMA busy_timeout={max(0, int(wait_ms))}")
            busy, log, checkpointed = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return {"checkpoint_busy": bool(busy), "wal_frames": log, "checkpointed": checkpointed}

    def compact_search_indexes(self) -> dict[str, Any]:
        """Merge FTS segments and truncate the WAL so erased text does not linger on disk."""
        with self.write() as db:
            self.optimize_search_indexes(db)
        return self.checkpoint()

    def integrity(self) -> dict[str, Any]:
        with self.read() as db:
            quick = db.execute("PRAGMA quick_check").fetchone()[0]
            fk = [tuple(r) for r in db.execute("PRAGMA foreign_key_check")]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            journal = db.execute("PRAGMA journal_mode").fetchone()[0]
        return {"quick_check": quick, "foreign_key_violations": len(fk), "schema_version": version,
                "journal_mode": journal, "fts5": self.fts5}

    # ------------------------------------------------------------ v0.2 API
    def audit(self, actor: str, action: str, target: str, decision: str,
              details: dict[str, Any] | None = None) -> None:
        self.events.append(action, actor=actor, target=target, decision=decision, payload=details or {})

    def add_project(self, name: str, description: str = "") -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("Project name must contain 1-100 characters")
        stamp = to_iso(self.now())
        with self.write() as db:
            db.execute("INSERT INTO projects(name,description,created_at,updated_at) VALUES(?,?,?,?)",
                       (name, description.strip(), stamp, stamp))
            row = db.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
            self.events.append("project.create", actor="user", target=name, decision="allowed", conn=db)
        return dict(row)

    def list_projects(self) -> list[dict[str, Any]]:
        with self.read() as db:
            return [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY name")]

    def get_project(self, name: str) -> dict[str, Any] | None:
        with self.read() as db:
            row = db.execute("SELECT * FROM projects WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            return dict(row) if row else None

    def add_task(self, title: str, project: str | None = None, description: str = "",
                 priority: int = 3, due_at: str | None = None) -> dict[str, Any]:
        if not 1 <= priority <= 5:
            raise ValueError("Priority must be between 1 (highest) and 5 (lowest)")
        return self.goals.add_task(title, project=project, description=description, priority=priority, due_at=due_at)

    def list_tasks(self, status: str | None = None, project: str | None = None) -> list[dict[str, Any]]:
        return self.goals.list_tasks(status=status, project=project)

    def set_task_status(self, task_id: int, status: str) -> dict[str, Any]:
        """v0.2 entry point: a direct status change requested by the user (CLI/API)."""
        return self.goals.set_task_status(task_id, status, actor="user",
                                          evidence={"type": "user_confirmation", "event_id": None})

    def remember(self, key: str, value: str, category: str = "general",
                 confidence: float = 1.0, source: str = "user") -> dict[str, Any]:
        """v0.2 entry point: keyed memory write. History is now preserved on change."""
        if not key.strip() or len(key) > 160:
            raise ValueError("Memory key must contain 1-160 characters")
        result = self.memory.remember(value, key=key, category=category, source=source,
                                      confidence=_confidence_label(confidence))
        return result.item

    def search_memories(self, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """v0.2 entry point. Now token/BM25-ranked instead of one whole-string LIKE."""
        from .contracts import MemoryQuery, to_dict  # local import keeps module import order simple

        limit = max(1, min(limit, 50))
        if not query.strip():
            return self.memory.list(limit=limit)
        hits = self.memory.search(MemoryQuery(text=query, limit=limit))
        return [to_dict(h) for h in hits]

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        return [{"id": e["id"], "timestamp": e["ts"], "actor": e["actor"], "action": e["kind"],
                 "target": e["target"], "decision": e["decision"], "details": e["payload"]}
                for e in self.events.recent(limit)]

    def status(self) -> dict[str, Any]:
        with self.read() as db:
            counts = {row["status"]: row["n"] for row in db.execute("SELECT status,COUNT(*) n FROM tasks GROUP BY status")}
            goal_counts = {row["status"]: row["n"] for row in db.execute("SELECT status,COUNT(*) n FROM goals GROUP BY status")}
            one = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
            last_cycle = db.execute("SELECT id, status, intent, mode, started_at FROM cycles "
                                    "ORDER BY started_at DESC LIMIT 1").fetchone()
            return {"projects": one("SELECT COUNT(*) FROM projects"),
                    "tasks": one("SELECT COUNT(*) FROM tasks"),
                    "memories": one("SELECT COUNT(*) FROM memory_items WHERE status='active'"),
                    "tasks_by_status": counts, "database": str(self.path),
                    "schema_version": self.schema_version, "goals_by_status": goal_counts,
                    "memory_versions": one("SELECT COUNT(*) FROM memory_items"),
                    "open_memory_conflicts": one("SELECT COUNT(*) FROM memory_conflicts WHERE status='open'"),
                    "operations_awaiting_approval": one("SELECT COUNT(*) FROM operations WHERE status='proposed'"),
                    "operations_interrupted": one("SELECT COUNT(*) FROM operations WHERE status='interrupted'"),
                    "events": one("SELECT COUNT(*) FROM events"),
                    "last_cycle": dict(last_cycle) if last_cycle else None,
                    "retrieval": "fts5-bm25" if self.fts5 else "token-like"}

    def cycle(self, cycle_id: str) -> dict[str, Any] | None:
        with self.read() as db:
            row = db.execute("SELECT * FROM cycles WHERE id=?", (cycle_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        for key in ("stages_json", "memory_ids_json", "operation_ids_json", "validation_json"):
            item[key[:-5]] = loads(item.pop(key), None)
        return item

    def last_cycle_id(self) -> str | None:
        with self.read() as db:
            row = db.execute("SELECT id FROM cycles ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
        return row[0] if row else None
