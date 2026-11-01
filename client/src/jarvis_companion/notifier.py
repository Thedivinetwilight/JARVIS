"""The notification boundary: what a reminder looks like, and what the OS said.

Four stages stay distinct and are never merged into "shown":

``handed_over``   the server put the event in this client's inbox
``received``      this client parsed it and journalled it
``os_accept``     the OS notification API accepted the display request (this module's verdict)
``acknowledged``  the owner acted and the server confirmed it

A toast the OS refused is ``denied``; a toast the platform cannot raise at all is ``unsupported``;
anything that raises while raising it is ``failed``. Only ``accepted`` may be reported as "the OS
took it", and even then nothing here claims a human saw it.

No backend here speaks, plays audio, opens a window or accepts text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class RenderedReminder:
    """What the notification will say. Built only from the server's own rendering."""

    title: str
    fire_at_local_iso: str
    display_timezone: str
    delivery_id: str
    reminder_id: str
    late: bool
    late_by_seconds: float

    @property
    def body(self) -> str:
        when = f"{self.fire_at_local_iso} ({self.display_timezone})"
        if self.late:
            when += f" — handed over {self.late_by_seconds:.3f} s after the scheduled time"
        return f"{when}\nFrom your local J.A.R.V.I.S. server. Not spoken; there is no reply."


@dataclass(frozen=True)
class NotificationResult:
    verdict: str          # accepted | denied | unsupported | failed
    message: str
    api: str              # which OS facility was asked
    at: str | None = None

    @property
    def accepted(self) -> bool:
        return self.verdict == "accepted"


class Notifier(Protocol):
    """One display request in, one verdict out."""

    api: str

    def availability(self) -> tuple[str, str]:
        """``ok``/``denied``/``unsupported`` plus a sentence the owner can act on."""

    def show(self, reminder: RenderedReminder) -> NotificationResult: ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class NullNotifier:
    """The honest backend when no OS notification facility exists (tests, other platforms)."""

    api = "none"

    def __init__(self, reason: str = "no OS notification backend on this platform") -> None:
        self.reason = reason

    def availability(self) -> tuple[str, str]:
        return "unsupported", self.reason

    def show(self, reminder: RenderedReminder) -> NotificationResult:
        return NotificationResult(
            verdict="unsupported",
            message=f"{self.reason}; nothing was shown for {reminder.delivery_id}",
            api=self.api,
        )


class RecordingNotifier:
    """Test double: records requests and returns a scripted verdict.

    It is a double, not a stand-in for Windows: no test in this repository claims that it shows
    anything, and the platform checks in ``doctor`` are reported unverified without a Windows host.
    """

    api = "recording-test-double"

    def __init__(self, verdict: str = "accepted", *,
                 availability: tuple[str, str] = ("ok", "test double")) -> None:
        self.verdict = verdict
        self._availability = availability
        self.shown: list[RenderedReminder] = []
        self.calls = 0

    def availability(self) -> tuple[str, str]:
        return self._availability

    def set_verdict(self, verdict: str, *, availability: tuple[str, str] | None = None) -> None:
        self.verdict = verdict
        if availability is not None:
            self._availability = availability

    def show(self, reminder: RenderedReminder) -> NotificationResult:
        self.calls += 1
        self.shown.append(reminder)
        return NotificationResult(
            verdict=self.verdict,
            message=f"test double verdict {self.verdict!r}",
            api=self.api,
            at=utc_now_iso(),
        )


__all__ = ["NotificationResult", "Notifier", "NullNotifier", "RecordingNotifier",
           "RenderedReminder", "utc_now_iso"]
