"""The local journal: which deliveries this client has handled, and in which stage.

It answers exactly one question per delivery — may I show this again? — and keeps a record of the four
stages the owner asked to keep apart:

``handed_over``  the server returned the event in the inbox (server-side)
``received``     this client parsed it and wrote this row
``os_accept``    the OS notification API accepted (or refused) the display request
``acknowledged`` the owner acted, and the server confirmed it

**No reminder content is stored.** Titles are shown in the notification and then forgotten: the
journal holds ids, timestamps, verdicts and counters only, so losing or copying it cannot leak what
the owner was reminded about.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id   TEXT PRIMARY KEY,
    reminder_id   TEXT NOT NULL,
    fire_at       TEXT NOT NULL,
    received_at   TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    verdict_reason TEXT,
    show_attempts INTEGER NOT NULL DEFAULT 0,
    acknowledged  INTEGER NOT NULL DEFAULT 0,
    ack_at        TEXT
);
CREATE TABLE IF NOT EXISTS pending_acks (
    delivery_id TEXT PRIMARY KEY,
    queued_at   TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

#: Verdicts the notifier may return. ``accepted`` is the only one that means the OS took the request.
VERDICTS = ("accepted", "denied", "unsupported", "failed")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: str
    reminder_id: str
    fire_at: str
    received_at: str
    verdict: str
    verdict_reason: str | None
    show_attempts: int
    acknowledged: bool
    ack_at: str | None


class Journal:
    """SQLite journal in the companion's state directory. Safe to open from one process at a time."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "Journal":  # pragma: no cover - trivial
        return self

    def __exit__(self, *exc) -> None:  # pragma: no cover - trivial
        self.close()

    # ------------------------------------------------------------------ deliveries
    def seen(self, delivery_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM deliveries WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
        return row is not None

    def record_receipt(self, *, delivery_id: str, reminder_id: str, fire_at: str,
                       received_at: str | None = None) -> bool:
        """Record that the client has this event. False when it was already known."""
        if self.seen(delivery_id):
            return False
        self._connection.execute(
            "INSERT INTO deliveries (delivery_id, reminder_id, fire_at, received_at, verdict) "
            "VALUES (?, ?, ?, ?, ?)",
            (delivery_id, reminder_id, fire_at, received_at or utc_now_iso(), "received"),
        )
        self._connection.commit()
        return True

    def note_verdict(self, delivery_id: str, verdict: str, *, reason: str | None = None,
                     at: str | None = None) -> None:
        """Record what the OS did with the display request. Bumps the attempt counter."""
        if verdict not in VERDICTS:
            raise ValueError(f"unknown verdict {verdict!r}")
        self._connection.execute(
            "UPDATE deliveries SET verdict = ?, verdict_reason = ?, show_attempts = show_attempts + 1 "
            "WHERE delivery_id = ?",
            (verdict, reason, delivery_id),
        )
        if verdict == "accepted" and at:
            self._connection.execute(
                "UPDATE deliveries SET ack_at = ack_at WHERE delivery_id = ?", (delivery_id,)
            )
        self._connection.commit()

    def should_show(self, delivery_id: str, *, max_attempts: int = 3) -> tuple[bool, str]:
        """Whether this delivery may still be shown, and why not when it may not.

        Rules, in order: already accepted → never again (the owner was shown it); accepted is
        terminal because a second toast for one reminder is a duplicate, not a retry.
        """
        row = self._connection.execute(
            "SELECT verdict, show_attempts FROM deliveries WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
        if row is None:
            return True, "new"
        if row["verdict"] == "accepted":
            return False, "already_accepted"
        if int(row["show_attempts"]) >= max_attempts:
            return False, "attempts_exhausted"
        return True, f"retrying_after_{row['verdict']}"

    def mark_acknowledged(self, delivery_id: str, *, at: str | None = None) -> None:
        self._connection.execute(
            "UPDATE deliveries SET acknowledged = 1, ack_at = ? WHERE delivery_id = ?",
            (at or utc_now_iso(), delivery_id),
        )
        self._connection.execute("DELETE FROM pending_acks WHERE delivery_id = ?", (delivery_id,))
        self._connection.commit()

    def get(self, delivery_id: str) -> DeliveryRecord | None:
        row = self._connection.execute(
            "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
        if row is None:
            return None
        return DeliveryRecord(
            delivery_id=row["delivery_id"], reminder_id=row["reminder_id"], fire_at=row["fire_at"],
            received_at=row["received_at"], verdict=row["verdict"],
            verdict_reason=row["verdict_reason"], show_attempts=int(row["show_attempts"]),
            acknowledged=bool(row["acknowledged"]), ack_at=row["ack_at"],
        )

    def recent(self, limit: int = 10) -> list[DeliveryRecord]:
        rows = self._connection.execute(
            "SELECT * FROM deliveries ORDER BY received_at DESC, delivery_id LIMIT ?", (int(limit),)
        ).fetchall()
        return [
            DeliveryRecord(
                delivery_id=row["delivery_id"], reminder_id=row["reminder_id"],
                fire_at=row["fire_at"], received_at=row["received_at"], verdict=row["verdict"],
                verdict_reason=row["verdict_reason"], show_attempts=int(row["show_attempts"]),
                acknowledged=bool(row["acknowledged"]), ack_at=row["ack_at"],
            )
            for row in rows
        ]

    def last_unacknowledged(self) -> DeliveryRecord | None:
        """The most recent delivery the owner could still acknowledge with ``ack --last``."""
        row = self._connection.execute(
            "SELECT * FROM deliveries WHERE acknowledged = 0 AND verdict = 'accepted' "
            "ORDER BY received_at DESC, delivery_id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self.get(row["delivery_id"])

    def counts(self) -> dict[str, int]:
        rows = self._connection.execute(
            "SELECT verdict, COUNT(*) AS n FROM deliveries GROUP BY verdict"
        ).fetchall()
        counts = {verdict: 0 for verdict in VERDICTS}
        for row in rows:
            counts[row["verdict"]] = int(row["n"])
        counts["acknowledged"] = int(self._connection.execute(
            "SELECT COUNT(*) FROM deliveries WHERE acknowledged = 1"
        ).fetchone()[0])
        counts["pending_acks"] = int(self._connection.execute(
            "SELECT COUNT(*) FROM pending_acks"
        ).fetchone()[0])
        return counts

    # ------------------------------------------------------------------ acknowledgements
    def enqueue_ack(self, delivery_id: str, *, error: str | None = None,
                    at: str | None = None) -> None:
        self._connection.execute(
            "INSERT INTO pending_acks (delivery_id, queued_at, attempts, last_error) "
            "VALUES (?, ?, 0, ?) ON CONFLICT(delivery_id) DO UPDATE SET last_error = excluded.last_error",
            (delivery_id, at or utc_now_iso(), error),
        )
        self._connection.commit()

    def note_ack_failure(self, delivery_id: str, error: str) -> None:
        self._connection.execute(
            "UPDATE pending_acks SET attempts = attempts + 1, last_error = ? WHERE delivery_id = ?",
            (error, delivery_id),
        )
        self._connection.commit()

    def pending_acks(self) -> list[str]:
        rows = self._connection.execute(
            "SELECT delivery_id FROM pending_acks ORDER BY queued_at, delivery_id"
        ).fetchall()
        return [row["delivery_id"] for row in rows]

    def drop_ack(self, delivery_id: str) -> None:
        self._connection.execute("DELETE FROM pending_acks WHERE delivery_id = ?", (delivery_id,))
        self._connection.commit()

    # ------------------------------------------------------------------ meta
    def set_meta(self, key: str, value: str) -> None:
        self._connection.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._connection.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


__all__ = ["DeliveryRecord", "Journal", "VERDICTS", "utc_now_iso"]
