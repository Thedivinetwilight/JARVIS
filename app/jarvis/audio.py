"""Voice-first audio sessions: the input boundary.

Protocol shape (all JSON, no text conversation):

``POST /v1/audio/sessions``          open a session; the reply states what the server can and
                                     cannot do with audio
``POST /v1/audio/sessions/{id}/audio`` append one chunk of ``audio/*`` bytes
``POST /v1/audio/sessions/{id}/end`` finish and get an honest summary
``GET  /v1/audio/output``            spoken-output boundary; 503 while no synthesiser exists

Rules enforced here:

* only ``audio/*`` bodies are accepted; a text body is refused with
  ``415 text_input_not_supported`` rather than being stored;
* chunk and session byte limits come from configuration;
* a session belongs to exactly one client and expires;
* audio is written under the private data directory, never into the repository, never
  logged, and never forwarded — there is no egress path in this module.
"""

from __future__ import annotations

import secrets
import shutil
from datetime import timedelta
from pathlib import Path

from .audit import AuditLog
from .clock import Clock, parse_iso, to_iso
from .config import Config
from .db import Store
from .errors import BadRequest, Expired, NotFound, PayloadTooLarge, Unavailable, UnsupportedMediaType

TEXTUAL_CONTENT_TYPES = {
    "text/plain", "text/markdown", "application/json", "application/x-www-form-urlencoded",
    "text/html",
}


def _session_dict(row) -> dict:  # noqa: ANN001 - sqlite3.Row
    return {
        "id": row["id"],
        "client_id": row["client_id"],
        "state": row["state"],
        "started_at": row["started_at"],
        "expires_at": row["expires_at"],
        "ended_at": row["ended_at"],
        "content_type": row["content_type"],
        "bytes": row["byte_count"],
        "chunks": row["chunk_count"],
        "max_bytes": row["max_bytes"],
    }


class AudioSessionService:
    def __init__(self, store: Store, config: Config, clock: Clock, audit: AuditLog) -> None:
        self.store = store
        self.config = config
        self.clock = clock
        self.audit = audit

    # ------------------------------------------------------------------ paths
    def _session_dir(self, client_id: str, session_id: str) -> Path:
        return self.config.storage.audio_dir / client_id / session_id

    # ------------------------------------------------------------------ lifecycle
    def start(self, *, client_id: str, request_id: str | None = None) -> dict:
        session_id = f"aud_{secrets.token_hex(8)}"
        now = self.clock.now()
        expires = now + timedelta(minutes=self.config.storage.audio_session_ttl_minutes)
        max_bytes = self.config.storage.max_audio_bytes_per_session
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO audio_sessions (id, client_id, started_at, expires_at, state, "
                "max_bytes) VALUES (?, ?, ?, ?, 'open', ?)",
                (session_id, client_id, to_iso(now), to_iso(expires), max_bytes),
            )
        directory = self._session_dir(client_id, session_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.audit.record(
            actor_kind="client", actor_id=client_id, action="audio.session_started",
            target=session_id, outcome="allowed", request_id=request_id,
        )
        return {
            "session_id": session_id,
            "expires_at": to_iso(expires),
            "max_bytes": max_bytes,
            "max_chunk_bytes": self.config.storage.max_audio_chunk_bytes,
            "accepted_content_types": ["audio/opus", "audio/wav", "audio/webm", "audio/ogg"],
            "input": "audio only",
            "text_input": {"supported": False, "code": "text_input_not_supported"},
            "transcription": {
                "available": False,
                "code": "speech_asr_unavailable",
                "note": "audio is stored locally; no speech adapter exists, so the server does "
                        "not understand or transcribe it",
            },
            "spoken_output": {
                "available": False,
                "code": "speech_tts_unavailable",
                "note": "no synthesiser is configured; GET /v1/audio/output reports this",
            },
        }

    def _load(self, session_id: str) -> dict:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM audio_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise NotFound("no such audio session", code="session_not_found")
        return _session_dict(row)

    def _require_open(self, session: dict, client_id: str) -> dict:
        if session["client_id"] != client_id:
            # Same answer as "not found": a client may not probe another client's sessions.
            raise NotFound("no such audio session", code="session_not_found")
        if session["state"] != "open":
            raise Expired("this audio session is no longer open", code="session_closed")
        if parse_iso(session["expires_at"]) <= self.clock.now():
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE audio_sessions SET state = 'expired' WHERE id = ? AND state = 'open'",
                    (session["id"],),
                )
            raise Expired("this audio session expired", code="session_expired")
        return session

    # ------------------------------------------------------------------ audio
    def append_audio(
        self,
        session_id: str,
        *,
        client_id: str,
        content_type: str,
        data: bytes,
        request_id: str | None = None,
    ) -> dict:
        session = self._require_open(self._load(session_id), client_id)
        normalised = (content_type or "").split(";")[0].strip().lower()
        if normalised in TEXTUAL_CONTENT_TYPES:
            raise UnsupportedMediaType(
                "text bodies are not accepted: the server has no text-conversation input; send "
                "encoded audio with an audio/* content type",
                code="text_input_not_supported",
            )
        if not normalised.startswith("audio/"):
            raise UnsupportedMediaType(
                "the audio endpoint accepts audio/* content types only",
                code="unsupported_media_type",
            )
        if not data:
            raise BadRequest("empty audio chunk", code="empty_chunk")
        if len(data) > self.config.storage.max_audio_chunk_bytes:
            raise PayloadTooLarge(
                f"chunk exceeds {self.config.storage.max_audio_chunk_bytes} bytes",
                code="chunk_too_large",
            )
        if session["bytes"] + len(data) > session["max_bytes"]:
            raise PayloadTooLarge(
                f"session would exceed {session['max_bytes']} bytes",
                code="session_too_large",
            )

        index = session["chunks"] + 1
        directory = self._session_dir(client_id, session_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / f"chunk-{index:04d}.bin"
        target.write_bytes(data)
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE audio_sessions SET byte_count = byte_count + ?, chunk_count = chunk_count + 1, "
                "content_type = COALESCE(content_type, ?) WHERE id = ?",
                (len(data), normalised, session_id),
            )
        self.audit.record(
            actor_kind="client", actor_id=client_id, action="audio.chunk_stored",
            target=session_id, outcome="allowed",
            detail={"chunk_index": index, "bytes": len(data), "content_type": normalised},
            request_id=request_id,
        )
        return {
            "session_id": session_id,
            "chunk_index": index,
            "bytes_stored": len(data),
            "session_bytes": session["bytes"] + len(data),
            "transcription": {
                "available": False,
                "code": "speech_asr_unavailable",
                "note": "stored, not transcribed",
            },
        }

    def end(self, session_id: str, *, client_id: str, request_id: str | None = None) -> dict:
        session = self._require_open(self._load(session_id), client_id)
        now = self.clock.now()
        started = parse_iso(session["started_at"])
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE audio_sessions SET state = 'ended', ended_at = ? WHERE id = ? AND state = 'open'",
                (to_iso(now), session_id),
            )
        self.audit.record(
            actor_kind="client", actor_id=client_id, action="audio.session_ended", target=session_id,
            outcome="allowed",
            detail={"bytes": session["bytes"], "chunks": session["chunks"]},
            request_id=request_id,
        )
        return {
            "session_id": session_id,
            "state": "ended",
            "bytes": session["bytes"],
            "chunks": session["chunks"],
            "duration_s": round((now - started).total_seconds(), 3),
            "transcription": {
                "available": False,
                "code": "speech_asr_unavailable",
                "note": "no transcript exists and none was invented",
            },
            "retention": {
                "days": self.config.storage.audio_retention_days,
                "note": "audio is kept locally for at most the configured retention window and is "
                        "deleted with the client on revocation",
            },
        }

    def get(self, session_id: str, *, client_id: str) -> dict:
        session = self._load(session_id)
        if session["client_id"] != client_id:
            raise NotFound("no such audio session", code="session_not_found")
        return session

    # ------------------------------------------------------------------ output
    def output(self) -> dict:
        """Spoken-audio output boundary. There is no synthesiser, so this always refuses."""
        raise Unavailable(
            "spoken audio output is not available: no local synthesiser is configured and no "
            "text response is substituted for audio",
            code="speech_tts_unavailable",
            detail={"text_conversation": False, "gate": "G-O1"},
        )

    # ------------------------------------------------------------------ housekeeping
    def purge_client(self, client_id: str) -> int:
        """Delete a revoked client's stored audio. Returns the number of files removed."""
        removed = 0
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT id FROM audio_sessions WHERE client_id = ?", (client_id,)
            ).fetchall()
        for row in rows:
            directory = self._session_dir(client_id, row["id"])
            if directory.exists():
                removed += sum(1 for path in directory.rglob("*") if path.is_file())
                shutil.rmtree(directory, ignore_errors=True)
        root = self.config.storage.audio_dir / client_id
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE audio_sessions SET state = 'ended' WHERE client_id = ? AND state = 'open'",
                (client_id,),
            )
        return removed

    def purge_expired(self) -> int:
        """Delete audio from sessions older than the retention window."""
        days = self.config.storage.audio_retention_days
        if days <= 0:
            return 0
        cutoff = self.clock.now() - timedelta(days=days)
        removed = 0
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT id, client_id FROM audio_sessions WHERE started_at < ?", (to_iso(cutoff),)
            ).fetchall()
        for row in rows:
            directory = self._session_dir(row["client_id"], row["id"])
            if directory.exists():
                removed += sum(1 for path in directory.rglob("*") if path.is_file())
                shutil.rmtree(directory, ignore_errors=True)
        return removed

    def storage_bytes(self) -> int:
        root = self.config.storage.audio_dir
        if not root.exists():
            return 0
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
