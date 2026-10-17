"""The experience log: one append-only record of cognitive and operational events.

This unifies v0.2's ``audit`` table (migrated row-for-row) with episodic memory.
Every event has a timestamp, kind, actor and optional links to the session,
cognitive cycle (correlation id), causing event, goal, task, memory item and
operation, so task context can be reconstructed later (Acceptance test 14).

Privacy contract:
- ``text`` and ``payload`` are passed through :mod:`jarvis.redact` before storage.
- ``sensitivity`` and ``expires_at`` implement retention; :meth:`purge_expired`
  deletes expired events (used for affect appraisals by default).
- :meth:`wipe_text` erases the content of a specific event while keeping the fact
  that it existed (used when the user asks JARVIS to forget something).
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, Iterable

from .redact import redact, redact_obj
from .search import fts_query, like_score, tokenize
from .util import bounded, dumps, loads, to_iso

if TYPE_CHECKING:  # pragma: no cover
    from .db import Store

FORGOTTEN_TEXT = "[forgotten at user request]"
MAX_TEXT = 8000


class EventLog:
    def __init__(self, store: "Store"):
        self.store = store

    def append(self, kind: str, *, actor: str, target: str = "", decision: str = "",
               session_id: str | None = None, cycle_id: str | None = None, caused_by: int | None = None,
               goal_id: int | None = None, task_id: int | None = None, memory_id: int | None = None,
               operation_id: int | None = None, text: str | None = None, payload: dict[str, Any] | None = None,
               sensitivity: str = "normal", expires_at: str | None = None,
               conn: sqlite3.Connection | None = None) -> int:
        if not kind or len(kind) > 80:
            raise ValueError("Event kind must contain 1-80 characters")
        safe_text = bounded(redact(text).text, MAX_TEXT) if text is not None else None
        safe_payload = dumps(redact_obj(payload or {}))
        row = (to_iso(self.store.now()), kind, actor, target, decision, session_id, cycle_id, caused_by,
               goal_id, task_id, memory_id, operation_id, safe_text, safe_payload, sensitivity, expires_at)
        sql = ("INSERT INTO events(ts, kind, actor, target, decision, session_id, cycle_id, caused_by, goal_id, "
               "task_id, memory_id, operation_id, text, payload_json, sensitivity, expires_at) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
        if conn is not None:
            return int(conn.execute(sql, row).lastrowid)
        with self.store.write() as db:
            return int(db.execute(sql, row).lastrowid)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = loads(item.pop("payload_json", "{}"), {})
        return item

    def get(self, event_id: int) -> dict[str, Any] | None:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self._row(row) if row else None

    def recent(self, limit: int = 50, *, kinds: Iterable[str] | None = None, session_id: str | None = None,
               cycle_id: str | None = None, include_sensitive: bool = True) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        clauses, args = [], []
        kinds = list(kinds or [])
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            args.extend(kinds)
        if session_id:
            clauses.append("session_id = ?")
            args.append(session_id)
        if cycle_id:
            clauses.append("cycle_id = ?")
            args.append(cycle_id)
        if not include_sensitive:
            clauses.append("sensitivity = 'normal'")
        sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        sql += " ORDER BY id DESC LIMIT ?"
        with self.store.read() as db:
            return [self._row(r) for r in db.execute(sql, (*args, limit))]

    def for_cycle(self, cycle_id: str) -> list[dict[str, Any]]:
        with self.store.read() as db:
            return [self._row(r) for r in db.execute("SELECT * FROM events WHERE cycle_id=? ORDER BY id", (cycle_id,))]

    def session_turns(self, session_id: str, limit: int = 6) -> list[dict[str, Any]]:
        """Most recent conversation turns of a session, oldest first (bounded)."""
        rows = self.recent(limit, kinds=("observation.received", "response.produced"), session_id=session_id)
        return list(reversed(rows))

    def search_episodes(self, text: str, *, limit: int = 5, exclude_session: str | None = None,
                        exclude_cycle: str | None = None,
                        intents: tuple[str, ...] | None = ("chat",)) -> list[dict[str, Any]]:
        """Find earlier user statements relevant to ``text`` (episodic recall).

        By default only conversational statements (interpreted as ``chat``) are returned:
        commands and questions are not information, and anything the user asked JARVIS to
        remember already lives in long-term memory with its current status (so a corrected
        or forgotten statement cannot resurface here as if it were current).
        """
        tokens = tokenize(text)
        if not tokens:
            return []
        limit = max(1, min(limit, 20))
        extra, args = "", []
        if intents:
            extra += (" AND EXISTS (SELECT 1 FROM events i WHERE i.caused_by = e.id AND i.kind = 'interpretation.made'"
                      f" AND i.target IN ({','.join('?' * len(intents))}))")
            args.extend(intents)
        if exclude_session:
            extra += " AND (e.session_id IS NULL OR e.session_id <> ?)"
            args.append(exclude_session)
        if exclude_cycle:
            extra += " AND (e.cycle_id IS NULL OR e.cycle_id <> ?)"
            args.append(exclude_cycle)
        with self.store.read() as db:
            if self.store.fts5:
                rows = db.execute(
                    "SELECT e.id, e.ts, e.session_id, e.text, bm25(episode_fts) AS bm FROM episode_fts "
                    "JOIN events e ON e.id = episode_fts.rowid WHERE episode_fts MATCH ? "
                    f"AND e.text IS NOT NULL AND e.text <> ?{extra} ORDER BY bm LIMIT ?",
                    (fts_query(tokens), FORGOTTEN_TEXT, *args, limit)).fetchall()
                return [{"event_id": r["id"], "ts": r["ts"], "session_id": r["session_id"],
                         "text": r["text"], "strategy": "fts5-bm25"} for r in rows]
            rows = db.execute(
                "SELECT e.id, e.ts, e.session_id, e.text FROM events e WHERE e.kind='observation.received' "
                f"AND e.text IS NOT NULL AND e.text <> ?{extra} ORDER BY e.id DESC LIMIT 2000",
                (FORGOTTEN_TEXT, *args)).fetchall()
        scored = [(like_score(tokens, (r["text"], 1.0)), r) for r in rows]
        scored = [(s, r) for s, r in scored if s > 0]
        scored.sort(key=lambda pair: (-pair[0], -pair[1]["id"]))
        return [{"event_id": r["id"], "ts": r["ts"], "session_id": r["session_id"], "text": r["text"],
                 "strategy": "token-like"} for _, r in scored[:limit]]

    def wipe_text(self, event_id: int, *, conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE events SET text=?, payload_json='{}' WHERE id=?", (FORGOTTEN_TEXT, event_id))

    def purge_expired(self) -> int:
        now = to_iso(self.store.now())
        with self.store.write() as db:
            cur = db.execute("DELETE FROM events WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
            return cur.rowcount
