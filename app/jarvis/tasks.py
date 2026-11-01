"""Tasks and reminders: the first real everyday-assistance capability.

This is deliberately model-free. A task is an explicit record (title, optional note,
optional due time) that a paired client creates, lists, completes or deletes; a reminder is
derived from a due time and can be acknowledged. Everything is local, in SQLite, and every
mutation is audited.

Honesty rules encoded here:

* there is no natural-language parsing — a client that wants voice entry needs the speech
  adapters, which are unavailable, so the response says so instead of pretending;
* delivery is a local queue retrieved by an explicitly targeted, paired client (see
  :mod:`app.jarvis.reminders`). The server never claims to have "reminded" anyone: it never
  pushes, speaks, renders or contacts a device, and a reminder with no target stays pending and is
  reported as unrouted rather than sent to whoever happens to be paired;
* validation is explicit (lengths, timestamps, status transitions, delivery target) rather than
  best-effort.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Iterable

from .audit import AuditLog
from .clock import Clock, parse_iso, to_iso
from .db import Store
from .errors import BadRequest, NotFound

MAX_TITLE = 200
MAX_NOTES = 2000
MAX_FUTURE_DAYS = 3650  # ten years; a guard against typos, not a product limit


def has_explicit_offset(text: str) -> bool:
    """True when an ISO-8601 timestamp names its offset (``Z`` or ``±HH:MM``).

    A wall-clock time without an offset is ambiguous twice a year, so it is recorded as an
    assumption instead of being presented as the owner's local time.
    """
    stripped = text.strip()
    if stripped.endswith(("Z", "z")):
        return True
    body = stripped[10:] if len(stripped) > 10 else ""
    return "+" in body or "-" in body


def _task_dict(row) -> dict:  # noqa: ANN001 - sqlite3.Row
    return {
        "id": row["id"],
        "title": row["title"],
        "notes": row["notes"],
        "due_at": row["due_at"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "source": row["source"],
        "completed_at": row["completed_at"],
        "status": "completed" if row["completed_at"] else "open",
        # How the schedule was supplied, recorded when it was written: nothing is re-derived
        # from the normalised value, which would make every timestamp look explicit.
        "due_time_basis": row["due_at_basis"],
        "delivery_client_id": row["delivery_client_id"],
    }


class TaskService:
    def __init__(self, store: Store, clock: Clock, audit: AuditLog, deliveries=None) -> None:
        self.store = store
        self.clock = clock
        self.audit = audit
        # Optional so the service can be exercised on its own; the app always supplies it.
        self.deliveries = deliveries

    # ------------------------------------------------------------------ validation
    def _validate_title(self, title: str) -> str:
        if not isinstance(title, str):
            raise BadRequest("title must be a string")
        cleaned = " ".join(title.split())
        if not cleaned:
            raise BadRequest("title is required", code="title_required")
        if len(cleaned) > MAX_TITLE:
            raise BadRequest(f"title must be at most {MAX_TITLE} characters", code="title_too_long")
        return cleaned

    def _validate_notes(self, notes: str | None) -> str:
        if notes is None:
            return ""
        if not isinstance(notes, str):
            raise BadRequest("notes must be a string")
        if len(notes) > MAX_NOTES:
            raise BadRequest(f"notes must be at most {MAX_NOTES} characters", code="notes_too_long")
        return notes

    def _validate_due(self, due_at: str | None) -> datetime | None:
        if due_at in (None, ""):
            return None
        if not isinstance(due_at, str):
            raise BadRequest("due_at must be an ISO-8601 string", code="due_at_invalid")
        try:
            moment = parse_iso(due_at)
        except ValueError as exc:
            raise BadRequest("due_at must be an ISO-8601 timestamp", code="due_at_invalid") from exc
        horizon = self.clock.now().timestamp() + MAX_FUTURE_DAYS * 86400
        if moment.timestamp() > horizon:
            raise BadRequest("due_at is implausibly far in the future", code="due_at_invalid")
        return moment

    def _validate_delivery_target(self, deliver_to: str | None, due_moment) -> str | None:
        """The target must be explicit, existing, live and allowed to receive reminders."""
        if deliver_to in (None, ""):
            return None
        if not isinstance(deliver_to, str):
            raise BadRequest("deliver_to must be a client id", code="delivery_target_invalid")
        if due_moment is None:
            raise BadRequest(
                "deliver_to needs a due_at: a reminder is only created when a schedule is set",
                code="delivery_target_without_schedule",
            )
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT id, scopes, revoked_at FROM clients WHERE id = ?", (deliver_to,)
            ).fetchone()
        if row is None:
            raise BadRequest(
                "deliver_to names an unknown client; targets are never guessed",
                code="delivery_target_unknown",
            )
        if row["revoked_at"]:
            raise BadRequest("deliver_to names a revoked client", code="delivery_target_revoked")
        if "reminders.receive" not in json.loads(row["scopes"]):
            raise BadRequest(
                "the target client does not carry the reminders.receive scope",
                code="delivery_target_scope_missing",
            )
        return row["id"]

    # ------------------------------------------------------------------ tasks
    def create(
        self,
        *,
        title: str,
        notes: str | None = None,
        due_at: str | None = None,
        source: str = "structured",
        deliver_to: str | None = None,
        actor_id: str,
        request_id: str | None = None,
    ) -> dict:
        cleaned_title = self._validate_title(title)
        cleaned_notes = self._validate_notes(notes)
        due_moment = self._validate_due(due_at)
        target_client = self._validate_delivery_target(deliver_to, due_moment)
        if source not in {"structured", "voice"}:
            raise BadRequest("source must be 'structured' or 'voice'", code="source_invalid")

        task_id = f"tsk_{secrets.token_hex(6)}"
        now = to_iso(self.clock.now())
        due_iso = to_iso(due_moment) if due_moment else None
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO tasks (id, title, notes, due_at, created_at, created_by, source, "
                "due_at_basis) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, cleaned_title, cleaned_notes, due_iso, now, actor_id, source,
                 ("explicit_offset" if due_at and has_explicit_offset(due_at)
                  else "assumed_utc" if due_iso else None)),
            )
            reminder_id = None
            if due_iso:
                reminder_id = f"rmd_{secrets.token_hex(6)}"
                connection.execute(
                    "INSERT INTO reminders (id, task_id, fire_at, created_at, delivery_client_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (reminder_id, task_id, due_iso, now, target_client),
                )
        task = self.get(task_id)
        if reminder_id:
            task["reminder_id"] = reminder_id
        # Queueing happens after the reminder is committed, and is idempotent: if the process
        # dies between the two, reconcile() queues it on the next read instead of losing it.
        if reminder_id and target_client and self.deliveries is not None:
            delivery = self.deliveries.enqueue(
                reminder_id=reminder_id, client_id=target_client, fire_at=due_iso,
                request_id=request_id, actor_id=actor_id,
            )
            task["delivery"] = {
                "queued_for": target_client,
                "delivery_id": delivery.id if delivery else None,
                "state": delivery.state if delivery else "pending",
                "model": "local queue retrieved by that client; nothing is pushed or spoken",
            }
        elif reminder_id:
            task["delivery"] = {
                "queued_for": None,
                "state": "pending_unrouted",
                "reason": "no_delivery_target",
                "note": "this reminder has no target, so it is reported as pending and will not "
                        "be delivered until a target is configured",
            }
        task["voice_input"] = {
            "available": source == "voice",
            "note": "audio is stored locally but not transcribed: no speech adapter is "
                    "configured (see /v1/limitations)",
        }
        self.audit.record(
            actor_kind="client",
            actor_id=actor_id,
            action="tasks.created",
            target=task_id,
            outcome="allowed",
            detail={"source": source, "has_due_at": bool(due_iso),
                    "routed": bool(target_client),
                    "due_time_basis": ("explicit_offset"
                                       if due_at and has_explicit_offset(due_at)
                                       else "assumed_utc" if due_iso else None)},
            request_id=request_id,
        )
        return task

    def get(self, task_id: str) -> dict:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT t.*, r.delivery_client_id AS delivery_client_id FROM tasks t "
                "LEFT JOIN reminders r ON r.task_id = t.id "
                "WHERE t.id = ? AND t.deleted_at IS NULL", (task_id,)
            ).fetchone()
        if row is None:
            raise NotFound("no such task", code="task_not_found")
        return _task_dict(row)

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict]:
        if status not in (None, "open", "completed", "all"):
            raise BadRequest("status must be one of open, completed, all", code="status_invalid")
        if not 1 <= int(limit) <= 500:
            raise BadRequest("limit must be between 1 and 500")
        # One reminder per task, so the join cannot duplicate a row.
        query = ("SELECT t.*, r.delivery_client_id AS delivery_client_id FROM tasks t "
                 "LEFT JOIN reminders r ON r.task_id = t.id WHERE t.deleted_at IS NULL")
        params: list = []
        if status in (None, "open"):
            query += " AND t.completed_at IS NULL"
        elif status == "completed":
            query += " AND t.completed_at IS NOT NULL"
        query += " ORDER BY COALESCE(t.due_at, t.created_at), t.id LIMIT ?"
        params.append(int(limit))
        with self.store.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_task_dict(row) for row in rows]

    def complete(self, task_id: str, *, actor_id: str, request_id: str | None = None) -> dict:
        task = self.get(task_id)
        if task["completed_at"]:
            return task
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET completed_at = ? WHERE id = ? AND completed_at IS NULL",
                (to_iso(self.clock.now()), task_id),
            )
        self.audit.record(
            actor_kind="client", actor_id=actor_id, action="tasks.completed", target=task_id,
            outcome="allowed", request_id=request_id,
        )
        return self.get(task_id)

    def delete(self, task_id: str, *, actor_id: str, request_id: str | None = None) -> dict:
        task = self.get(task_id)
        with self.store.connection() as connection:
            queued = connection.execute(
                "SELECT COUNT(*) AS n FROM reminder_deliveries d JOIN reminders r "
                "ON r.id = d.reminder_id WHERE r.task_id = ?", (task_id,)
            ).fetchone()["n"]
            reminders = connection.execute(
                "SELECT COUNT(*) AS n FROM reminders WHERE task_id = ?", (task_id,)
            ).fetchone()["n"]
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET deleted_at = ? WHERE id = ?", (to_iso(self.clock.now()), task_id)
            )
            connection.execute("DELETE FROM reminders WHERE task_id = ?", (task_id,))
        self.audit.record(
            actor_kind="client", actor_id=actor_id, action="tasks.deleted", target=task_id,
            # Ids and counts only: the title is already hashed and the note is never audited.
            outcome="allowed",
            detail={"title_sha": hashlib_prefix(task["title"]),
                    "reminders_deleted": int(reminders),
                    "queued_deliveries_deleted": int(queued)},
            request_id=request_id,
        )
        return {"id": task_id, "deleted": True}

    # ------------------------------------------------------------------ reminders
    def due_reminders(self, *, now: str | None = None, limit: int = 100) -> list[dict]:
        moment = parse_iso(now) if now else self.clock.now()
        if not 1 <= int(limit) <= 500:
            raise BadRequest("limit must be between 1 and 500")
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT r.*, t.title AS title, t.due_at AS due_at FROM reminders r "
                "JOIN tasks t ON t.id = r.task_id "
                "WHERE r.acknowledged_at IS NULL AND t.deleted_at IS NULL "
                "AND r.fire_at <= ? ORDER BY r.fire_at, r.id LIMIT ?",
                (to_iso(moment), int(limit)),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "title": row["title"],
                "fire_at": row["fire_at"],
                "acknowledged_at": row["acknowledged_at"],
                "delivery": {
                    "routed": row["delivery_client_id"] is not None,
                    "queued_for": row["delivery_client_id"],
                    "pushed": False,
                    "spoken": False,
                    "displayed": False,
                    "note": ("queued for that paired client to retrieve by polling; the server "
                             "does not push, speak or render it")
                    if row["delivery_client_id"] else
                            ("no delivery target was configured, so nothing was queued; this "
                             "reminder stays pending and unrouted"),
                },
            }
            for row in rows
        ]

    def acknowledge(
        self, reminder_id: str, *, actor_id: str, request_id: str | None = None
    ) -> dict:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        if row is None:
            raise NotFound("no such reminder", code="reminder_not_found")
        if row["acknowledged_at"] is None:
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE reminders SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
                    (to_iso(self.clock.now()), reminder_id),
                )
            cancelled = (self.deliveries.cancel_for_reminder(
                reminder_id, reason="acknowledged_by_client") if self.deliveries else 0)
            self.audit.record(
                actor_kind="client", actor_id=actor_id, action="reminders.acknowledged",
                target=reminder_id, outcome="allowed",
                detail={"cancelled_deliveries": cancelled}, request_id=request_id,
            )
        with self.store.connection() as connection:
            updated = connection.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        return {
            "id": updated["id"],
            "task_id": updated["task_id"],
            "fire_at": updated["fire_at"],
            "acknowledged_at": updated["acknowledged_at"],
        }

    def counts(self) -> dict:
        with self.store.connection() as connection:
            open_tasks = connection.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE deleted_at IS NULL AND completed_at IS NULL"
            ).fetchone()["n"]
            completed = connection.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE deleted_at IS NULL AND completed_at IS NOT NULL"
            ).fetchone()["n"]
            reminders = connection.execute(
                "SELECT COUNT(*) AS n FROM reminders WHERE acknowledged_at IS NULL"
            ).fetchone()["n"]
        return {"open": int(open_tasks), "completed": int(completed), "unacknowledged_reminders": int(reminders)}


def hashlib_prefix(text: str) -> str:
    """Short stable prefix so a deletion can be audited without storing the title again."""
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def summarise(tasks: Iterable[dict]) -> dict:
    tasks = list(tasks)
    return {
        "count": len(tasks),
        "due": sum(1 for task in tasks if task.get("due_at")),
        "voice_sourced": sum(1 for task in tasks if task.get("source") == "voice"),
    }
