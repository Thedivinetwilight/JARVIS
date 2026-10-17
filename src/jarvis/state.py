"""Persistent identity, runtime instances, sessions, and small focus state.

Identity (enduring): a single ``identity`` row (enforced by a CHECK constraint)
holding a random ``system_id`` created once, the principal's name, and the
charter version. Charter changes are recorded as events, not silent rewrites.

Runtime history (enduring): one ``runtime_instances`` row per process start,
recording the software and schema versions that ran. This supports comparing
behaviour across versions and detecting operations left behind by a crashed process.

Sessions (changeable): logical conversations per channel. A session continues
while activity is within ``idle_minutes``; otherwise the old session is closed
with a deterministic summary and a new one starts.

Focus (temporary but restorable): ``state_kv`` keeps small pointers such as the
goal currently in focus, so working context can be rebuilt after a restart.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .util import dumps, loads, new_id, parse_iso, to_iso

if TYPE_CHECKING:  # pragma: no cover
    from .db import Store

_SESSION_ID = re.compile(r"[A-Za-z0-9_]{1,80}")


class StateStore:
    def __init__(self, store: "Store"):
        self.store = store

    # ------------------------------------------------------------- identity
    def ensure_identity(self, *, principal: str, charter_version: str) -> dict[str, Any]:
        with self.store.write() as db:
            now = to_iso(self.store.now())
            row = db.execute("SELECT * FROM identity WHERE singleton=1").fetchone()
            if row is None:
                system_id = new_id("jarvis")
                db.execute("INSERT INTO identity(singleton, system_id, principal, charter_version, created_at, "
                           "updated_at) VALUES (1,?,?,?,?,?)", (system_id, principal, charter_version, now, now))
                self.store.events.append("identity.created", actor="system", target=system_id,
                                         payload={"charter_version": charter_version}, conn=db)
            else:
                changes = {}
                if row["charter_version"] != charter_version:
                    changes["charter_version"] = (row["charter_version"], charter_version)
                if row["principal"] != principal:
                    changes["principal"] = (row["principal"], principal)
                if changes:
                    db.execute("UPDATE identity SET charter_version=?, principal=?, updated_at=? WHERE singleton=1",
                               (charter_version, principal, now))
                    self.store.events.append("identity.updated", actor="system", target=row["system_id"],
                                             payload={k: {"from": a, "to": b} for k, (a, b) in changes.items()},
                                             conn=db)
            return dict(db.execute("SELECT * FROM identity WHERE singleton=1").fetchone())

    def identity(self) -> dict[str, Any] | None:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM identity WHERE singleton=1").fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------- runtime
    def register_runtime(self, *, version: str, schema_version: int, pid: int) -> str:
        runtime_id = new_id("rt")
        now = to_iso(self.store.now())
        with self.store.write() as db:
            db.execute("INSERT INTO runtime_instances(id, version, schema_version, pid, started_at, last_seen_at) "
                       "VALUES (?,?,?,?,?,?)", (runtime_id, version, schema_version, pid, now, now))
            previous = db.execute("SELECT version FROM runtime_instances WHERE id<>? ORDER BY started_at DESC LIMIT 1",
                                  (runtime_id,)).fetchone()
            if previous and previous["version"] != version:
                self.store.events.append("system.version_changed", actor="system", target=runtime_id,
                                         payload={"from": previous["version"], "to": version}, conn=db)
        return runtime_id

    def heartbeat(self, runtime_id: str) -> None:
        with self.store.write() as db:
            db.execute("UPDATE runtime_instances SET last_seen_at=? WHERE id=?", (to_iso(self.store.now()), runtime_id))

    def end_runtime(self, runtime_id: str, reason: str = "shutdown") -> None:
        with self.store.write() as db:
            db.execute("UPDATE runtime_instances SET ended_at=?, end_reason=? WHERE id=? AND ended_at IS NULL",
                       (to_iso(self.store.now()), reason, runtime_id))

    def runtime_history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.store.read() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM runtime_instances ORDER BY started_at DESC LIMIT ?", (limit,))]

    # ------------------------------------------------------------- sessions
    def open_session(self, channel: str, *, idle_minutes: int, session_id: str | None = None) -> dict[str, Any]:
        """Return the session to use for this interaction.

        Result keys: ``session`` (row), ``is_new`` (bool), ``previous`` (the most
        recent earlier session on this channel, if a new one was started).
        """
        now_dt = self.store.now()
        now = to_iso(now_dt)
        if session_id is not None and not _SESSION_ID.fullmatch(session_id):
            raise ValueError("session_id must be 1-80 characters of letters, digits or underscore")
        with self.store.write() as db:
            if session_id:
                row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
                if row and row["status"] == "active":
                    return {"session": dict(row), "is_new": False, "previous": None}
                if row:  # an ended session is never reopened or overwritten; start a fresh one
                    session_id = None
            row = db.execute("SELECT * FROM sessions WHERE channel=? AND status='active' "
                             "ORDER BY last_active_at DESC LIMIT 1", (channel,)).fetchone()
            if row is not None and not session_id:
                last = parse_iso(row["last_active_at"])
                if last and now_dt - last <= timedelta(minutes=idle_minutes):
                    return {"session": dict(row), "is_new": False, "previous": None}
            stale = db.execute("SELECT * FROM sessions WHERE channel=? AND status='active'", (channel,)).fetchall()
            for old in stale:
                self._close(db, old["id"], "idle", now)
            new_session = session_id or new_id("ses")
            db.execute("INSERT INTO sessions(id, channel, started_at, last_active_at) VALUES (?,?,?,?)",
                       (new_session, channel, now, now))
            self.store.events.append("session.started", actor="system", target=new_session, session_id=new_session,
                                     payload={"channel": channel}, conn=db)
            previous = db.execute("SELECT * FROM sessions WHERE id<>? ORDER BY last_active_at DESC LIMIT 1",
                                  (new_session,)).fetchone()
            created = db.execute("SELECT * FROM sessions WHERE id=?", (new_session,)).fetchone()
            return {"session": dict(created), "is_new": True, "previous": dict(previous) if previous else None}

    def _close(self, db, session_id: str, reason: str, now: str) -> None:
        summary = self._summarise(db, session_id)
        db.execute("UPDATE sessions SET status='ended', ended_at=?, end_reason=?, summary_json=? WHERE id=?",
                   (now, reason, dumps(summary), session_id))
        self.store.events.append("session.ended", actor="system", target=session_id, session_id=session_id,
                                 payload={"reason": reason, "summary": summary}, conn=db)

    def end_session(self, session_id: str, reason: str = "user_exit") -> None:
        with self.store.write() as db:
            row = db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row and row["status"] == "active":
                self._close(db, session_id, reason, to_iso(self.store.now()))

    def touch_session(self, session_id: str) -> None:
        with self.store.write() as db:
            db.execute("UPDATE sessions SET last_active_at=?, turn_count=turn_count+1 WHERE id=?",
                       (to_iso(self.store.now()), session_id))

    @staticmethod
    def _summarise(db, session_id: str) -> dict[str, Any]:
        """Deterministic extractive summary of what happened in a session (no model involved)."""
        counts = {r["kind"]: r["n"] for r in db.execute(
            "SELECT kind, COUNT(*) n FROM events WHERE session_id=? GROUP BY kind", (session_id,))}
        memories = [r[0] for r in db.execute(
            "SELECT DISTINCT memory_id FROM events WHERE session_id=? AND kind='memory.created' "
            "AND memory_id IS NOT NULL ORDER BY memory_id", (session_id,))]
        goals = [r[0] for r in db.execute(
            "SELECT DISTINCT goal_id FROM events WHERE session_id=? AND goal_id IS NOT NULL ORDER BY goal_id",
            (session_id,))]
        completed = [r[0] for r in db.execute(
            "SELECT task_id FROM events WHERE session_id=? AND kind='task.status_changed' "
            "AND payload_json LIKE '%\"to\":\"done\"%'", (session_id,))]
        last_user = db.execute("SELECT text FROM events WHERE session_id=? AND kind='observation.received' "
                               "AND text IS NOT NULL ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        return {"turns": counts.get("observation.received", 0), "memories_created": memories,
                "goals_touched": goals, "tasks_completed": completed,
                "last_user_message": (last_user[0][:200] if last_user and last_user[0] else None)}

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["summary"] = loads(item.pop("summary_json", None))
        return item

    def last_ended_session(self, exclude: str | None = None) -> dict[str, Any] | None:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM sessions WHERE status='ended' AND id<>coalesce(?, '') "
                             "ORDER BY ended_at DESC LIMIT 1", (exclude,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["summary"] = loads(item.pop("summary_json", None))
        return item

    # ------------------------------------------------------------- focus kv
    def get(self, key: str, default: Any = None) -> Any:
        with self.store.read() as db:
            row = db.execute("SELECT value_json FROM state_kv WHERE key=?", (key,)).fetchone()
        return loads(row[0], default) if row else default

    def set(self, key: str, value: Any) -> None:
        with self.store.write() as db:
            db.execute("INSERT INTO state_kv(key, value_json, updated_at) VALUES (?,?,?) ON CONFLICT(key) "
                       "DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                       (key, dumps(value), to_iso(self.store.now())))
