"""Time source, injectable so expiry and reminders are testable without sleeping."""

from __future__ import annotations

import zoneinfo
from datetime import datetime, timedelta, timezone
from typing import Protocol

#: Stable explanations for the one way a display zone can be wrong. Both are asserted by tests, so an
#: operator gets the same sentence from every entry point (startup, readiness, rendering).
DISPLAY_ZONE_REQUIRED = (
    "reminders.display_timezone is required: name the IANA time zone of the display that shows "
    "reminders (take the name from the host itself, e.g. `timedatectl show -p Timezone`, or a file "
    "under /usr/share/zoneinfo). Stored times are UTC and the server will not guess a display zone."
)
DISPLAY_ZONE_UNKNOWN = (
    "reminders.display_timezone is not a known IANA time zone: {name!r}. Use an Area/City name "
    "(or 'UTC') from this host's tz database; it is resolved locally and never fetched."
)


class Clock(Protocol):
    def now(self) -> datetime:  # pragma: no cover - protocol
        ...


class SystemClock:
    """UTC wall clock. All stored timestamps are timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Deterministic clock for tests; ``advance`` moves it forward explicitly."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 1, 9, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now


def display_zone(name: str, *, fresh: bool = False) -> zoneinfo.ZoneInfo:
    """Resolve an IANA display zone locally, or raise ``ValueError`` with a stable explanation.

    No network is consulted: ``zoneinfo`` reads the host's own tz database. An empty name is a
    configuration error rather than a silent fall back to UTC, because a reminder rendered in the
    wrong zone is a wrong reminder.

    ``fresh=True`` bypasses ``zoneinfo``'s in-process cache, so the answer describes the tz database
    *as it is now* rather than the object this process parsed at startup. That distinction is not
    cosmetic: without it, a host whose tz database lost the zone keeps answering from the cached
    object, and readiness would keep claiming the zone is resolvable. Startup validation and
    rendering use the cache (cheap, and a parsed zone stays correct); readiness and the hand-over
    guard use ``fresh=True`` so they report the current state of the host.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError(DISPLAY_ZONE_REQUIRED)
    try:
        if fresh:
            return zoneinfo.ZoneInfo.no_cache(cleaned)
        return zoneinfo.ZoneInfo(cleaned)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(DISPLAY_ZONE_UNKNOWN.format(name=cleaned)) from exc


def to_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
