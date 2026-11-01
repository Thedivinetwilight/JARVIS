"""Local persistence: SQLite, explicit migrations, no ORM and no hidden state.

Design notes:

* one connection per request/operation, opened with ``foreign_keys=ON`` and WAL, so a
  long audio upload cannot starve an audit write;
* migrations are ordered SQL files with a recorded SHA-256 checksum. Editing an applied
  migration is a hard error, not a silent drift;
* the database file lives under the configured data directory and is never created
  inside ``eval/`` or ``docs/`` (enforced by config validation);
* nothing in this module logs SQL parameters, and no secret is ever written to SQLite:
  tokens and pairing codes are stored only as HMAC-SHA256 digests.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import MigrationError

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str


@dataclass(frozen=True)
class MigrationResult:
    applied: tuple[str, ...]
    already_applied: int

    @property
    def total(self) -> int:
        return self.already_applied + len(self.applied)


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    directory = directory or MIGRATIONS_DIR
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        stem = path.stem
        if "_" not in stem:
            raise MigrationError(f"migration file name must be <version>_<name>.sql: {path.name}")
        version_text, name = stem.split("_", 1)
        try:
            version = int(version_text)
        except ValueError as exc:
            raise MigrationError(f"migration version is not an integer: {path.name}") from exc
        raw = path.read_bytes()
        found.append(
            Migration(
                version=version,
                name=name,
                path=path,
                sql=raw.decode("utf-8"),
                checksum=hashlib.sha256(raw).hexdigest(),
            )
        )
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise MigrationError("duplicate migration version numbers found")
    return found


def split_statements(sql: str) -> list[str]:
    """Split a migration file into complete statements (SQLite-aware).

    Whole-line ``--`` comments are dropped first: a comment above the first ``CREATE TABLE``
    must not make the statement look like a comment (that bug silently skipped a table).
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        if line.strip().startswith("--"):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise MigrationError("migration file ends with an incomplete statement")
    return statements


class Store:
    """Owns the SQLite file and the migration history."""

    def __init__(self, database_path: Path | str, *, migrations_dir: Path | None = None) -> None:
        self.path = Path(database_path)
        self.migrations_dir = migrations_dir or MIGRATIONS_DIR

    # ---------------------------------------------------------------- connections
    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            # Personal data: create the database 0600 rather than relying on the umask.
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(descriptor)
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def connection(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - rollback of a failed transaction
                pass
            raise
        finally:
            connection.close()

    # ---------------------------------------------------------------- migrations
    def applied_migrations(self) -> dict[int, sqlite3.Row]:
        with self.connection() as connection:
            exists = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if not exists:
                return {}
            rows = connection.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
            return {row["version"]: row for row in rows}

    def pending_migrations(self) -> list[Migration]:
        applied = self.applied_migrations()
        return [m for m in discover_migrations(self.migrations_dir) if m.version not in applied]

    def verify_checksums(self) -> None:
        applied = self.applied_migrations()
        for migration in discover_migrations(self.migrations_dir):
            record = applied.get(migration.version)
            if record and record["checksum"] != migration.checksum:
                raise MigrationError(
                    f"migration {migration.path.name} changed after it was applied "
                    f"(recorded {record['checksum'][:12]}, file {migration.checksum[:12]})"
                )

    def migrate(self) -> MigrationResult:
        from .clock import to_iso, SystemClock

        now = to_iso(SystemClock().now())
        with self.connection() as connection:
            connection.execute(SCHEMA_MIGRATIONS_SQL)
            applied_rows = {
                row["version"]: row
                for row in connection.execute("SELECT * FROM schema_migrations").fetchall()
            }
        self.verify_checksums()

        applied: list[str] = []
        for migration in discover_migrations(self.migrations_dir):
            if migration.version in applied_rows:
                continue
            statements = split_statements(migration.sql)
            with self.transaction() as connection:
                for statement in statements:
                    try:
                        connection.execute(statement)
                    except sqlite3.Error as exc:
                        raise MigrationError(
                            f"migration {migration.path.name} failed: {exc}"
                        ) from exc
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
                    "VALUES (?, ?, ?, ?)",
                    (migration.version, migration.name, migration.checksum, now),
                )
            applied.append(migration.path.name)
        return MigrationResult(applied=tuple(applied), already_applied=len(applied_rows))

    def status(self) -> dict:
        applied = self.applied_migrations()
        pending = self.pending_migrations()
        return {
            "path": str(self.path),
            "applied_count": len(applied),
            "applied": [m.path.name for m in discover_migrations(self.migrations_dir)
                        if m.version in applied],
            "pending": [m.path.name for m in pending],
            "up_to_date": not pending,
        }
