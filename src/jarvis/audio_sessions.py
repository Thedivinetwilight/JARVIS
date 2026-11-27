"""Audio session management: local capture, bounded, expiring.

Ported from bbff (app/jarvis/audio.py) and adapted to the Genesis store.
Audio chunks are stored locally (never sent off-machine), bounded in size
and duration, expire after a TTL, and are purged when a client is revoked.

No speech recognition or synthesis exists in this build; audio sessions
record raw chunks for future adapters. Spoken output is refused, not
substituted with text.
"""
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .util import to_iso

MAX_CHUNK_BYTES = 256 * 1024  # 256 KiB per chunk
MAX_SESSION_BYTES = 10 * 1024 * 1024  # 10 MiB per session
MAX_SESSION_SECONDS = 300  # 5 minutes
DEFAULT_TTL_MINUTES = 30


@dataclass
class AudioSession:
    id: str
    client_id: str
    status: str  # open | closed | expired | purged
    chunk_count: int
    total_bytes: int
    created_at: str
    closed_at: str | None
    expires_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id, "client_id": self.client_id, "status": self.status,
            "chunk_count": self.chunk_count, "total_bytes": self.total_bytes,
            "created_at": self.created_at, "closed_at": self.closed_at,
            "expires_at": self.expires_at,
        }


class AudioError(RuntimeError):
    pass


class AudioSessionService:
    """Manages audio capture sessions."""

    def __init__(self, store, *, ttl_minutes: int = DEFAULT_TTL_MINUTES):
        self.store = store
        self.ttl_minutes = ttl_minutes

    # ------------------------------------------------------------ lifecycle
    def open_session(self, client_id: str) -> AudioSession:
        session_id = f"aud_{secrets.token_hex(6)}"
        now = self.store.now()
        expires = now + timedelta(minutes=self.ttl_minutes)
        with self.store.write() as db:
            db.execute(
                "INSERT INTO audio_sessions (id, client_id, status, created_at, expires_at) "
                "VALUES (?,?,?,?,?)",
                (session_id, client_id, "open", to_iso(now), to_iso(expires)))
        return AudioSession(
            id=session_id, client_id=client_id, status="open",
            chunk_count=0, total_bytes=0, created_at=to_iso(now),
            closed_at=None, expires_at=to_iso(expires))

    def append_chunk(self, session_id: str, data: bytes, mime_type: str = "audio/wav") -> dict:
        if len(data) > MAX_CHUNK_BYTES:
            raise AudioError(f"Chunk exceeds {MAX_CHUNK_BYTES} bytes")
        if not mime_type.startswith("audio/"):
            raise AudioError("mime_type must be audio/*")

        now_iso = to_iso(self.store.now())
        expired = False
        with self.store.write() as db:
            row = db.execute(
                "SELECT * FROM audio_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise AudioError("Session not found")
            session = self._row_to_session(row)
            if session.status != "open":
                raise AudioError(f"Session is {session.status}")
            expired = session.expires_at < now_iso
            if not expired:
                if session.total_bytes + len(data) > MAX_SESSION_BYTES:
                    raise AudioError(f"Session exceeds {MAX_SESSION_BYTES} bytes")
                seq = session.chunk_count + 1
                chunk_id = f"chk_{secrets.token_hex(6)}"
                db.execute(
                    "INSERT INTO audio_chunks (id, session_id, seq, mime_type, data, size_bytes, "
                    "created_at) VALUES (?,?,?,?,?,?,?)",
                    (chunk_id, session_id, seq, mime_type, data, len(data), now_iso))
                db.execute(
                    "UPDATE audio_sessions SET chunk_count = ?, total_bytes = ? WHERE id = ?",
                    (seq, session.total_bytes + len(data), session_id))
        if expired:
            # Commit the expiry before refusing, otherwise the rollback would leave the
            # session looking open and every later chunk would repeat the same work.
            with self.store.write() as db:
                db.execute("UPDATE audio_sessions SET status = 'expired' WHERE id = ?", (session_id,))
            raise AudioError("Session expired")
        return {"chunk_id": chunk_id, "seq": seq, "size_bytes": len(data)}

    def close_session(self, session_id: str) -> AudioSession:
        now_iso = to_iso(self.store.now())
        with self.store.write() as db:
            row = db.execute(
                "SELECT * FROM audio_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise AudioError("Session not found")
            session = self._row_to_session(row)
            if session.status == "open":
                db.execute(
                    "UPDATE audio_sessions SET status = 'closed', closed_at = ? WHERE id = ?",
                    (now_iso, session_id))
                session = AudioSession(
                    id=session.id, client_id=session.client_id, status="closed",
                    chunk_count=session.chunk_count, total_bytes=session.total_bytes,
                    created_at=session.created_at, closed_at=now_iso,
                    expires_at=session.expires_at)
        return session

    def get_session(self, session_id: str) -> AudioSession | None:
        with self.store.read() as db:
            row = db.execute(
                "SELECT * FROM audio_sessions WHERE id = ?", (session_id,)).fetchone()
        return self._row_to_session(row) if row else None

    # ------------------------------------------------------------ maintenance
    def purge_expired(self) -> int:
        """Expire open sessions past TTL and delete their chunks. Returns count."""
        now_iso = to_iso(self.store.now())
        with self.store.write() as db:
            rows = db.execute(
                "SELECT id FROM audio_sessions WHERE status = 'open' AND expires_at < ?",
                (now_iso,)).fetchall()
            for row in rows:
                db.execute("DELETE FROM audio_chunks WHERE session_id = ?", (row["id"],))
                db.execute(
                    "UPDATE audio_sessions SET status = 'expired' WHERE id = ?", (row["id"],))
            return len(rows)

    def purge_for_client(self, client_id: str) -> int:
        """Delete all audio data for a revoked client. Returns session count."""
        with self.store.write() as db:
            rows = db.execute(
                "SELECT id FROM audio_sessions WHERE client_id = ?", (client_id,)).fetchall()
            for row in rows:
                db.execute("DELETE FROM audio_chunks WHERE session_id = ?", (row["id"],))
                db.execute(
                    "UPDATE audio_sessions SET status = 'purged' WHERE id = ?", (row["id"],))
            return len(rows)

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> AudioSession:
        return AudioSession(
            id=row["id"], client_id=row["client_id"], status=row["status"],
            chunk_count=row["chunk_count"], total_bytes=row["total_bytes"],
            created_at=row["created_at"], closed_at=row["closed_at"],
            expires_at=row["expires_at"])
