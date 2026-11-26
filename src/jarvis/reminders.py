"""Reminder delivery: local queue, explicit routing, poll-and-acknowledge.

Ported from bbff (app/jarvis/reminders.py) and adapted to the Genesis store.
A reminder is queued only for a client named explicitly at creation. The
server never pushes; the target client polls its own queue and acknowledges.

Delivery rules:
- UNIQUE (reminder_id, client_id) prevents duplicates
- Reading an event counts as one attempt (per retry interval, not per poll)
- Exceeding max_delivery_attempts closes the event as failed
- Unacknowledged events older than delivery_window_minutes expire
- Revoking a client cancels its open events immediately
- Timestamps stored in UTC; display timezone is required (never defaulted)
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .util import to_iso

MAX_TITLE = 200
MAX_NOTES = 2000
MIN_FUTURE_SECONDS = 30          # a reminder must be far enough ahead to be actionable

_IN_UNITS = {
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400, "week": 604800, "weeks": 604800,
}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def parse_due(text: str, now: datetime) -> datetime | None:
    """Parse a natural-language due time into an aware UTC datetime, or ``None``.

    Deliberately narrow and honest: an unparsed phrase returns ``None`` so the kernel
    asks the owner instead of inventing a time. Supported forms —

    * durations: ``in 10 minutes``, ``in 2 hours``, ``3 days``, ``90m``
    * days: ``today``, ``tomorrow``, ``friday``, each optionally ``at 9am``
    * clock times: ``at 18:30``, ``7pm`` (next occurrence)
    * ISO-8601: ``2026-12-01T09:00`` / ``2026-12-01T09:00:00Z``

    The result must be at least :data:`MIN_FUTURE_SECONDS` ahead.
    """
    import re
    raw = " ".join((text or "").split()).strip().lower().rstrip(".!")
    raw = re.sub(r"^(?:at|by|on|in|around|before)\s+", "", raw)
    if not raw:
        return None
    now = now.astimezone(timezone.utc)

    try:
        parsed = datetime.fromisoformat(raw.replace("z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        return parsed if _far_enough(parsed, now) else None

    match = re.fullmatch(r"(\d{1,4})\s*(" + "|".join(_IN_UNITS) + r")s?", raw)
    if match:
        due = now + timedelta(seconds=int(match.group(1)) * _IN_UNITS[match.group(2)])
        return due if _far_enough(due, now) else None
    match = re.fullmatch(r"(\d{1,4})\s*(m|h|d)", raw)
    if match:
        due = now + timedelta(seconds=int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400}[match.group(2)])
        return due if _far_enough(due, now) else None

    match = re.fullmatch(r"(today|tomorrow)(?:\s+(.+))?", raw)
    if match:
        clock = _parse_clock((match.group(2) or "").replace("at ", "", 1)) if match.group(2) else (9, 0)
        if clock is None:
            return None
        due = _combine(now, 0 if match.group(1) == "today" else 1, *clock)
        return due if _far_enough(due, now) else None

    match = re.fullmatch(r"(" + "|".join(_WEEKDAYS) + r")(?:\s+(.+))?", raw)
    if match:
        target = _WEEKDAYS.index(match.group(1))
        days = (target - now.weekday()) % 7 or 7
        clock = _parse_clock((match.group(2) or "").replace("at ", "", 1)) if match.group(2) else (9, 0)
        return None if clock is None else _combine(now, days, *clock)

    clock = _parse_clock(raw)
    if clock is not None:
        due = _combine(now, 0, *clock)
        if not _far_enough(due, now):
            due = _combine(now, 1, *clock)
        return due
    return None


def _far_enough(due: datetime, now: datetime) -> bool:
    return (due - now).total_seconds() >= MIN_FUTURE_SECONDS


def _parse_clock(text: str) -> tuple[int, int] | None:
    import re
    text = text.strip().lower().replace(".", "")
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _combine(now: datetime, day_offset: int, hour: int, minute: int) -> datetime:
    base = (now + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return base


@dataclass
class Reminder:
    id: str
    task_id: str | None
    title: str
    notes: str
    due_at: str
    created_at: str
    created_by: str
    delivery_client_id: str | None
    status: str  # pending | delivered | acknowledged | expired | failed
    acknowledged_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "task_id": self.task_id, "title": self.title,
            "notes": self.notes, "due_at": self.due_at, "created_at": self.created_at,
            "created_by": self.created_by, "delivery_client_id": self.delivery_client_id,
            "status": self.status, "acknowledged_at": self.acknowledged_at,
        }


@dataclass
class DeliveryEvent:
    id: str
    reminder_id: str
    client_id: str
    status: str  # queued | delivered | acknowledged | failed | expired | cancelled
    attempts: int
    last_attempt_at: str | None
    delivered_at: str | None
    acknowledged_at: str | None
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id, "reminder_id": self.reminder_id, "client_id": self.client_id,
            "status": self.status, "attempts": self.attempts,
            "last_attempt_at": self.last_attempt_at, "delivered_at": self.delivered_at,
            "acknowledged_at": self.acknowledged_at, "created_at": self.created_at,
        }


class ReminderError(RuntimeError):
    pass


class ReminderService:
    """Manages reminders and their delivery queue."""

    def __init__(self, store, *, max_attempts: int = 5, retry_interval_minutes: int = 5,
                 delivery_window_minutes: int = 1440):
        self.store = store
        self.max_attempts = max_attempts
        self.retry_interval_minutes = retry_interval_minutes
        self.delivery_window_minutes = delivery_window_minutes

    # ------------------------------------------------------------ creation
    def create(self, *, title: str, due_at: str, notes: str = "",
               created_by: str = "operator", delivery_client_id: str | None = None,
               task_id: str | None = None) -> Reminder:
        import secrets
        title = " ".join(title.split())
        if not title:
            raise ReminderError("title is required")
        if len(title) > MAX_TITLE:
            raise ReminderError(f"title must be at most {MAX_TITLE} characters")
        if len(notes) > MAX_NOTES:
            raise ReminderError(f"notes must be at most {MAX_NOTES} characters")
        if not due_at:
            raise ReminderError("due_at is required")

        reminder_id = f"rem_{secrets.token_hex(6)}"
        now = to_iso(self.store.now())
        status = "pending"
        with self.store.write() as db:
            db.execute(
                "INSERT INTO reminders (id, task_id, title, notes, due_at, created_at, "
                "created_by, delivery_client_id, status) VALUES (?,?,?,?,?,?,?,?,?)",
                (reminder_id, task_id, title, notes, due_at, now, created_by,
                 delivery_client_id, status))
            if delivery_client_id:
                delivery_id = f"dlv_{secrets.token_hex(6)}"
                db.execute(
                    "INSERT INTO reminder_deliveries (id, reminder_id, client_id, status, "
                    "created_at) VALUES (?,?,?,?,?)",
                    (delivery_id, reminder_id, delivery_client_id, "queued", now))
        return Reminder(
            id=reminder_id, task_id=task_id, title=title, notes=notes,
            due_at=due_at, created_at=now, created_by=created_by,
            delivery_client_id=delivery_client_id, status=status)

    # ------------------------------------------------------------ queries
    def get(self, reminder_id: str) -> Reminder | None:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return self._row_to_reminder(row) if row else None

    def list_due(self, *, now: str | None = None, limit: int = 50) -> list[Reminder]:
        now = now or to_iso(self.store.now())
        with self.store.read() as db:
            rows = db.execute(
                "SELECT * FROM reminders WHERE due_at <= ? AND status = 'pending' "
                "ORDER BY due_at LIMIT ?", (now, limit)).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    def list_all(self, *, limit: int = 100) -> list[Reminder]:
        with self.store.read() as db:
            rows = db.execute(
                "SELECT * FROM reminders ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    # ------------------------------------------------------------ delivery
    def poll_inbox(self, client_id: str, *, limit: int = 20) -> list[DeliveryEvent]:
        """Poll this client's queued events. Counts as one attempt per retry interval."""
        now = self.store.now()
        now_iso = to_iso(now)
        retry_cutoff = to_iso(now - timedelta(minutes=self.retry_interval_minutes))
        window_cutoff = to_iso(now - timedelta(minutes=self.delivery_window_minutes))

        with self.store.write() as db:
            # Expire old events
            db.execute(
                "UPDATE reminder_deliveries SET status = 'expired' "
                "WHERE client_id = ? AND status IN ('queued', 'delivered') "
                "AND created_at < ?", (client_id, window_cutoff))
            # Fail exhausted events
            db.execute(
                "UPDATE reminder_deliveries SET status = 'failed' "
                "WHERE client_id = ? AND status IN ('queued', 'delivered') "
                "AND attempts >= ?", (client_id, self.max_attempts))
            # Get queued/delivered events
            rows = db.execute(
                "SELECT * FROM reminder_deliveries WHERE client_id = ? "
                "AND status IN ('queued', 'delivered') ORDER BY created_at LIMIT ?",
                (client_id, limit)).fetchall()
            events = []
            for row in rows:
                event = self._row_to_event(row)
                # Count attempt only if outside retry interval
                if event.last_attempt_at is None or event.last_attempt_at < retry_cutoff:
                    new_attempts = event.attempts + 1
                    new_status = "delivered"
                    db.execute(
                        "UPDATE reminder_deliveries SET attempts = ?, last_attempt_at = ?, "
                        "delivered_at = COALESCE(delivered_at, ?), status = ? WHERE id = ?",
                        (new_attempts, now_iso, now_iso, new_status, event.id))
                    event = DeliveryEvent(
                        id=event.id, reminder_id=event.reminder_id, client_id=event.client_id,
                        status=new_status, attempts=new_attempts, last_attempt_at=now_iso,
                        delivered_at=event.delivered_at or now_iso,
                        acknowledged_at=event.acknowledged_at, created_at=event.created_at)
                events.append(event)
        return events

    def acknowledge(self, delivery_id: str) -> DeliveryEvent:
        now_iso = to_iso(self.store.now())
        with self.store.write() as db:
            row = db.execute(
                "SELECT * FROM reminder_deliveries WHERE id = ?", (delivery_id,)).fetchone()
            if row is None:
                raise ReminderError("Delivery not found")
            event = self._row_to_event(row)
            if event.status in ("acknowledged", "failed", "expired", "cancelled"):
                return event  # idempotent
            db.execute(
                "UPDATE reminder_deliveries SET status = 'acknowledged', acknowledged_at = ? "
                "WHERE id = ?", (now_iso, delivery_id))
            db.execute(
                "UPDATE reminders SET status = 'acknowledged', acknowledged_at = ? WHERE id = ?",
                (now_iso, event.reminder_id))
        return DeliveryEvent(
            id=event.id, reminder_id=event.reminder_id, client_id=event.client_id,
            status="acknowledged", attempts=event.attempts, last_attempt_at=event.last_attempt_at,
            delivered_at=event.delivered_at, acknowledged_at=now_iso, created_at=event.created_at)

    def cancel_for_client(self, client_id: str) -> int:
        """Cancel all open deliveries for a revoked client. Returns count."""
        with self.store.write() as db:
            result = db.execute(
                "UPDATE reminder_deliveries SET status = 'cancelled' "
                "WHERE client_id = ? AND status IN ('queued', 'delivered')",
                (client_id,))
            return result.rowcount

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _row_to_reminder(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"], task_id=row["task_id"], title=row["title"], notes=row["notes"],
            due_at=row["due_at"], created_at=row["created_at"], created_by=row["created_by"],
            delivery_client_id=row["delivery_client_id"], status=row["status"],
            acknowledged_at=row["acknowledged_at"])

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> DeliveryEvent:
        return DeliveryEvent(
            id=row["id"], reminder_id=row["reminder_id"], client_id=row["client_id"],
            status=row["status"], attempts=row["attempts"],
            last_attempt_at=row["last_attempt_at"], delivered_at=row["delivered_at"],
            acknowledged_at=row["acknowledged_at"], created_at=row["created_at"])
