"""Durable local reminder delivery to explicitly paired clients.

**What "delivery" means here.** A delivery is a row in a local queue that a *paired client* asks
for. The server never pushes, never speaks, never renders and never contacts a device: there is no
message broker, no vendor service and no socket in this module. A reminder reaches its client when
that client polls ``GET /v1/reminders/inbox``, and it is finished when the client acknowledges it.
Calling this "delivery" is accurate only with that meaning attached, and every response says so.

**What is explicit.** The schedule (``due_at``) and the target (``deliver_to``) are both set by the
client that creates the task; nothing here infers a target, a device preference or a recurrence.
A reminder with no target is left pending and *reported* as unrouted with a reason — the service
never picks a recipient, not even when there is exactly one paired client.

**Why it is safe to retry.** ``UNIQUE (reminder_id, client_id)`` makes enqueueing idempotent, and
every state change is a conditional ``UPDATE ... WHERE state = ?`` inside an ``IMMEDIATE``
transaction. A duplicate request, a retried poll, a crashed process or a restart therefore cannot
produce a second delivery, move an acknowledgement timestamp or lose an attempt count.

**Attempts.** The first hand-over of an event counts as attempt 1. A further hand-over counts as a
new attempt only if at least ``retry_interval_minutes`` have passed since the last one, so a client
that polls every second does not burn its retry budget. Once ``max_delivery_attempts`` is exceeded
the delivery is closed as ``failed`` with reason ``attempts_exhausted`` and is no longer served as
a pending event, but it is still reported (without its contents) so the client can see why nothing
appeared.

**Time.** Every stored timestamp is UTC. ``display_timezone`` is used only to *render* a local wall
clock for a human, with the zone's rules at that instant, so the two 01:30s on a daylight-saving
fall-back day are two distinct instants and each is delivered once. The service never converts a
local wall-clock time back into an instant, which is why it cannot invent a time that does not
exist (a spring-forward gap). Naive timestamps are accepted for compatibility and recorded as
``time_basis: "assumed_utc"`` rather than silently treated as the owner's local time.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from .audit import AuditLog
from .clock import Clock, parse_iso, to_iso
from .config import RemindersConfig
from .db import Store
from .errors import BadRequest, Conflict, NotFound

#: A hand-over this long after the scheduled time is labelled late. Below it, the difference is
#: ordinary scheduling jitter rather than a missed reminder.
LATE_AFTER_S = 60

DELIVERY_MODEL = (
    "local queue: a paired client retrieves events by polling and acknowledges them. The server "
    "does not push, speak, render or contact a device."
)

STATE_REASONS = {
    "expired": "delivery_window_elapsed",
    "failed": "attempts_exhausted",
    "cancelled": "client_revoked",
}


@dataclass(frozen=True)
class Delivery:
    id: str
    reminder_id: str
    client_id: str
    state: str
    enqueued_at: str
    expires_at: str
    attempts: int
    max_attempts: int
    first_delivered_at: str | None
    last_delivered_at: str | None
    acknowledged_at: str | None
    closed_at: str | None
    failure_reason: str | None


def _delivery(row) -> Delivery:  # noqa: ANN001 - sqlite3.Row
    return Delivery(
        id=row["id"],
        reminder_id=row["reminder_id"],
        client_id=row["client_id"],
        state=row["state"],
        enqueued_at=row["enqueued_at"],
        expires_at=row["expires_at"],
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        first_delivered_at=row["first_delivered_at"],
        last_delivered_at=row["last_delivered_at"],
        acknowledged_at=row["acknowledged_at"],
        closed_at=row["closed_at"],
        failure_reason=row["failure_reason"],
    )


class ReminderDeliveryService:
    """The queue, its state machine, and the audit trail of both."""

    def __init__(
        self,
        store: Store,
        clock: Clock,
        audit: AuditLog,
        config: RemindersConfig,
    ) -> None:
        self.store = store
        self.clock = clock
        self.audit = audit
        self.config = config

    # ------------------------------------------------------------------ queueing
    def enqueue(
        self,
        *,
        reminder_id: str,
        client_id: str,
        fire_at: str,
        request_id: str | None = None,
        actor_id: str | None = None,
    ) -> Delivery | None:
        """Queue one reminder for one explicit client. Idempotent by construction."""
        fire = parse_iso(fire_at)
        expires_at = to_iso(fire + timedelta(minutes=self.config.delivery_window_minutes))
        delivery_id = f"dlv_{secrets.token_hex(6)}"
        now = to_iso(self.clock.now())
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO reminder_deliveries "
                "(id, reminder_id, client_id, state, enqueued_at, attempts, max_attempts, "
                " expires_at) VALUES (?, ?, ?, 'pending', ?, 0, ?, ?)",
                (delivery_id, reminder_id, client_id, now, self.config.max_delivery_attempts,
                 expires_at),
            )
            inserted = cursor.rowcount == 1
        if not inserted:
            # Already queued: return the existing row rather than a second one.
            existing = self._find(reminder_id=reminder_id, client_id=client_id)
            return existing
        self.audit.record(
            actor_kind="client" if actor_id else "system",
            actor_id=actor_id,
            action="reminder_delivery.enqueued",
            target=delivery_id,
            outcome="info",
            detail={"client_id": client_id, "expires_at": expires_at},
            request_id=request_id,
        )
        return self._find(reminder_id=reminder_id, client_id=client_id)

    def reconcile(self) -> int:
        """Queue anything an explicit target was set for but which has no row yet.

        This is what makes the queue durable across a crash between "task committed" and
        "delivery committed": the next read repairs it, and the UNIQUE constraint makes the
        repair harmless when it runs twice.
        """
        with self.store.connection() as connection:
            missing = connection.execute(
                "SELECT r.id AS reminder_id, r.fire_at AS fire_at, r.delivery_client_id AS client_id "
                "FROM reminders r LEFT JOIN reminder_deliveries d "
                "  ON d.reminder_id = r.id AND d.client_id = r.delivery_client_id "
                "WHERE r.delivery_client_id IS NOT NULL AND d.id IS NULL"
            ).fetchall()
        repaired = 0
        for row in missing:
            if self.enqueue(reminder_id=row["reminder_id"], client_id=row["client_id"],
                            fire_at=row["fire_at"]):
                repaired += 1
        if repaired:
            self.audit.record(
                actor_kind="system", actor_id=None, action="reminder_delivery.reconciled",
                target=None, outcome="info", detail={"queued": repaired},
            )
        return repaired

    def unrouted_reminders(self, *, now: datetime | None = None) -> list[dict]:
        """Due reminders that have no target: visible, pending, and explained — never guessed."""
        moment = now or self.clock.now()
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT r.id, r.task_id, r.fire_at FROM reminders r "
                "JOIN tasks t ON t.id = r.task_id "
                "WHERE r.delivery_client_id IS NULL AND r.acknowledged_at IS NULL "
                "AND t.deleted_at IS NULL AND r.fire_at <= ? ORDER BY r.fire_at, r.id",
                (to_iso(moment),),
            ).fetchall()
        return [
            {
                "reminder_id": row["id"],
                "task_id": row["task_id"],
                "fire_at": row["fire_at"],
                "state": "pending_unrouted",
                "reason": "no_delivery_target",
                "note": "no delivery target was configured, so nothing was queued; set "
                        "deliver_to on the task to route it",
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ transitions
    def sweep(self, *, now: datetime | None = None) -> dict:
        """Apply the time-based transitions once, idempotently, and audit each one."""
        moment = now or self.clock.now()
        moment_iso = to_iso(moment)
        reconciled = self.reconcile()
        expired = self._close(
            "expired", reason=STATE_REASONS["expired"], moment_iso=moment_iso,
            predicate="state IN ('pending', 'delivered') AND expires_at < ?", params=(moment_iso,),
        )
        exhausted = self._close(
            "failed", reason=STATE_REASONS["failed"], moment_iso=moment_iso,
            predicate="state IN ('pending', 'delivered') AND attempts >= max_attempts",
            params=(),
        )
        return {"reconciled": reconciled, "expired": expired, "failed": exhausted}

    def _close(self, state: str, *, reason: str, moment_iso: str, predicate: str,
               params: tuple) -> int:
        with self.store.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE reminder_deliveries SET state = ?, closed_at = ?, failure_reason = ? "
                f"WHERE {predicate}",
                (state, moment_iso, reason, *params),
            )
            changed = cursor.rowcount or 0
        if changed:
            self.audit.record(
                actor_kind="system", actor_id=None,
                action=f"reminder_delivery.{state}", target=None, outcome="info",
                detail={"count": changed, "reason": reason},
            )
        return changed

    def cancel_for_client(self, client_id: str, *, reason: str = "client_revoked",
                          actor_kind: str = "system", actor_id: str | None = None) -> int:
        """A revoked client loses its queue immediately; the rows stay for the audit trail."""
        now = to_iso(self.clock.now())
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE reminder_deliveries SET state = 'cancelled', closed_at = ?, "
                "failure_reason = ? WHERE client_id = ? AND state IN ('pending', 'delivered')",
                (now, reason, client_id),
            )
            changed = cursor.rowcount or 0
        if changed:
            self.audit.record(
                actor_kind=actor_kind, actor_id=actor_id,
                action="reminder_delivery.cancelled", target=client_id, outcome="info",
                detail={"count": changed, "reason": reason},
            )
        return changed

    def cancel_for_reminder(self, reminder_id: str, *, reason: str) -> int:
        now = to_iso(self.clock.now())
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE reminder_deliveries SET state = 'cancelled', closed_at = ?, "
                "failure_reason = ? WHERE reminder_id = ? AND state IN ('pending', 'delivered')",
                (now, reason, reminder_id),
            )
            changed = cursor.rowcount or 0
        if changed:
            self.audit.record(
                actor_kind="client", actor_id=None, action="reminder_delivery.cancelled",
                target=reminder_id, outcome="info", detail={"count": changed, "reason": reason},
            )
        return changed

    # ------------------------------------------------------------------ reading
    def inbox(
        self, *, client_id: str, now: datetime | None = None, limit: int = 50
    ) -> dict:
        """Hand over this client's actionable events. Not a push: the client asked."""
        if not 1 <= int(limit) <= 200:
            raise BadRequest("limit must be between 1 and 200")
        moment = now or self.clock.now()
        self.sweep(now=moment)

        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT d.*, r.fire_at AS fire_at, r.task_id AS task_id, t.title AS title "
                "FROM reminder_deliveries d "
                "JOIN reminders r ON r.id = d.reminder_id "
                "JOIN tasks t ON t.id = r.task_id "
                "WHERE d.client_id = ? AND d.state IN ('pending', 'delivered') "
                "AND r.acknowledged_at IS NULL AND t.deleted_at IS NULL "
                "ORDER BY r.fire_at, d.id LIMIT ?",
                (client_id, int(limit)),
            ).fetchall()

        events: list[dict] = []
        scheduled = 0
        for row in rows:
            fire = parse_iso(row["fire_at"])
            if fire > moment:
                scheduled += 1
                continue
            event = self._hand_over(row, moment)
            if event is not None:
                events.append(event)

        held_back = self._held_back(client_id, limit=limit)
        return {
            "events": events,
            "count": len(events),
            "held_back": held_back,
            "counts": {
                "due_now": len(events),
                "scheduled": scheduled,
                "held_back": len(held_back),
                "acknowledged": self._count_state(client_id, "acknowledged"),
                "unrouted_reminders": len(self.unrouted_reminders(now=moment)),
            },
            "delivery": {
                "model": DELIVERY_MODEL,
                "pushed": False,
                "spoken": False,
                "displayed": False,
                "transport": "client-initiated polling over the authenticated local API",
            },
            "display_timezone": self.config.display_timezone,
        }

    def _hand_over(self, row, moment: datetime) -> dict | None:  # noqa: ANN001 - sqlite3.Row
        delivery = _delivery(row)
        fire = parse_iso(row["fire_at"])
        now_iso = to_iso(moment)
        due_since_s = max(0.0, (moment - fire).total_seconds())

        if delivery.first_delivered_at is None:
            new_attempts = 1
        else:
            last = parse_iso(delivery.last_delivered_at or delivery.first_delivered_at)
            elapsed = (moment - last).total_seconds()
            if elapsed < self.config.retry_interval_minutes * 60:
                new_attempts = delivery.attempts  # same attempt: an idempotent re-read
            else:
                new_attempts = delivery.attempts + 1
        if new_attempts > delivery.max_attempts:
            # Close it here rather than serving an event we have already given up on.
            self._fail_exhausted(delivery.id, now_iso)
            return None

        first = delivery.first_delivered_at or now_iso
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE reminder_deliveries SET state = 'delivered', attempts = ?, "
                "first_delivered_at = COALESCE(first_delivered_at, ?), last_delivered_at = ? "
                "WHERE id = ? AND state IN ('pending', 'delivered')",
                (new_attempts, now_iso, now_iso, delivery.id),
            )
        if new_attempts != delivery.attempts:
            self.audit.record(
                actor_kind="client", actor_id=delivery.client_id,
                action="reminder_delivery.attempt", target=delivery.id, outcome="info",
                detail={"attempt": new_attempts, "max_attempts": delivery.max_attempts,
                        "late_by_seconds": int(due_since_s)},
            )

        late_by = max(0, int((parse_iso(first) - fire).total_seconds()))
        return {
            "delivery_id": delivery.id,
            "reminder_id": delivery.reminder_id,
            "task_id": row["task_id"],
            "title": row["title"],
            "fire_at": row["fire_at"],
            "fire_at_local": self._render_local(fire),
            "state": "delivered",
            "attempt": new_attempts,
            "max_attempts": delivery.max_attempts,
            "expires_at": delivery.expires_at,
            "late": late_by > LATE_AFTER_S,
            "late_by_seconds": late_by,
            "delivery": {
                "model": DELIVERY_MODEL,
                "pushed": False,
                "spoken": False,
                "displayed": False,
                "note": "retrieved by this client; the server has not spoken or displayed it",
            },
        }

    def _fail_exhausted(self, delivery_id: str, now_iso: str) -> None:
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE reminder_deliveries SET state = 'failed', closed_at = ?, "
                "failure_reason = ? WHERE id = ? AND state IN ('pending', 'delivered')",
                (now_iso, STATE_REASONS["failed"], delivery_id),
            )
            closed = cursor.rowcount or 0
        if closed:
            self.audit.record(
                actor_kind="system", actor_id=None, action="reminder_delivery.failed",
                target=delivery_id, outcome="info",
                detail={"reason": STATE_REASONS["failed"]},
            )

    def _count_state(self, client_id: str, state: str) -> int:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM reminder_deliveries WHERE client_id = ? AND state = ?",
                (client_id, state),
            ).fetchone()
        return int(row["n"])

    def _held_back(self, client_id: str, *, limit: int) -> list[dict]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT d.id, d.reminder_id, d.state, d.failure_reason, d.closed_at, "
                "r.fire_at FROM reminder_deliveries d JOIN reminders r ON r.id = d.reminder_id "
                "WHERE d.client_id = ? AND d.state IN ('expired', 'failed', 'cancelled') "
                "ORDER BY d.closed_at DESC LIMIT ?",
                (client_id, int(limit)),
            ).fetchall()
        return [
            {
                "delivery_id": row["id"],
                "reminder_id": row["reminder_id"],
                "state": row["state"],
                "reason": row["failure_reason"],
                "closed_at": row["closed_at"],
                "fire_at": row["fire_at"],
                "not_delivered_because": {
                    "expired": "the delivery window elapsed before the client acknowledged it",
                    "failed": "the retry limit was reached without an acknowledgement",
                    "cancelled": "the target was revoked or the reminder was acknowledged elsewhere",
                }.get(row["state"], "closed"),
            }
            for row in rows
        ]

    def _render_local(self, moment: datetime) -> dict:
        """Render a UTC instant as a wall clock in the configured zone. Never the reverse."""
        import zoneinfo

        zone = zoneinfo.ZoneInfo(self.config.display_timezone)
        local = moment.astimezone(zone)
        return {
            "iso": local.isoformat(),
            "timezone": self.config.display_timezone,
            "utc_offset_seconds": int(local.utcoffset().total_seconds()),
        }

    # ------------------------------------------------------------------ acknowledgement
    def acknowledge(
        self, delivery_id: str, *, client_id: str, request_id: str | None = None
    ) -> dict:
        """Acknowledge one of *this client's* deliveries. Idempotent; never crosses clients."""
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM reminder_deliveries WHERE id = ? AND client_id = ?",
                (delivery_id, client_id),
            ).fetchone()
        if row is None:
            # Another client's delivery is indistinguishable from a missing one on purpose.
            raise NotFound("no such delivery for this client", code="delivery_not_found")
        delivery = _delivery(row)
        if delivery.state in ("expired", "failed", "cancelled"):
            raise Conflict(
                "this delivery is closed and cannot be acknowledged",
                code="delivery_closed",
                detail={"state": delivery.state, "reason": delivery.failure_reason},
            )

        now_iso = to_iso(self.clock.now())
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE reminder_deliveries SET state = 'acknowledged', "
                "acknowledged_at = COALESCE(acknowledged_at, ?), closed_at = COALESCE(closed_at, ?) "
                "WHERE id = ? AND state IN ('pending', 'delivered', 'acknowledged')",
                (now_iso, now_iso, delivery_id),
            )
            connection.execute(
                "UPDATE reminders SET acknowledged_at = COALESCE(acknowledged_at, ?) WHERE id = ?",
                (now_iso, delivery.reminder_id),
            )
        others = self.cancel_for_reminder(delivery.reminder_id, reason="acknowledged_elsewhere")
        self.audit.record(
            actor_kind="client", actor_id=client_id, action="reminder_delivery.acknowledged",
            target=delivery_id, outcome="allowed",
            detail={"reminder_id": delivery.reminder_id, "cancelled_others": others,
                    "was_attempt": delivery.attempts},
            request_id=request_id,
        )
        updated = self._get(delivery_id)
        return {
            "delivery_id": updated.id,
            "reminder_id": updated.reminder_id,
            "state": updated.state,
            "acknowledged_at": updated.acknowledged_at,
            "attempts": updated.attempts,
            "delivery": {"model": DELIVERY_MODEL, "spoken": False, "displayed": False},
        }

    # ------------------------------------------------------------------ reporting
    def _find(self, *, reminder_id: str, client_id: str) -> Delivery | None:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM reminder_deliveries WHERE reminder_id = ? AND client_id = ?",
                (reminder_id, client_id),
            ).fetchone()
        return _delivery(row) if row else None

    def _get(self, delivery_id: str) -> Delivery:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM reminder_deliveries WHERE id = ?", (delivery_id,)
            ).fetchone()
        if row is None:  # pragma: no cover - the row was read moments ago
            raise NotFound("no such delivery", code="delivery_not_found")
        return _delivery(row)

    def counts(self) -> dict:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS n FROM reminder_deliveries GROUP BY state"
            ).fetchall()
            unrouted = connection.execute(
                "SELECT COUNT(*) AS n FROM reminders WHERE delivery_client_id IS NULL "
                "AND acknowledged_at IS NULL"
            ).fetchone()["n"]
        by_state = {row["state"]: int(row["n"]) for row in rows}
        return {
            "by_state": by_state,
            "total": sum(by_state.values()),
            "unrouted_reminders": int(unrouted),
            "delivery_model": DELIVERY_MODEL,
        }

__all__ = [
    "DELIVERY_MODEL",
    "Delivery",
    "LATE_AFTER_S",
    "ReminderDeliveryService",
]
