"""The polling companion: hand-over to OS notification, and nothing else.

Shape of one tick:

1. ask the server for **this client's** inbox (the server routes to an explicitly selected target;
   the companion never chooses, filters or invents one);
2. refuse to continue if the server published no display zone, or if an event's rendered time
   disagrees with it — a reminder shown at the wrong hour is a wrong reminder;
3. for each event not already handled, ask the OS to show it and record the verdict;
4. flush acknowledgements the owner has already made but the server has not yet confirmed.

What it does **not** do: acknowledge because a notification was raised (showing is not
acknowledging), retry a notification the OS accepted (a second toast for one reminder is a
duplicate), render times in the PC's own zone, or keep polling after the token is refused.

Cadence: one tick per ``poll_seconds``, capped at 10 by configuration. After a long gap — the machine
slept, or the process was suspended — the next tick happens immediately and is reported as resumed,
because a sleeping client cannot be "online" and the events that came due meanwhile are still due.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .api import ServerApi
from .config import CompanionConfig
from .errors import (
    AuthRejected,
    CompanionError,
    DisplayZoneMissing,
    ProtocolError,
    ServerUnreachable,
)
from .notifier import Notifier, NotificationResult, RenderedReminder
from .state import Journal

LOGGER = logging.getLogger("jarvis.companion")

#: A gap this much longer than the cadence is treated as sleep/suspend rather than ordinary drift.
RESUME_FACTOR = 2.5
#: Ceiling for the offline backoff. Never more than a minute: a client that has been away should
#: notice the server coming back within one cadence of doing so.
MAX_BACKOFF_SECONDS = 60.0
#: How many times one delivery may be offered to the OS before the companion gives up on showing it.
MAX_SHOW_ATTEMPTS = 3

REQUIRED_EVENT_FIELDS = ("delivery_id", "reminder_id", "title", "fire_at", "fire_at_local",
                         "late", "late_by_seconds_exact")


@dataclass(frozen=True)
class TickReport:
    at_utc: str
    status: str                 # ok | unreachable
    events: int = 0
    shown: int = 0
    duplicates: int = 0
    denied: int = 0
    unsupported: int = 0
    failed: int = 0
    acks_confirmed: int = 0
    acks_failed: int = 0
    resumed: bool = False
    message: str | None = None

    def as_dict(self) -> dict:
        return {
            "at": self.at_utc, "status": self.status, "events": self.events, "shown": self.shown,
            "duplicates": self.duplicates, "denied": self.denied,
            "unsupported": self.unsupported, "failed": self.failed,
            "acks_confirmed": self.acks_confirmed, "acks_failed": self.acks_failed,
            "resumed": self.resumed, "message": self.message,
        }


@dataclass
class RunReport:
    ticks: int = 0
    shown: int = 0
    stopped_reason: str = "max_ticks"
    tick_reports: list[TickReport] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ticks": self.ticks, "shown": self.shown, "stopped_reason": self.stopped_reason}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Companion:
    """Owns the poll loop, the journal and the notification verdicts."""

    def __init__(
        self,
        *,
        config: CompanionConfig,
        api: ServerApi,
        token: str,
        journal: Journal,
        notifier: Notifier,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now_utc: Callable[[], str] = _utc_now_iso,
        log: Callable[[str, dict], None] | None = None,
        max_show_attempts: int = MAX_SHOW_ATTEMPTS,
    ) -> None:
        self.config = config
        self.api = api
        self._token = token
        self.journal = journal
        self.notifier = notifier
        self.clock = clock
        self.sleep = sleep
        self.now_utc = now_utc
        self.max_show_attempts = int(max_show_attempts)
        self._log_sink = log
        self._availability: tuple[str, str] = ("unknown", "not checked yet")

    # ------------------------------------------------------------------ logging
    def _log(self, event: str, **fields) -> None:
        payload = {"event": event, **fields}
        if self._log_sink is not None:
            self._log_sink(event, payload)
        else:
            LOGGER.info("%s %s", event, fields)

    # ------------------------------------------------------------------ one tick
    def tick(self, *, resumed: bool = False) -> TickReport:
        """One poll. Raises for conditions that make further polling pointless."""
        at = self.now_utc()
        try:
            payload = self.api.inbox(self._token)
        except ServerUnreachable as exc:
            self._log("server_unreachable", message=str(exc))
            return TickReport(at_utc=at, status="unreachable", resumed=resumed, message=str(exc))

        zone = payload.get("display_timezone")
        if not isinstance(zone, str) or not zone.strip():
            raise DisplayZoneMissing(
                "the server did not publish reminders.display_timezone, so no reminder time can be "
                "rendered honestly; configure it on the server (it is required there) and retry",
                detail={"payload_keys": sorted(payload)},
            )

        events = payload.get("events") or []
        if not isinstance(events, list):
            raise ProtocolError("the inbox events field is not a list")

        state, detail = self._notification_state()
        shown = duplicates = denied = unsupported = failed = 0
        for event in events:
            rendered = self._rendered(event, zone=zone)
            fresh = self.journal.record_receipt(
                delivery_id=rendered.delivery_id, reminder_id=rendered.reminder_id,
                fire_at=str(event["fire_at"]), received_at=at,
            )
            may_show, why = self.journal.should_show(
                rendered.delivery_id, max_attempts=self.max_show_attempts
            )
            if not may_show:
                duplicates += 1
                if fresh:
                    self._log("delivery_seen_again", delivery_id=rendered.delivery_id, why=why)
                continue
            if state != "ok":
                result = NotificationResult(verdict=(
                    "unsupported" if state == "unsupported" else "denied"), message=detail,
                    api=getattr(self.notifier, "api", "unknown"))
            else:
                result = self.notifier.show(rendered)
            self.journal.note_verdict(rendered.delivery_id, result.verdict, reason=result.message,
                                      at=result.at or at)
            if result.verdict == "accepted":
                shown += 1
                self._log("os_accepted", delivery_id=rendered.delivery_id,
                          reminder_id=rendered.reminder_id, api=result.api, at=result.at)
            elif result.verdict == "denied":
                denied += 1
                self._log("os_denied", delivery_id=rendered.delivery_id, message=result.message)
            elif result.verdict == "unsupported":
                unsupported += 1
                self._log("os_unsupported", delivery_id=rendered.delivery_id,
                          message=result.message)
            else:
                failed += 1
                self._log("os_failed", delivery_id=rendered.delivery_id, message=result.message)

        confirmed, acks_failed = self.flush_pending_acks()
        self.journal.set_meta("last_poll_at", at)
        return TickReport(
            at_utc=at, status="ok", events=len(events), shown=shown, duplicates=duplicates,
            denied=denied, unsupported=unsupported, failed=failed,
            acks_confirmed=confirmed, acks_failed=acks_failed, resumed=resumed,
        )

    # ------------------------------------------------------------------ helpers
    def _notification_state(self) -> tuple[str, str]:
        """Ask the OS every tick: the owner may switch notifications off — or back on — at any time.

        The check is two registry reads on Windows. Caching it would mean a client that kept
        refusing to show reminders for the rest of its life after one Settings change.
        """
        state, detail = self.notifier.availability()
        if (state, detail) != self._availability:
            self._availability = (state, detail)
            self._log("notification_availability", state=state, detail=detail)
        return state, detail

    def refresh_notification_state(self) -> tuple[str, str]:
        """Force a re-check now (used by ``doctor`` and by ``run`` between ticks)."""
        self._availability = ("unknown", "not checked yet")
        return self._notification_state()

    def _rendered(self, event: object, *, zone: str) -> RenderedReminder:
        if not isinstance(event, dict):
            raise ProtocolError("the inbox contained an event that is not an object")
        missing = [name for name in REQUIRED_EVENT_FIELDS if name not in event]
        if missing:
            raise ProtocolError(
                "an inbox event is missing fields the companion needs to show it honestly",
                detail={"missing": missing},
            )
        local = event["fire_at_local"]
        if not isinstance(local, dict) or not local.get("iso"):
            raise ProtocolError("an inbox event has no rendered local time")
        event_zone = local.get("timezone")
        if event_zone != zone:
            raise ProtocolError(
                "an event was rendered in a different zone from the one the server published; "
                "refusing to show a time that disagrees with the server",
                detail={"event_zone": event_zone, "published_zone": zone},
            )
        return RenderedReminder(
            title=str(event["title"]),
            fire_at_local_iso=str(local["iso"]),
            display_timezone=str(zone),
            delivery_id=str(event["delivery_id"]),
            reminder_id=str(event["reminder_id"]),
            late=bool(event["late"]),
            late_by_seconds=float(event["late_by_seconds_exact"]),
        )

    def flush_pending_acks(self) -> tuple[int, int]:
        """Confirm acknowledgements the owner already made. Returns (confirmed, still_pending)."""
        confirmed = 0
        failed = 0
        for delivery_id in self.journal.pending_acks():
            try:
                detail = self.api.ack(self._token, delivery_id)
            except ServerUnreachable as exc:
                self.journal.note_ack_failure(delivery_id, str(exc))
                failed += 1
                break
            except ProtocolError as exc:
                code = str(exc.detail.get("code", ""))
                if code in ("delivery_closed", "delivery_not_found"):
                    # The server has closed it: retrying cannot help, and hiding that would be a lie.
                    self.journal.drop_ack(delivery_id)
                    self._log("ack_closed_by_server", delivery_id=delivery_id, code=code,
                              message=str(exc))
                    continue
                self.journal.note_ack_failure(delivery_id, str(exc))
                failed += 1
                break
            self.journal.mark_acknowledged(delivery_id, at=str(detail.get("acknowledged_at") or ""))
            confirmed += 1
            self._log("acknowledged", delivery_id=delivery_id,
                      acknowledged_at=detail.get("acknowledged_at"))
        return confirmed, failed

    # ------------------------------------------------------------------ owner action
    def acknowledge(self, delivery_id: str) -> dict:
        """Acknowledge *because the owner acted*, then report which stage was reached."""
        record = self.journal.get(delivery_id)
        if record is not None and record.acknowledged:
            return {"delivery_id": delivery_id, "stage": "already_acknowledged",
                    "server_confirmed": True, "queued": False}
        try:
            detail = self.api.ack(self._token, delivery_id)
        except ServerUnreachable as exc:
            self.journal.enqueue_ack(delivery_id, error=str(exc))
            self._log("ack_queued", delivery_id=delivery_id, message=str(exc))
            return {"delivery_id": delivery_id, "stage": "queued_offline",
                    "server_confirmed": False, "queued": True, "message": str(exc)}
        except ProtocolError as exc:
            code = str(exc.detail.get("code", ""))
            self.journal.drop_ack(delivery_id)
            self._log("ack_refused", delivery_id=delivery_id, code=code, message=str(exc))
            return {"delivery_id": delivery_id, "stage": code or "refused",
                    "server_confirmed": False, "queued": False, "message": str(exc)}
        self.journal.mark_acknowledged(delivery_id, at=str(detail.get("acknowledged_at") or ""))
        self._log("acknowledged", delivery_id=delivery_id,
                  acknowledged_at=detail.get("acknowledged_at"))
        return {"delivery_id": delivery_id, "stage": "acknowledged", "server_confirmed": True,
                "queued": False, "acknowledged_at": detail.get("acknowledged_at")}

    # ------------------------------------------------------------------ the loop
    def run(self, *, max_ticks: int | None = None) -> RunReport:
        """Poll until a fatal condition, ``max_ticks``, or the caller interrupts."""
        report = RunReport()
        interval = float(self.config.poll_seconds)
        last_start: float | None = None
        failures = 0
        while max_ticks is None or report.ticks < max_ticks:
            resumed = False
            if last_start is not None:
                elapsed = self.clock() - last_start
                if elapsed > interval * RESUME_FACTOR:
                    resumed = True
                    self._log("resumed_after_gap", gap_seconds=round(elapsed, 3))
                else:
                    delay = max(0.0, interval - elapsed)
                    if delay:
                        self.sleep(delay)
            last_start = self.clock()
            try:
                tick = self.tick(resumed=resumed)
            except ServerUnreachable as exc:  # pragma: no cover - tick reports this itself
                tick = TickReport(at_utc=self.now_utc(), status="unreachable", message=str(exc))
            except DisplayZoneMissing as exc:
                report.stopped_reason = "display_zone_missing"
                self._log("stopped", reason="display_zone_missing", message=str(exc))
                return report
            except AuthRejected as exc:
                report.stopped_reason = exc.kind
                self._log("stopped", reason=exc.kind, message=str(exc))
                return report
            except ProtocolError as exc:
                report.stopped_reason = "protocol_error"
                self._log("stopped", reason="protocol_error", message=str(exc))
                return report
            except CompanionError as exc:  # pragma: no cover - defensive
                report.stopped_reason = exc.code
                self._log("stopped", reason=exc.code, message=str(exc))
                return report
            report.ticks += 1
            report.shown += tick.shown
            report.tick_reports.append(tick)
            if tick.status == "unreachable":
                failures += 1
                backoff = min(interval * (2 ** failures), MAX_BACKOFF_SECONDS)
                self._log("backoff", failures=failures, seconds=round(backoff, 1))
                self.sleep(backoff)
            else:
                failures = 0
        if max_ticks is None:  # pragma: no cover - only when the loop is unbounded
            report.stopped_reason = "loop_ended"
        return report


__all__ = [
    "Companion",
    "MAX_BACKOFF_SECONDS",
    "MAX_SHOW_ATTEMPTS",
    "RESUME_FACTOR",
    "RunReport",
    "TickReport",
]
