"""Schema migrations: fresh databases, idempotent reopen, backups, connection hygiene."""
from __future__ import annotations

import gc
import sqlite3
import warnings

from jarvis.db import Store
from jarvis.migrations import LATEST_VERSION, V02_BASELINE, apply_migrations, split_script


def test_fresh_database_reaches_latest_version_without_backup(tmp_path):
    report = apply_migrations(tmp_path / "new.sqlite3", backup_dir=tmp_path / "backups")
    assert report.to_version == LATEST_VERSION and report.applied == (1, 2) and report.backup is None
    again = apply_migrations(tmp_path / "new.sqlite3", backup_dir=tmp_path / "backups")
    assert again.applied == () and again.backup is None


def test_existing_v02_database_is_backed_up_before_migration(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(V02_BASELINE)
        db.execute("INSERT INTO memories(category,key,value,confidence,source,created_at,updated_at) "
                   "VALUES('general','k','v',1.0,'user','t','t')")
    report = apply_migrations(path, backup_dir=tmp_path / "backups")
    assert report.backup is not None
    with sqlite3.connect(report.backup) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
        assert backup.execute("SELECT value FROM memories").fetchone()[0] == "v"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 2


def test_split_script_keeps_trigger_bodies_intact():
    statements = split_script("CREATE TABLE a(x);\nCREATE TRIGGER t AFTER INSERT ON a BEGIN\n"
                              "  INSERT INTO a VALUES (1);\n  INSERT INTO a VALUES (2);\nEND;\n")
    assert len(statements) == 2 and statements[1].endswith("END;")


def test_connections_are_closed_deterministically(tmp_path):
    """v0.2 leaked one connection per call (Python 3.13 reports these as ResourceWarnings)."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        store = Store(tmp_path / "jarvis.sqlite3")
        for i in range(20):
            store.memory.remember(f"note {i}", kind="note")
            store.search_memories(f"note {i}")
        gc.collect()
    assert not [w for w in caught if issubclass(w.category, ResourceWarning) and "sqlite3" in str(w.message)]


def test_nested_write_transactions_fail_fast(tmp_path):
    store = Store(tmp_path / "jarvis.sqlite3")
    with store.write():
        try:
            with store.write():
                raise AssertionError("nested write should not be allowed")
        except RuntimeError as exc:
            assert "Nested write" in str(exc)


def test_failed_write_rolls_back_atomically(tmp_path):
    store = Store(tmp_path / "jarvis.sqlite3")
    item = store.memory.remember("first value", key="thing")
    original_append = store.events.append

    def exploding_append(kind, **kw):
        if kind == "memory.corrected":
            raise OSError("simulated crash between writes")
        return original_append(kind, **kw)

    store.events.append = exploding_append
    try:
        store.memory.correct(item.item["id"], "second value")
    except OSError:
        pass
    store.events.append = original_append
    assert store.memory.get(item.item["id"])["status"] == "active"          # old row untouched
    assert [m["value"] for m in store.memory.list()] == ["first value"]     # no orphan new row


def _mode(path) -> int:
    import stat
    return stat.S_IMODE(path.stat().st_mode)


def test_personal_data_files_are_owner_only_even_with_a_permissive_umask(tmp_path):
    """Review: with the default umask (022) the data directory was created 0755 and the
    database 0644, so every other account on a shared machine could read all memories
    directly — bypassing the API token. Directories JARVIS creates are 0700; the database,
    its WAL/SHM and backups are 0600; an existing world-readable database is tightened."""
    import os

    import pytest
    if os.name != "posix":
        pytest.skip("POSIX permissions")
    previous = os.umask(0o022)
    try:
        home = tmp_path / "home"
        store = Store(home / "jarvis.sqlite3")
        assert _mode(home) == 0o700 and _mode(home / "jarvis.sqlite3") == 0o600
        reader = sqlite3.connect(home / "jarvis.sqlite3", isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM events").fetchone()           # keeps the WAL and SHM open
        store.events.append("test.write", actor="system")
        assert _mode(home / "jarvis.sqlite3-wal") == 0o600 and _mode(home / "jarvis.sqlite3-shm") == 0o600
        reader.execute("COMMIT")
        reader.close()
        legacy_home = tmp_path / "legacy"
        legacy_home.mkdir()
        with sqlite3.connect(legacy_home / "jarvis.sqlite3") as db:
            db.executescript(V02_BASELINE)
        os.chmod(legacy_home / "jarvis.sqlite3", 0o644)
        Store(legacy_home / "jarvis.sqlite3")                               # migrates v0.2 with a backup
        assert _mode(legacy_home / "jarvis.sqlite3") == 0o600
        backups = list((legacy_home / "backups").glob("*.sqlite3"))
        assert backups and all(_mode(b) == 0o600 for b in backups) and _mode(legacy_home / "backups") == 0o700
        assert _mode(legacy_home) == 0o755                # a directory the user created is reported, not changed
    finally:
        os.umask(previous)
