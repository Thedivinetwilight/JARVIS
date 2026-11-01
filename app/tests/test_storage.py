"""Local persistence interfaces and migrations."""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import tempfile
import unittest

from app.jarvis.clock import SystemClock, to_iso
from app.jarvis.db import MIGRATIONS_DIR, MigrationError, Store, split_statements


class SplitStatementTests(unittest.TestCase):
    def test_leading_comment_does_not_swallow_the_first_statement(self):
        # Regression: a whole-line comment above CREATE TABLE once made the splitter treat
        # the statement as a comment, so a table silently never existed.
        statements = split_statements("-- a comment\nCREATE TABLE t (id TEXT);\n")
        self.assertEqual(statements, ["CREATE TABLE t (id TEXT);"])

    def test_incomplete_statement_is_refused(self):
        with self.assertRaises(MigrationError):
            split_statements("CREATE TABLE t (id TEXT)\n")


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = pathlib.Path(self._tmp.name)
        self.path = self.directory / "jarvis.sqlite3"

    def test_fresh_database_applies_every_migration_once(self):
        store = Store(self.path)
        result = store.migrate()
        self.assertEqual(result.applied, ("0001_core.sql", "0002_tasks_reminders.sql",
                                          "0003_reminder_delivery.sql"))
        self.assertEqual(result.already_applied, 0)
        again = store.migrate()
        self.assertEqual(again.applied, ())
        self.assertEqual(again.already_applied, 3)

    def test_pending_and_status_report_the_schema_state(self):
        store = Store(self.path)
        self.assertEqual(len(store.pending_migrations()), 3)
        self.assertFalse(store.status()["up_to_date"])
        store.migrate()
        status = store.status()
        self.assertTrue(status["up_to_date"])
        self.assertEqual(status["applied_count"], 3)
        self.assertEqual(status["pending"], [])
        self.assertEqual(status["applied"][-1], "0003_reminder_delivery.sql")

    def test_applied_migrations_are_checksummed(self):
        store = Store(self.path)
        store.migrate()
        with store.connection() as connection:
            rows = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertEqual([row["name"] for row in rows],
                         ["core", "tasks_reminders", "reminder_delivery"])
        for row in rows:
            self.assertEqual(len(row["checksum"]), 64)

    def test_edited_migration_is_refused(self):
        migrations = self.directory / "migrations"
        shutil.copytree(MIGRATIONS_DIR, migrations)
        store = Store(self.path, migrations_dir=migrations)
        store.migrate()
        target = migrations / "0001_core.sql"
        target.write_text(target.read_text(encoding="utf-8") + "\n-- edited\n",
                          encoding="utf-8")
        with self.assertRaises(MigrationError) as caught:
            Store(self.path, migrations_dir=migrations).migrate()
        self.assertIn("changed after it was applied", str(caught.exception))

    def test_statement_failure_is_wrapped_with_the_migration_name(self):
        migrations = self.directory / "migrations2"
        migrations.mkdir()
        (migrations / "0001_broken.sql").write_text(
            "CREATE TABLE ok (id TEXT);\nCREATE TABLE ok (id TEXT);\n", encoding="utf-8")
        with self.assertRaises(MigrationError) as caught:
            Store(self.path, migrations_dir=migrations).migrate()
        self.assertIn("0001_broken.sql", str(caught.exception))

    CLIENT_SQL = (
        "INSERT INTO clients (id, label, platform, kind, scopes, token_hash, token_prefix, "
        "created_at, expires_at, revoked_at) "
        "VALUES (?, 'label', 'tablet', 'device', ?, ?, 'abcd1234', ?, ?, NULL)"
    )

    def _insert_client(self, store, client_id: str, now: str) -> None:
        with store.connection() as connection:
            connection.execute(self.CLIENT_SQL,
                               (client_id, '["tasks.read"]', f"hash-{client_id}", now, now))

    def _insert_session(self, store, session_id: str, client_id: str, now: str) -> None:
        with store.connection() as connection:
            connection.execute(
                "INSERT INTO audio_sessions (id, client_id, started_at, expires_at, state, "
                "byte_count, chunk_count, max_bytes) VALUES (?, ?, ?, ?, 'open', 0, 0, 1000)",
                (session_id, client_id, now, now),
            )

    def test_foreign_keys_and_cascades_are_enforced(self):
        store = Store(self.path)
        store.migrate()
        now = to_iso(SystemClock().now())
        self._insert_client(store, "cli_test", now)
        self._insert_session(store, "as_test", "cli_test", now)
        with store.connection() as connection:
            connection.execute("DELETE FROM clients WHERE id = 'cli_test'")
            remaining = connection.execute(
                "SELECT COUNT(*) AS n FROM audio_sessions").fetchone()["n"]
        self.assertEqual(remaining, 0)

    def test_unknown_client_is_rejected_by_foreign_keys(self):
        store = Store(self.path)
        store.migrate()
        now = to_iso(SystemClock().now())
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_session(store, "as_orphan", "nobody", now)

    def test_database_is_wal_and_private(self):
        store = Store(self.path)
        store.migrate()
        with store.connection() as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_unapplied_migration_failure_leaves_no_partial_state(self):
        migrations = self.directory / "migrations3"
        migrations.mkdir()
        (migrations / "0001_partial.sql").write_text(
            "CREATE TABLE first (id TEXT);\nCREATE TABLE second (id TEXT);\n"
            "INSERT INTO missing_table VALUES (1);\n",
            encoding="utf-8",
        )
        store = Store(self.path, migrations_dir=migrations)
        with self.assertRaises(MigrationError):
            store.migrate()
        with store.connection() as connection:
            tables = {row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertNotIn("first", tables)
        self.assertNotIn("second", tables)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
