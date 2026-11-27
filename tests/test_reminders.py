"""Reminder queue: explicit routing, bounded delivery, expiry and revocation.

These exercise the real store and a controllable clock, so "delivered twice within
the retry interval" and "expired after the window" are observed, not assumed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jarvis.auth import AuthService, load_or_create_server_secret
from jarvis.db import Store
from jarvis.reminders import MAX_TITLE, ReminderError, ReminderService


class ManualClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now_value = start or datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)

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
def service(store):
    return ReminderService(store)


@pytest.fixture
def device(tmp_path, store):
    auth = AuthService(store, load_or_create_server_secret(tmp_path / "home"))
    issued = auth.create_pairing_code(label_hint="pixel", platform="android")
    client, _ = auth.redeem_pairing_code(issued["code"])
    return client


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def test_reminder_is_created_normalised_and_persisted(store, service, clock):
    due = _iso(clock.now_value + timedelta(hours=1))
    reminder = service.create(title="  Call   the   bank  ", due_at=due, notes="policy renewal")
    assert reminder.title == "Call the bank"
    assert service.get(reminder.id).notes == "policy renewal"
    assert service.get(reminder.id).status == "pending"
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 1


def test_invalid_reminders_are_refused(service, clock):
    due = _iso(clock.now_value)
    with pytest.raises(ReminderError):
        service.create(title="   ", due_at=due)
    with pytest.raises(ReminderError):
        service.create(title="x" * (MAX_TITLE + 1), due_at=due)
    with pytest.raises(ReminderError):
        service.create(title="fine", due_at="")


def test_due_list_uses_the_stored_time_and_orders_by_it(service, clock):
    service.create(title="later", due_at=_iso(clock.now_value + timedelta(hours=2)))
    service.create(title="now", due_at=_iso(clock.now_value))
    assert [r.title for r in service.list_due()] == ["now"]
    clock.advance(hours=3)
    assert [r.title for r in service.list_due()] == ["now", "later"]      # earliest first


def test_nothing_is_delivered_to_a_client_that_was_not_named(service, clock, device):
    service.create(title="unrouted", due_at=_iso(clock.now_value))
    assert service.poll_inbox(device.id) == []
    assert service.poll_inbox("cli_unknown") == []


def test_delivery_counts_one_attempt_per_retry_interval(service, clock, device):
    service.create(title="stand up", due_at=_iso(clock.now_value), delivery_client_id=device.id)
    first = service.poll_inbox(device.id)
    assert len(first) == 1 and first[0].attempts == 1 and first[0].status == "delivered"
    again = service.poll_inbox(device.id)                  # same interval: no new attempt
    assert again[0].attempts == 1
    clock.advance(minutes=6)
    third = service.poll_inbox(device.id)
    assert third[0].attempts == 2


def test_acknowledgement_closes_both_records_and_is_idempotent(service, clock, device):
    reminder = service.create(title="take pills", due_at=_iso(clock.now_value),
                              delivery_client_id=device.id)
    event = service.poll_inbox(device.id)[0]
    acked = service.acknowledge(event.id)
    assert acked.status == "acknowledged"
    assert service.get(reminder.id).status == "acknowledged"
    assert service.acknowledge(event.id).status == "acknowledged"      # idempotent
    assert service.poll_inbox(device.id) == []


def test_unacknowledged_delivery_expires_after_the_window(service, clock, device):
    service.create(title="old news", due_at=_iso(clock.now_value), delivery_client_id=device.id)
    service.poll_inbox(device.id)
    clock.advance(days=2)
    assert service.poll_inbox(device.id) == []
    with service.store.read() as db:
        row = db.execute("SELECT status FROM reminder_deliveries").fetchone()
    assert row["status"] == "expired"


def test_exhausted_attempts_fail_rather_than_loop_forever(store, clock, device):
    service = ReminderService(store, max_attempts=2, retry_interval_minutes=1)
    service.create(title="retry me", due_at=_iso(clock.now_value), delivery_client_id=device.id)
    for _ in range(3):
        service.poll_inbox(device.id)
        clock.advance(minutes=2)
    service.poll_inbox(device.id)          # the sweep marks it failed once attempts >= max
    with store.read() as db:
        row = db.execute("SELECT status, attempts FROM reminder_deliveries").fetchone()
    assert row["status"] == "failed" and row["attempts"] == 2


def test_revoking_a_client_cancels_its_open_deliveries(service, clock, device):
    service.create(title="cancel me", due_at=_iso(clock.now_value), delivery_client_id=device.id)
    assert service.cancel_for_client(device.id) == 1
    assert service.poll_inbox(device.id) == []
    assert service.cancel_for_client(device.id) == 0


def test_reminders_survive_a_restart(tmp_path, store, service, clock):
    reminder = service.create(title="persist", due_at=_iso(clock.now_value))
    reopened = Store(tmp_path / "jarvis.sqlite3", clock=clock)
    assert ReminderService(reopened).get(reminder.id).title == "persist"


# --------------------------------------------------------------------------- natural language
@pytest.mark.parametrize("phrase,expected", [
    ("in 10 minutes", timedelta(minutes=10)),
    ("in 2 hours", timedelta(hours=2)),
    ("3 days", timedelta(days=3)),
    ("at 18:30", None),                       # clock time, checked separately
])
def test_parse_due_durations(phrase, expected):
    from jarvis.reminders import parse_due
    now = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
    due = parse_due(phrase, now)
    if expected is not None:
        assert due == now + expected


def test_parse_due_clock_and_day_forms():
    from jarvis.reminders import parse_due
    now = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)      # Thursday
    assert parse_due("at 18:30", now) == datetime(2026, 11, 26, 18, 30, tzinfo=timezone.utc)
    assert parse_due("7pm", now) == datetime(2026, 11, 26, 19, 0, tzinfo=timezone.utc)
    assert parse_due("tomorrow at 9am", now) == datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
    assert parse_due("friday at 9", now) == datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
    assert parse_due("2026-12-01T09:00", now) == datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc)


def test_parse_due_refuses_past_ambiguous_or_too_soon():
    from jarvis.reminders import parse_due
    now = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
    for phrase in ("", "   ", "later", "sometime soon", "in 5 seconds", "yesterday"):
        assert parse_due(phrase, now) is None, phrase
    # a bare clock time already past rolls to the next day instead of the past
    assert parse_due("9", now) == datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)


def test_kernel_creates_a_reminder_from_natural_language(app_factory):
    app = app_factory()
    result = app.ask("remind me to call mom in 2 hours")
    assert result["status"] == "completed"
    assert result["intent"] == "remind"
    assert result["actions"][0]["capability"] == "reminder.create"
    assert result["actions"][0]["verified"] is True
    stored = app.reminders.list_all()
    assert len(stored) == 1 and stored[0].title == "call mom"
    assert "call mom" in result["answer"]


def test_kernel_asks_for_a_time_instead_of_inventing_one(app_factory):
    app = app_factory()
    result = app.ask("remind me to stretch")
    assert result["status"] == "completed"
    assert result["actions"] == []                     # nothing executed
    assert "When should I remind you" in result["answer"]
    assert app.reminders.list_all() == []


def test_model_proposed_reminder_waits_for_approval(app_factory):
    """A reminder created on JARVIS's own initiative is a write and must be approved."""
    from jarvis.contracts import ActionRequest, Origin
    app = app_factory()
    outcome = app.tools.propose(ActionRequest("reminder.create", {
        "title": "drink water", "due_at": "2026-12-01T09:00:00+00:00",
        "notes": "", "delivery_client_id": ""}, Origin.MODEL_PROPOSAL, reason="suggested"))
    assert outcome.status.value == "proposed"
    assert app.reminders.list_all() == []
    app.tools.approve(outcome.operation_id, approved_by="owner")
    assert [r.title for r in app.reminders.list_all()] == ["drink water"]
