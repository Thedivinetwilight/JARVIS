"""Audio sessions: local capture, hard bounds, expiry and revocation purge.

No speech adapter exists, so these tests only claim what the code does: bytes are
stored locally under explicit limits and deleted on expiry or revocation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jarvis.audio_sessions import (
    MAX_CHUNK_BYTES,
    MAX_SESSION_BYTES,
    AudioError,
    AudioSessionService,
)
from jarvis.db import Store


class ManualClock:
    def __init__(self) -> None:
        self.now_value = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now_value

    def advance(self, **kwargs) -> None:
        self.now_value += timedelta(**kwargs)


@pytest.fixture
def clock():
    return ManualClock()


@pytest.fixture
def store(tmp_path, clock):
    return Store(tmp_path / "jarvis.sqlite3", clock=clock)


@pytest.fixture
def audio(store):
    return AudioSessionService(store, ttl_minutes=30)


def test_chunks_accumulate_and_are_stored_locally(store, audio):
    session = audio.open_session("operator")
    assert session.status == "open" and session.chunk_count == 0
    first = audio.append_chunk(session.id, b"\x00\x01" * 100)
    second = audio.append_chunk(session.id, b"\x02" * 50)
    assert (first["seq"], second["seq"]) == (1, 2)
    updated = audio.get_session(session.id)
    assert updated.chunk_count == 2 and updated.total_bytes == 250
    with store.read() as db:
        stored = db.execute("SELECT SUM(size_bytes) FROM audio_chunks").fetchone()[0]
    assert stored == 250


def test_chunk_bounds_are_enforced(audio):
    session = audio.open_session("operator")
    with pytest.raises(AudioError, match="Chunk exceeds"):
        audio.append_chunk(session.id, b"\x00" * (MAX_CHUNK_BYTES + 1))
    with pytest.raises(AudioError, match="audio/"):
        audio.append_chunk(session.id, b"data", mime_type="text/plain")
    assert audio.get_session(session.id).chunk_count == 0          # refused chunks are not stored


def test_session_total_size_limit_is_enforced(store):
    service = AudioSessionService(store)
    session = service.open_session("operator")
    chunk = b"\x00" * MAX_CHUNK_BYTES
    written = 0
    with pytest.raises(AudioError, match="Session exceeds"):
        while written <= MAX_SESSION_BYTES:
            service.append_chunk(session.id, chunk)
            written += len(chunk)
    assert service.get_session(session.id).total_bytes <= MAX_SESSION_BYTES


def test_closed_session_refuses_more_audio(audio):
    session = audio.open_session("operator")
    closed = audio.close_session(session.id)
    assert closed.status == "closed" and closed.closed_at is not None
    with pytest.raises(AudioError, match="closed"):
        audio.append_chunk(session.id, b"more")


def test_expired_sessions_are_purged_with_their_bytes(store, clock, audio):
    session = audio.open_session("operator")
    audio.append_chunk(session.id, b"\x00" * 32)
    clock.advance(minutes=31)
    assert audio.purge_expired() == 1
    assert audio.get_session(session.id).status == "expired"
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM audio_chunks").fetchone()[0] == 0


def test_appending_after_expiry_is_refused(store, clock, audio):
    session = audio.open_session("operator")
    clock.advance(minutes=31)
    with pytest.raises(AudioError, match="expired"):
        audio.append_chunk(session.id, b"\x00")
    assert audio.get_session(session.id).status == "expired"


def test_revoking_a_client_deletes_every_chunk_it_uploaded(store, audio):
    phone = audio.open_session("cli_phone")
    other = audio.open_session("cli_tablet")
    audio.append_chunk(phone.id, b"\x00" * 16)
    audio.append_chunk(other.id, b"\x00" * 16)
    assert audio.purge_for_client("cli_phone") == 1
    assert audio.get_session(phone.id).status == "purged"
    with store.read() as db:
        remaining = db.execute("SELECT session_id, COUNT(*) FROM audio_chunks GROUP BY session_id").fetchall()
    assert [r["session_id"] for r in remaining] == [other.id]
    assert audio.get_session(other.id).status == "open"


def test_unknown_session_is_reported_not_silently_ignored(audio):
    with pytest.raises(AudioError, match="not found"):
        audio.append_chunk("aud_missing", b"\x00")
    with pytest.raises(AudioError, match="not found"):
        audio.close_session("aud_missing")
    assert audio.get_session("aud_missing") is None
