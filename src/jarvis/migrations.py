"""Versioned SQLite schema migrations.

Contract
--------
* The schema version is stored in ``PRAGMA user_version``.
* Version 0 is either an empty file or a JARVIS v0.2 database (created before
  versioning existed). Migration 1 recreates the v0.2 baseline idempotently, so
  both cases converge.
* Before migrating a database that already contains tables, a consistent copy is
  written with the SQLite backup API to ``<home>/backups/``.
* Each migration runs inside one ``BEGIN IMMEDIATE`` transaction together with
  its ``user_version`` update. If any statement fails the transaction is rolled
  back and the file keeps its previous schema and data (tested).
* A database whose version is newer than this code refuses to open
  (:class:`SchemaTooNewError`) instead of being silently modified.
* Data-moving migrations verify row counts and abort on mismatch.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .util import PRIVATE_DIR_MODE, Clock, make_private, to_iso, utc_now


class MigrationError(RuntimeError):
    def __init__(self, message: str, *, version: int, backup: Path | None):
        super().__init__(message)
        self.version = version
        self.backup = backup


class SchemaTooNewError(RuntimeError):
    pass


class DatabaseIntegrityError(RuntimeError):
    pass


def fts5_available() -> bool:
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
            return True
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def split_script(script: str) -> list[str]:
    """Split SQL into complete statements (trigger bodies containing ';' stay intact)."""
    statements, buffer = [], ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise ValueError("Incomplete SQL statement in migration script")
    return statements


def _run(conn: sqlite3.Connection, script: str) -> None:
    for statement in split_script(script):
        conn.execute(statement)


# --------------------------------------------------------------------------------------
# Migration 1: the JARVIS v0.2 baseline (exact DDL from the v0.2 Store, idempotent).
# --------------------------------------------------------------------------------------
V02_BASELINE = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'todo', priority INTEGER NOT NULL DEFAULT 3,
  due_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY, category TEXT NOT NULL DEFAULT 'general',
  key TEXT NOT NULL, value TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 1.0,
  source TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(category, key)
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, target TEXT NOT NULL, decision TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit(timestamp);
"""


def _migration_1(conn: sqlite3.Connection, ctx: "MigrationContext") -> None:
    _run(conn, V02_BASELINE)


# --------------------------------------------------------------------------------------
# Migration 2: Genesis 0.1 persistent cognitive kernel.
# --------------------------------------------------------------------------------------
GENESIS_CORE = """
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL, backup_path TEXT
);
CREATE TABLE identity (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  system_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT 'JARVIS',
  principal TEXT NOT NULL, charter_version TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE runtime_instances (
  id TEXT PRIMARY KEY, version TEXT NOT NULL, schema_version INTEGER NOT NULL,
  pid INTEGER, started_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  ended_at TEXT, end_reason TEXT
);
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, channel TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  started_at TEXT NOT NULL, last_active_at TEXT NOT NULL, ended_at TEXT,
  end_reason TEXT, turn_count INTEGER NOT NULL DEFAULT 0, summary_json TEXT
);
CREATE INDEX idx_sessions_channel ON sessions(channel, status, last_active_at);
CREATE TABLE state_kv (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL DEFAULT '',
  session_id TEXT, cycle_id TEXT, caused_by INTEGER REFERENCES events(id) ON DELETE SET NULL,
  goal_id INTEGER, task_id INTEGER, memory_id INTEGER, operation_id INTEGER,
  text TEXT, payload_json TEXT NOT NULL DEFAULT '{}',
  sensitivity TEXT NOT NULL DEFAULT 'normal', expires_at TEXT
);
CREATE INDEX idx_events_session ON events(session_id, id);
CREATE INDEX idx_events_cycle ON events(cycle_id);
CREATE INDEX idx_events_kind ON events(kind, id);
CREATE INDEX idx_events_expiry ON events(expires_at) WHERE expires_at IS NOT NULL;

CREATE TABLE memory_items (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT 'fact', category TEXT NOT NULL DEFAULT 'general',
  key TEXT, value TEXT NOT NULL,
  epistemic TEXT NOT NULL DEFAULT 'source_claim', confidence TEXT NOT NULL DEFAULT 'medium',
  source TEXT NOT NULL DEFAULT 'user', source_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL, goal_id INTEGER,
  sensitivity TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'active',
  supersedes_id INTEGER REFERENCES memory_items(id), superseded_by_id INTEGER REFERENCES memory_items(id),
  change_reason TEXT, volatility TEXT NOT NULL DEFAULT 'stable',
  valid_from TEXT, valid_to TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  last_verified_at TEXT, last_accessed_at TEXT, access_count INTEGER NOT NULL DEFAULT 0, expires_at TEXT
);
CREATE INDEX idx_memory_key ON memory_items(category, key, status);
CREATE INDEX idx_memory_status ON memory_items(status, updated_at);
CREATE TABLE memory_conflicts (
  id INTEGER PRIMARY KEY,
  item_a INTEGER NOT NULL REFERENCES memory_items(id), item_b INTEGER NOT NULL REFERENCES memory_items(id),
  detected_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
  resolution TEXT, resolved_at TEXT, resolved_event_id INTEGER
);
CREATE INDEX idx_conflicts_status ON memory_conflicts(status);

CREATE TABLE goals (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  origin TEXT NOT NULL DEFAULT 'user', authorised INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active', priority INTEGER NOT NULL DEFAULT 3,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  success_criteria TEXT NOT NULL DEFAULT '', due_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT,
  completion_evidence_json TEXT, source_event_id INTEGER
);
CREATE INDEX idx_goals_status ON goals(status, priority);
ALTER TABLE tasks ADD COLUMN goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL;
ALTER TABLE tasks ADD COLUMN position INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN blocker TEXT;
ALTER TABLE tasks ADD COLUMN completion_evidence_json TEXT;
ALTER TABLE tasks ADD COLUMN completed_at TEXT;
ALTER TABLE tasks ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN last_error TEXT;
ALTER TABLE tasks ADD COLUMN origin TEXT NOT NULL DEFAULT 'user';
CREATE INDEX idx_tasks_goal ON tasks(goal_id, position);
CREATE TABLE task_dependencies (
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  depends_on INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  PRIMARY KEY (task_id, depends_on), CHECK (task_id <> depends_on)
);

CREATE TABLE operations (
  id INTEGER PRIMARY KEY, correlation_id TEXT, session_id TEXT,
  capability TEXT NOT NULL, arguments_json TEXT NOT NULL, args_digest TEXT NOT NULL,
  idempotency_key TEXT UNIQUE, origin TEXT NOT NULL, risk TEXT NOT NULL,
  status TEXT NOT NULL, decision_reason TEXT NOT NULL DEFAULT '',
  requested_at TEXT NOT NULL, approved_at TEXT, approved_by TEXT,
  started_at TEXT, finished_at TEXT, timeout_s REAL NOT NULL,
  result_json TEXT, error_type TEXT, error_message TEXT,
  verified INTEGER, verification_note TEXT, runtime_instance TEXT
);
CREATE INDEX idx_operations_status ON operations(status, requested_at);
CREATE INDEX idx_operations_correlation ON operations(correlation_id);

CREATE TABLE cycles (
  id TEXT PRIMARY KEY, session_id TEXT, runtime_instance TEXT,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
  intent TEXT, mode TEXT, input_event_id INTEGER, response_event_id INTEGER,
  stages_json TEXT NOT NULL DEFAULT '[]', memory_ids_json TEXT NOT NULL DEFAULT '[]',
  operation_ids_json TEXT NOT NULL DEFAULT '[]', provider TEXT, provider_outcome TEXT,
  validation_json TEXT NOT NULL DEFAULT '{}', error_stage TEXT, error_type TEXT
);
CREATE INDEX idx_cycles_session ON cycles(session_id, started_at);
"""

GENESIS_FTS = """
CREATE VIRTUAL TABLE memory_fts USING fts5(key, value, category, tokenize='porter unicode61');
CREATE TRIGGER memory_items_fts_insert AFTER INSERT ON memory_items WHEN new.status = 'active' BEGIN
  INSERT INTO memory_fts(rowid, key, value, category) VALUES (new.id, coalesce(new.key, ''), new.value, new.category);
END;
CREATE TRIGGER memory_items_fts_update AFTER UPDATE ON memory_items BEGIN
  DELETE FROM memory_fts WHERE rowid = old.id;
  INSERT INTO memory_fts(rowid, key, value, category)
    SELECT new.id, coalesce(new.key, ''), new.value, new.category WHERE new.status = 'active';
END;
CREATE TRIGGER memory_items_fts_delete AFTER DELETE ON memory_items BEGIN
  DELETE FROM memory_fts WHERE rowid = old.id;
END;
CREATE VIRTUAL TABLE episode_fts USING fts5(text, tokenize='porter unicode61');
CREATE TRIGGER events_fts_insert AFTER INSERT ON events
  WHEN new.kind = 'observation.received' AND new.text IS NOT NULL BEGIN
  INSERT INTO episode_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER events_fts_update AFTER UPDATE OF text ON events BEGIN
  DELETE FROM episode_fts WHERE rowid = old.id;
  INSERT INTO episode_fts(rowid, text)
    SELECT new.id, new.text WHERE new.kind = 'observation.received' AND new.text IS NOT NULL;
END;
CREATE TRIGGER events_fts_delete AFTER DELETE ON events BEGIN
  DELETE FROM episode_fts WHERE rowid = old.id;
END;
"""

_LEGACY_CONFIDENCE = "CASE WHEN confidence >= 0.8 THEN 'high' WHEN confidence >= 0.5 THEN 'medium' ELSE 'low' END"


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _migration_2(conn: sqlite3.Connection, ctx: "MigrationContext") -> None:
    _run(conn, GENESIS_CORE)
    if ctx.fts5:
        _run(conn, GENESIS_FTS)
    stamp = to_iso(ctx.clock())

    # 1. Legacy audit rows become events (the unified experience log).
    audit_rows = _count(conn, "audit")
    conn.execute(
        "INSERT INTO events(ts, kind, actor, target, decision, payload_json) "
        "SELECT timestamp, action, actor, target, decision, details_json FROM audit ORDER BY id")
    if _count(conn, "events") != audit_rows:
        raise RuntimeError("audit -> events row count mismatch")
    conn.execute("DROP TABLE audit")

    # 2. Legacy memories become versioned memory items (history starts here).
    memory_rows = _count(conn, "memories")
    conn.execute(
        "INSERT INTO memory_items(kind, category, key, value, epistemic, confidence, source, status, "
        "change_reason, created_at, updated_at) "
        f"SELECT 'fact', category, key, value, 'source_claim', {_LEGACY_CONFIDENCE}, source, 'active', "
        "'migrated_from_v0.2', created_at, updated_at FROM memories ORDER BY id")
    if _count(conn, "memory_items") != memory_rows:
        raise RuntimeError("memories -> memory_items row count mismatch")
    conn.execute("DROP TABLE memories")

    # 3. Tasks marked done in v0.2 had no completion evidence; say so explicitly.
    conn.execute(
        "UPDATE tasks SET completion_evidence_json = ?, completed_at = updated_at WHERE status = 'done'",
        ('{"type":"legacy_unverified","note":"completed before evidence tracking (v0.2)"}',))

    conn.execute(
        "INSERT INTO events(ts, kind, actor, target, decision, payload_json) VALUES (?,?,?,?,?,?)",
        (stamp, "system.migrated", "system", "schema", "success",
         f'{{"to_version":2,"audit_rows":{audit_rows},"memory_rows":{memory_rows},"fts5":{str(ctx.fts5).lower()}}}'))


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection, "MigrationContext"], None]


@dataclass(frozen=True)
class MigrationContext:
    clock: Clock
    fts5: bool


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "v0.2 baseline", _migration_1),
    Migration(2, "genesis-0.1 persistent cognitive kernel", _migration_2),
)
LATEST_VERSION = MIGRATIONS[-1].version


@dataclass(frozen=True)
class MigrationReport:
    from_version: int
    to_version: int
    applied: tuple[int, ...]
    backup: Path | None
    fts5: bool


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=10000")
    # Migration 2 copies v0.2 rows and DROPs the old tables. Without secure_delete the freed
    # pages keep the old content on SQLite builds that do not enable it at compile time
    # (it is a per-connection setting), so a later `forget` could not erase those copies.
    conn.execute("PRAGMA secure_delete=ON")
    return conn


def _user_tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def _backup(conn: sqlite3.Connection, backup_dir: Path, version: int, clock: Clock) -> Path:
    backup_dir.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    stamp = clock().strftime("%Y%m%dT%H%M%S")
    target = backup_dir / f"jarvis-schema{version}-{stamp}.sqlite3"
    n = 1
    while target.exists():
        target = backup_dir / f"jarvis-schema{version}-{stamp}-{n}.sqlite3"
        n += 1
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    make_private(target)          # a full copy of personal data: owner-only
    return target


def database_has_fts(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_fts'").fetchone() is not None


def apply_migrations(path: Path, *, backup_dir: Path, clock: Clock = utc_now,
                     use_fts5: bool | None = None,
                     migrations: tuple[Migration, ...] = MIGRATIONS) -> MigrationReport:
    """Bring the database at ``path`` to the latest schema. See module docstring."""
    fts5 = fts5_available() if use_fts5 is None else (use_fts5 and fts5_available())
    latest = migrations[-1].version
    conn = _open(path)
    try:
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise DatabaseIntegrityError(f"{path.name} is not a readable SQLite database: {exc}") from None
        if not result or result[0] != "ok":
            raise DatabaseIntegrityError(f"{path.name} failed integrity check: {result[0] if result else 'unknown'}")
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if current > latest:
            raise SchemaTooNewError(
                f"Database schema version {current} is newer than this JARVIS build supports ({latest}). "
                "Refusing to open it to avoid corrupting data; upgrade JARVIS instead.")
        if current >= 2 and database_has_fts(conn) and not fts5_available():
            raise DatabaseIntegrityError("This database uses SQLite FTS5, which this Python build lacks.")
        if current == latest:
            return MigrationReport(current, current, (), None, database_has_fts(conn))
        conn.execute("PRAGMA journal_mode=WAL")
        backup = _backup(conn, backup_dir, current, clock) if _user_tables(conn) else None
        ctx = MigrationContext(clock=clock, fts5=fts5)
        applied: list[int] = []
        for migration in migrations:
            if migration.version <= current:
                continue
            try:
                conn.execute("BEGIN IMMEDIATE")
                migration.apply(conn, ctx)
                conn.execute(f"PRAGMA user_version = {int(migration.version)}")
                if migration.version >= 2:
                    conn.execute(
                        "INSERT INTO schema_migrations(version, name, applied_at, backup_path) VALUES (?,?,?,?)",
                        (migration.version, migration.name, to_iso(clock()), str(backup) if backup else None))
                conn.execute("COMMIT")
            except Exception as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise MigrationError(
                    f"Migration {migration.version} ({migration.name}) failed and was rolled back: "
                    f"{type(exc).__name__}: {exc}", version=migration.version, backup=backup) from exc
            applied.append(migration.version)
        problems = [tuple(r) for r in conn.execute("PRAGMA foreign_key_check")]
        if problems:
            raise DatabaseIntegrityError(f"Foreign key check failed after migration: {problems[:5]}")
        return MigrationReport(current, latest, tuple(applied), backup, database_has_fts(conn))
    finally:
        conn.close()
