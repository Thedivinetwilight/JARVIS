"""Tests for durable local reminder delivery to explicitly paired clients.

The behaviour under test, in the order the classes appear:

1. the schema (a fresh migration, an upgrade from 0002, the uniqueness guarantee, cascade delete);
2. access control (scopes, targeting, cross-client isolation, revocation);
3. the delivery flow itself and its idempotency;
4. durability across a restart, plus the repair path for an interrupted write;
5. scheduling edges: offsets, daylight saving in both directions, late and expired reminders,
   retry limits;
6. revocation while events are queued;
7. the audit trail, which must record the lifecycle without recording reminder contents;
8. egress, which must stay at zero because delivery is a local queue.

Deterministic tests establish no rate, no latency and no reliability percentage: ``FixedClock``
makes every scheduling case exact, and no test measures anything.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import sqlite3
import tempfile
import unittest

from app.jarvis import manifests, reporting
from app.jarvis.db import MIGRATIONS_DIR, Store
from app.jarvis.errors import ConfigError
from app.jarvis.egress import GUARD
from app.tests import support
from app.tests.support import ApiTestCase

TITLE = "dentist appointment"
NOTE = "bring the referral letter and the insurance card"


def _rows(store: Store, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with store.connection() as connection:
        return connection.execute(sql, params).fetchall()


class DeliveryBase(ApiTestCase):
    """A paired phone that is the explicit target of one reminder."""

    def setUp(self) -> None:
        super().setUp()
        self.device, self.token = self.pair_device(label="phone", platform="phone")
        self.client_id = self.device["id"]

    # ------------------------------------------------------------------ helpers
    def make_reminder(self, *, due_at="2026-11-01T10:00:00Z", deliver_to=None,
                      title=TITLE, notes=NOTE, token=None):
        body = {"title": title, "notes": notes, "due_at": due_at}
        target = self.client_id if deliver_to is None else deliver_to
        if target is not False:
            body["deliver_to"] = target
        return self.call("POST", "/v1/tasks", token=token or self.token, body=body)

    def inbox(self, *, token=None, limit=None):
        path = "/v1/reminders/inbox" + (f"?limit={limit}" if limit is not None else "")
        return self.call("GET", path, token=token or self.token)

    def ack(self, delivery_id: str, *, token=None):
        return self.call("POST", f"/v1/reminders/inbox/{delivery_id}/ack",
                         token=token or self.token)

    def deliveries(self) -> list[sqlite3.Row]:
        return _rows(self.app.store, "SELECT * FROM reminder_deliveries ORDER BY enqueued_at, id")

    def restart(self):
        """Rebuild the application over the same data directory and clock."""
        self.app, self.config = support.build_app(self.directory, clock=self.clock)
        return self.app


class ReminderDeliverySchemaTests(DeliveryBase):
    def test_a_fresh_database_creates_the_queue_and_its_indexes(self):
        with self.app.store.connection() as connection:
            tables = {row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            indexes = {row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'")}
            columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(reminder_deliveries)")}
            reminder_columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(reminders)")}
        self.assertIn("reminder_deliveries", tables)
        self.assertIn("delivery_client_id", reminder_columns)
        self.assertLessEqual(
            {"id", "reminder_id", "client_id", "state", "enqueued_at", "attempts",
             "max_attempts", "expires_at", "failure_reason", "acknowledged_at",
             "first_delivered_at", "last_delivered_at", "closed_at"}, columns)
        self.assertLessEqual({"idx_reminder_deliveries_inbox", "idx_reminder_deliveries_open",
                              "idx_reminders_delivery_client"}, indexes)

    def test_an_existing_reminder_table_accepts_the_target_column_through_an_upgrade(self):
        """A database migrated to 0002 keeps its reminders and gains the queue in 0003."""
        with tempfile.TemporaryDirectory() as staging:
            staging_path = pathlib.Path(staging)
            for name in ("0001_core.sql", "0002_tasks_reminders.sql"):
                shutil.copy(MIGRATIONS_DIR / name, staging_path / name)
            old = Store(self.directory / "upgrade.sqlite3", migrations_dir=staging_path)
            old.migrate()
            with old.transaction() as connection:
                connection.execute(
                    "INSERT INTO tasks (id, title, notes, due_at, created_at, created_by, "
                    "source, completed_at, deleted_at) VALUES ('tsk_old', 'from before', '', "
                    "'2026-11-01T10:00:00Z', '2026-11-01T08:00:00Z', 'test', 'structured', "
                    "NULL, NULL)")
                connection.execute(
                    "INSERT INTO reminders (id, task_id, fire_at, created_at, acknowledged_at) "
                    "VALUES ('rmd_old', 'tsk_old', '2026-11-01T10:00:00Z', "
                    "'2026-11-01T08:00:00Z', NULL)")
            upgraded = Store(self.directory / "upgrade.sqlite3")
            result = upgraded.migrate()
            self.assertEqual(result.applied, ("0003_reminder_delivery.sql",))
            row = _rows(upgraded, "SELECT * FROM reminders WHERE id = 'rmd_old'")[0]
        # An old reminder has no target, so it is unrouted rather than routed somewhere.
        self.assertIsNone(row["delivery_client_id"])
        self.assertEqual(row["fire_at"], "2026-11-01T10:00:00Z")
        old_task = _rows(upgraded, "SELECT due_at_basis FROM tasks WHERE id = 'tsk_old'")[0]
        # The pre-existing row never recorded how its timestamp was written, so it stays unknown.
        self.assertIsNone(old_task["due_at_basis"])

    def test_the_unique_constraint_makes_enqueueing_idempotent(self):
        _, payload = self.make_reminder()
        reminder_id = payload["task"]["reminder_id"]
        service = self.app.deliveries
        again = service.enqueue(reminder_id=reminder_id, client_id=self.client_id,
                                fire_at="2026-11-01T10:00:00Z")
        self.assertIsNotNone(again)
        rows = _rows(self.app.store,
                     "SELECT * FROM reminder_deliveries WHERE reminder_id = ?", (reminder_id,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(again.id, rows[0]["id"])

    def test_queue_rows_are_deleted_with_their_reminder(self):
        _, payload = self.make_reminder()
        task_id = payload["task"]["id"]
        self.assertEqual(len(self.deliveries()), 1)
        response, _ = self.call("DELETE", f"/v1/tasks/{task_id}", token=self.token)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.deliveries(), [])

    def test_migrating_a_second_time_changes_nothing(self):
        self.app.store.migrate()
        result = self.app.store.migrate()
        self.assertEqual(result.applied, ())
        self.assertEqual(result.already_applied, 3)


class DeliveryAccessTests(DeliveryBase):
    def test_the_inbox_needs_the_receive_scope(self):
        narrowed = self.pair_device(scopes=["tasks.read", "tasks.write", "reminders.read"],
                                    label="narrow", platform="tablet")[1]
        response, payload = self.inbox(token=narrowed)
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["code"], "scope_denied")
        self.assertEqual(payload["error"]["detail"]["required_scope"], "reminders.receive")

    def test_a_narrowed_client_can_still_see_due_reminders(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        narrowed = self.pair_device(scopes=["reminders.read"], label="reader",
                                    platform="tablet")[1]
        response, payload = self.call("GET", "/v1/reminders/due", token=narrowed)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["count"], 1)

    def test_acknowledgement_needs_the_receive_scope(self):
        _, payload = self.make_reminder()
        self.clock.advance(hours=2)
        delivery_id = self.inbox()[1]["events"][0]["delivery_id"]
        narrowed = self.pair_device(scopes=["reminders.read", "reminders.write"],
                                    label="writer", platform="tablet")[1]
        response, payload = self.ack(delivery_id, token=narrowed)
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["detail"]["required_scope"], "reminders.receive")

    def test_the_device_scopes_never_include_administration(self):
        for scope in manifests.ADMIN_SCOPES:
            self.assertNotIn(scope, manifests.DEFAULT_DEVICE_SCOPES)
        from app.jarvis.errors import BadRequest

        with self.assertRaises(BadRequest) as caught:
            self.app.auth.create_pairing_code(
                label_hint="sneaky", platform="phone", scopes=["clients.read"])
        self.assertEqual(caught.exception.code, "scope_not_grantable")

    def test_one_client_cannot_acknowledge_another_clients_delivery(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        delivery_id = self.inbox()[1]["events"][0]["delivery_id"]
        other = self.pair_device(label="tablet", platform="tablet")[1]
        response, payload = self.ack(delivery_id, token=other)
        # Indistinguishable from "does not exist", so the id is not confirmed to a stranger.
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"]["code"], "delivery_not_found")
        state = _rows(self.app.store, "SELECT state FROM reminder_deliveries")[0]["state"]
        self.assertEqual(state, "delivered")

    def test_a_revoked_token_cannot_reach_its_inbox(self):
        doomed, token = self.pair_device(label="doomed", platform="phone")
        self.call("POST", f"/v1/clients/{doomed['id']}/revoke", token=self.operator_token)
        response, payload = self.inbox(token=token)
        self.assertEqual(response.status, 401)
        self.assertEqual(payload["error"]["code"], "token_revoked")

    def test_a_target_must_exist_be_live_and_be_scoped(self):
        unknown, payload = self.make_reminder(deliver_to="cli_doesnotexist")
        self.assertEqual(unknown.status, 400)
        self.assertEqual(payload["error"]["code"], "delivery_target_unknown")

        # The target's scopes are what matters, so the creator is the fully scoped device.
        reader, _reader_token = self.pair_device(scopes=["tasks.read"],
                                                 label="reader", platform="phone")
        unscoped, payload = self.make_reminder(deliver_to=reader["id"])
        self.assertEqual(unscoped.status, 400)
        self.assertEqual(payload["error"]["code"], "delivery_target_scope_missing")

        # Revoking the target is the last case: it also invalidates this device's own token.
        self.call("POST", f"/v1/clients/{self.client_id}/revoke", token=self.operator_token)
        revoked, payload = self.make_reminder(deliver_to=self.client_id,
                                              token=self.operator_token)
        self.assertEqual(revoked.status, 400)
        self.assertEqual(payload["error"]["code"], "delivery_target_revoked")

    def test_a_target_without_a_schedule_is_refused(self):
        response, payload = self.call(
            "POST", "/v1/tasks", token=self.token,
            body={"title": TITLE, "deliver_to": self.client_id})
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "delivery_target_without_schedule")

    def test_no_target_is_reported_rather_than_guessed(self):
        _, payload = self.make_reminder(deliver_to=False)
        task = payload["task"]
        self.assertEqual(task["delivery"]["state"], "pending_unrouted")
        self.assertEqual(task["delivery"]["reason"], "no_delivery_target")
        self.assertEqual(self.deliveries(), [])

        self.clock.advance(hours=2)
        _, payload = self.inbox()
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["counts"]["unrouted_reminders"], 1)
        _, due = self.call("GET", "/v1/reminders/due", token=self.token)
        self.assertEqual(due["count"], 1)
        self.assertFalse(due["reminders"][0]["delivery"]["routed"])
        self.assertIn("no delivery target", due["reminders"][0]["delivery"]["note"])


class DeliveryFlowTests(DeliveryBase):
    def test_a_reminder_reaches_only_its_explicit_target(self):
        _, payload = self.make_reminder()
        self.assertEqual(payload["task"]["delivery"]["queued_for"], self.client_id)
        self.clock.advance(hours=2)

        mine, payload = self.inbox()
        self.assertEqual(mine.status, 200)
        self.assertEqual(payload["count"], 1)
        event = payload["events"][0]
        self.assertEqual(event["title"], TITLE)
        self.assertEqual(event["state"], "delivered")
        self.assertEqual(event["attempt"], 1)

        stranger = self.pair_device(label="tablet", platform="tablet")[1]
        theirs, payload = self.inbox(token=stranger)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["counts"]["due_now"], 0)

    def test_reading_twice_does_not_create_a_second_delivery(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        first = self.inbox()[1]
        second = self.inbox()[1]
        self.assertEqual(first["events"][0]["delivery_id"], second["events"][0]["delivery_id"])
        self.assertEqual(second["events"][0]["attempt"], 1)
        self.assertEqual(len(self.deliveries()), 1)

    def test_acknowledgement_is_idempotent(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        delivery_id = self.inbox()[1]["events"][0]["delivery_id"]
        first, payload = self.ack(delivery_id)
        self.assertEqual(first.status, 200)
        stamp = payload["delivery"]["acknowledged_at"]
        self.clock.advance(minutes=30)
        second, payload = self.ack(delivery_id)
        self.assertEqual(second.status, 200)
        self.assertEqual(payload["delivery"]["acknowledged_at"], stamp)
        self.assertEqual(payload["delivery"]["state"], "acknowledged")

    def test_acknowledging_clears_the_due_list(self):
        _, payload = self.make_reminder()
        reminder_id = payload["task"]["reminder_id"]
        self.clock.advance(hours=2)
        delivery_id = self.inbox()[1]["events"][0]["delivery_id"]
        self.ack(delivery_id)
        _, due = self.call("GET", "/v1/reminders/due", token=self.token)
        self.assertEqual(due["count"], 0)
        reminder = _rows(self.app.store, "SELECT * FROM reminders WHERE id = ?", (reminder_id,))[0]
        self.assertIsNotNone(reminder["acknowledged_at"])

    def test_events_are_not_returned_before_their_due_time(self):
        self.make_reminder(due_at="2026-11-01T10:00:00Z")
        response, payload = self.inbox()
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["counts"]["scheduled"], 1)
        self.assertEqual(payload["events"], [])

    def test_the_payload_never_claims_push_speech_or_display(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        _, payload = self.inbox()
        delivery = payload["delivery"]
        self.assertFalse(delivery["pushed"])
        self.assertFalse(delivery["spoken"])
        self.assertFalse(delivery["displayed"])
        self.assertIn("does not push, speak, render or contact a device", delivery["model"])
        event = payload["events"][0]
        self.assertFalse(event["delivery"]["spoken"])
        self.assertFalse(event["delivery"]["displayed"])
        rendered = json.dumps(payload).lower()
        for claim in ("voice delivered", "spoken aloud", "read aloud", "notification sent",
                      "pushed to the device", "delivered by voice"):
            self.assertNotIn(claim, rendered)

    def test_the_inbox_limit_is_bounded(self):
        response, payload = self.inbox(limit=0)
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")
        response, payload = self.inbox(limit=500)
        self.assertEqual(response.status, 400)
        response, payload = self.call("GET", "/v1/reminders/inbox?limit=abc", token=self.token)
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")

    def test_the_legacy_acknowledge_path_also_closes_the_queue(self):
        _, payload = self.make_reminder()
        reminder_id = payload["task"]["reminder_id"]
        self.clock.advance(hours=2)
        response, _ = self.call("POST", f"/v1/reminders/{reminder_id}/ack", token=self.token)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.deliveries()[0]["state"], "cancelled")
        self.assertEqual(self.deliveries()[0]["failure_reason"], "acknowledged_by_client")
        self.assertEqual(self.inbox()[1]["count"], 0)


class PersistenceTests(DeliveryBase):
    def test_the_queue_survives_a_restart(self):
        _, payload = self.make_reminder()
        reminder_id = payload["task"]["reminder_id"]
        self.clock.advance(hours=2)
        self.inbox()
        self.restart()
        response, payload = self.inbox()
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["events"][0]["reminder_id"], reminder_id)
        self.assertEqual(len(self.deliveries()), 1)

    def test_an_acknowledgement_survives_a_restart(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        delivery_id = self.inbox()[1]["events"][0]["delivery_id"]
        _, acked = self.ack(delivery_id)
        stamp = acked["delivery"]["acknowledged_at"]
        self.restart()
        _, payload = self.inbox()
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["counts"]["acknowledged"], 1)
        replayed, payload = self.ack(delivery_id)
        self.assertEqual(replayed.status, 200)
        self.assertEqual(payload["delivery"]["acknowledged_at"], stamp)

    def test_a_missing_queue_row_is_repaired_exactly_once(self):
        """A crash between "reminder committed" and "delivery committed" must not lose the event."""
        self.make_reminder()
        with self.app.store.transaction() as connection:
            connection.execute("DELETE FROM reminder_deliveries")
        self.clock.advance(hours=2)
        _, payload = self.inbox()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(self.deliveries()), 1)
        self.inbox()
        self.assertEqual(len(self.deliveries()), 1)
        actions = [entry.action for entry in self.app.audit.tail(50)]
        self.assertIn("reminder_delivery.reconciled", actions)

    def test_deleting_a_task_removes_its_queued_events(self):
        _, payload = self.make_reminder()
        task_id = payload["task"]["id"]
        self.clock.advance(hours=2)
        self.inbox()
        self.call("DELETE", f"/v1/tasks/{task_id}", token=self.token)
        self.assertEqual(self.deliveries(), [])
        _, payload = self.inbox()
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["counts"]["due_now"], 0)

    def test_deleting_a_task_reports_what_it_removed_without_the_contents(self):
        _, payload = self.make_reminder()
        task_id = payload["task"]["id"]
        self.call("DELETE", f"/v1/tasks/{task_id}", token=self.token)
        entry = next(item for item in self.audit_entries() if item["action"] == "tasks.deleted")
        detail = entry["detail"]
        self.assertEqual(detail["reminders_deleted"], 1)
        self.assertEqual(detail["queued_deliveries_deleted"], 1)
        self.assertNotIn(TITLE, json.dumps(entry))
        self.assertNotIn("referral", json.dumps(entry))


class SchedulingTests(DeliveryBase):
    def test_an_explicit_offset_is_recorded_and_rendered_as_that_offset(self):
        _, payload = self.make_reminder(due_at="2026-11-01T05:30:00-05:00")
        self.assertEqual(payload["task"]["due_time_basis"], "explicit_offset")
        self.clock.advance(hours=2)
        event = self.inbox()[1]["events"][0]
        # Stored in UTC, rendered in the configured zone (UTC by default here).
        self.assertEqual(event["fire_at"], "2026-11-01T10:30:00Z")
        self.assertEqual(event["fire_at_local"]["iso"], "2026-11-01T10:30:00+00:00")

    def test_a_naive_timestamp_is_labelled_as_an_assumption(self):
        _, payload = self.make_reminder(due_at="2026-11-01T10:00:00")
        self.assertEqual(payload["task"]["due_time_basis"], "assumed_utc")
        self.assertEqual(payload["task"]["due_at"], "2026-11-01T10:00:00Z")

    def test_two_offsets_for_the_same_instant_agree(self):
        first = self.make_reminder(due_at="2026-11-07T12:00:00Z")[1]["task"]
        second = self.make_reminder(due_at="2026-11-07T07:00:00-05:00")[1]["task"]
        self.assertEqual(first["due_at"], second["due_at"])
        self.assertEqual(len(self.deliveries()), 2)
        self.clock.advance(days=7)
        events = self.inbox()[1]["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual({event["fire_at"] for event in events}, {"2026-11-07T12:00:00Z"})

    def test_a_fall_back_hour_delivers_both_instants_once_each(self):
        """01:30 occurs twice on 2026-11-01 in New York; they are two instants, not one."""
        self.restart_with_zone("America/New_York")
        early = self.make_reminder(due_at="2026-11-01T05:30:00Z")[1]["task"]
        late = self.make_reminder(due_at="2026-11-01T06:30:00Z")[1]["task"]
        self.assertNotEqual(early["due_at"], late["due_at"])
        self.clock.advance(hours=3)
        events = self.inbox()[1]["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(len({event["delivery_id"] for event in events}), 2)
        rendered = sorted(event["fire_at_local"]["iso"] for event in events)
        # Same wall clock, different offset: neither is collapsed into the other.
        self.assertEqual(rendered, ["2026-11-01T01:30:00-04:00", "2026-11-01T01:30:00-05:00"])
        self.assertEqual(sorted(event["fire_at"] for event in events),
                         ["2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"])
        # Re-reading produces no third event and no duplicate.
        self.assertEqual(len(self.inbox()[1]["events"]), 2)

    def test_a_spring_forward_gap_is_never_reported_as_a_local_time(self):
        """02:30 does not exist on 2027-03-14 in New York; the server never invents it."""
        self.restart_with_zone("America/New_York", device_ttl=365)
        self.make_reminder(due_at="2027-03-14T06:30:00Z")
        self.make_reminder(due_at="2027-03-14T07:30:00Z")
        self.clock.advance(days=133)
        events = sorted(self.inbox()[1]["events"], key=lambda item: item["fire_at"])
        self.assertEqual([event["fire_at_local"]["iso"] for event in events],
                         ["2027-03-14T01:30:00-05:00", "2027-03-14T03:30:00-04:00"])
        offsets = [event["fire_at_local"]["utc_offset_seconds"] for event in events]
        self.assertEqual(offsets, [-18000, -14400])
        for event in events:
            self.assertFalse(event["fire_at_local"]["iso"].startswith("2027-03-14T02"))

    def test_a_reminder_missed_while_offline_is_delivered_late_and_labelled(self):
        self.make_reminder(due_at="2026-11-01T10:00:00Z")
        # "Downtime": the clock moves past the due time with no client polling, then the process
        # restarts. The reminder is still pending, so it is served — with the lateness stated.
        self.clock.advance(hours=5)
        self.restart()
        event = self.inbox()[1]["events"][0]
        self.assertTrue(event["late"])
        self.assertEqual(event["late_by_seconds"], 4 * 3600)
        self.assertEqual(event["fire_at"], "2026-11-01T10:00:00Z")

    def test_a_reminder_older_than_the_window_is_reported_not_delivered(self):
        self.make_reminder(due_at="2026-11-01T10:00:00Z")
        self.clock.advance(days=3)  # window is 1440 minutes by default
        _, payload = self.inbox()
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["counts"]["held_back"], 1)
        held = payload["held_back"][0]
        self.assertEqual(held["state"], "expired")
        self.assertEqual(held["reason"], "delivery_window_elapsed")
        self.assertIn("window elapsed", held["not_delivered_because"])

    def test_attempts_stop_at_the_configured_limit(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        attempts = []
        for _ in range(3):
            payload = self.inbox()[1]
            self.assertEqual(payload["count"], 1)
            attempts.append(payload["events"][0]["attempt"])
            self.clock.advance(minutes=6)
        self.assertEqual(attempts, [1, 2, 3])
        exhausted = self.inbox()[1]
        self.assertEqual(exhausted["count"], 0)
        self.assertEqual(exhausted["held_back"][0]["state"], "failed")
        self.assertEqual(exhausted["held_back"][0]["reason"], "attempts_exhausted")

    def test_repeat_reads_inside_the_retry_interval_are_one_attempt(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        self.assertEqual(self.inbox()[1]["events"][0]["attempt"], 1)
        self.clock.advance(minutes=4)
        # Inside the interval measured from the last hand-over: still the same attempt.
        self.assertEqual(self.inbox()[1]["events"][0]["attempt"], 1)
        self.clock.advance(minutes=6)
        self.assertEqual(self.inbox()[1]["events"][0]["attempt"], 2)

    def test_an_invalid_display_time_zone_is_refused(self):
        with tempfile.TemporaryDirectory() as staging:
            with self.assertRaises(ConfigError) as caught:
                support.load(pathlib.Path(staging), reminders_tz="Mars/Olympus_Mons")
        self.assertIn("IANA time zone", str(caught.exception))

    # ------------------------------------------------------------------ helper
    def restart_with_zone(self, zone: str, *, device_ttl: int = 90):
        self.app, self.config = support.build_app(self.directory, clock=self.clock,
                                                  reminders_tz=zone, device_ttl=device_ttl)
        self.device, self.token = self.pair_device(label="zone-phone", platform="phone")
        self.client_id = self.device["id"]
        return self.app


class RevocationTests(DeliveryBase):
    def test_revoking_a_client_cancels_its_queued_events(self):
        self.make_reminder()
        response, payload = self.call("POST", f"/v1/clients/{self.client_id}/revoke",
                                      token=self.operator_token)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["reminder_deliveries_cancelled"], 1)
        row = self.deliveries()[0]
        self.assertEqual(row["state"], "cancelled")
        self.assertEqual(row["failure_reason"], "client_revoked")
        self.assertIsNotNone(row["closed_at"])
        actions = [entry.action for entry in self.app.audit.tail(50)]
        self.assertIn("reminder_delivery.cancelled", actions)

    def test_a_revoked_client_cannot_read_its_inbox_even_mid_delivery(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        self.inbox()  # delivered once, not yet acknowledged
        self.call("POST", f"/v1/clients/{self.client_id}/revoke", token=self.operator_token)
        response, payload = self.inbox()
        self.assertEqual(response.status, 401)
        self.assertEqual(payload["error"]["code"], "token_revoked")

    def test_revocation_leaves_other_clients_untouched(self):
        other, other_token = self.pair_device(label="tablet", platform="tablet")
        self.make_reminder(deliver_to=other["id"], token=other_token)
        self.make_reminder(deliver_to=self.client_id)
        self.call("POST", f"/v1/clients/{self.client_id}/revoke", token=self.operator_token)
        self.clock.advance(hours=2)
        response, payload = self.inbox(token=other_token)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["count"], 1)
        states = sorted(row["state"] for row in self.deliveries())
        self.assertEqual(states, ["cancelled", "delivered"])

    def test_a_cancelled_delivery_is_closed_but_kept_for_the_record(self):
        self.make_reminder()
        self.call("POST", f"/v1/clients/{self.client_id}/revoke", token=self.operator_token)
        # Closed, not deleted: the attempt and its reason remain inspectable in the local record.
        row = self.deliveries()[0]
        self.assertEqual(row["state"], "cancelled")
        self.assertEqual(row["attempts"], 0)
        self.assertEqual(row["failure_reason"], "client_revoked")
        # A different client's queue is unaffected and shows nothing of this reminder.
        fresh = self.pair_device(label="replacement", platform="phone")[1]
        _, payload = self.inbox(token=fresh)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["held_back"], [])
        self.assertNotIn(TITLE, json.dumps(payload))
        counts = self.app.deliveries.counts()
        self.assertEqual(counts["by_state"]["cancelled"], 1)


class AuditTests(DeliveryBase):
    def test_the_audit_records_the_lifecycle_without_reminder_contents(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        delivery_id = self.inbox()[1]["events"][0]["delivery_id"]
        self.ack(delivery_id)
        rendered = json.dumps(self.audit_entries(limit=200)).lower()
        for action in ("reminder_delivery.enqueued", "reminder_delivery.attempt",
                       "reminder_delivery.acknowledged"):
            self.assertIn(action, rendered)
        # Contents never reach the trail: not the title, not the note.
        self.assertNotIn(TITLE.lower(), rendered)
        self.assertNotIn("referral", rendered)
        self.assertNotIn("insurance", rendered)

    def test_acknowledgement_is_audited_against_the_delivery_id(self):
        self.make_reminder()
        self.clock.advance(hours=2)
        delivery_id = self.inbox()[1]["events"][0]["delivery_id"]
        self.ack(delivery_id)
        entry = next(item for item in self.audit_entries()
                     if item["action"] == "reminder_delivery.acknowledged")
        self.assertEqual(entry["target"], delivery_id)
        self.assertEqual(entry["outcome"], "allowed")
        detail = entry["detail"]
        self.assertEqual(detail["was_attempt"], 1)
        self.assertNotIn("title", detail)

    def test_expiry_and_failure_are_audited_with_counts_only(self):
        self.make_reminder(due_at="2026-11-01T10:00:00Z")
        self.clock.advance(days=3)
        self.inbox()
        entry = next(item for item in self.audit_entries()
                     if item["action"] == "reminder_delivery.expired")
        detail = entry["detail"]
        self.assertEqual(detail["count"], 1)
        self.assertEqual(detail["reason"], "delivery_window_elapsed")
        self.assertEqual(set(detail), {"count", "reason"})

    def test_the_audit_records_the_scoped_refusal(self):
        narrowed = self.pair_device(scopes=["reminders.read"], label="narrow",
                                    platform="tablet")[1]
        self.inbox(token=narrowed)
        entries = [item for item in self.audit_entries()
                   if item["action"] == "GET /v1/reminders/inbox"]
        self.assertTrue(entries)
        self.assertEqual(entries[0]["outcome"], "denied")

    def test_delivery_is_never_described_as_voice_or_push_anywhere(self):
        self.make_reminder()
        _, payload = self.call("GET", "/v1/status", token=self.operator_token)
        reminders = payload["storage"]["reminder_deliveries"]
        self.assertIn("does not push, speak, render or contact a device",
                      reminders["delivery_model"])
        inventory = {row["component"]: row for row in payload["infrastructure"]}
        self.assertIn("no push, no speech, no display", inventory["reminders"]["detail"])
        limitation = next(item for item in reporting.limitation_catalogue()
                          if item["code"] == "reminder_delivery_unavailable")
        self.assertIn("no push channel", limitation["detail"])
        manifest = manifests.get("reminders")
        self.assertIn("no push channel", manifest.notes)
        # The status block is a report, not a claim about voice.
        self.assertNotIn("spoken", json.dumps(reminders).lower())


class DeliveryEgressTests(DeliveryBase):
    def test_the_delivery_module_has_no_network_import(self):
        source = (pathlib.Path("app/jarvis/reminders.py").read_text(encoding="utf-8"))
        for banned in ("socket", "urllib", "http.client", "httpx", "requests", "ssl",
                       "smtplib", "ftplib", "websocket", "push"):
            self.assertIsNone(
                re.search(rf"^\s*(?:from|import)\s+{re.escape(banned)}\b", source, re.M),
                banned)
        # The only outbound-capable module in the package is the guard itself.
        self.assertIn("class EgressGuard", (pathlib.Path("app/jarvis/egress.py")
                                            .read_text(encoding="utf-8")))

    def test_no_push_subscribe_or_callback_route_exists(self):
        from app.jarvis.app import ROUTES

        patterns = " ".join(route.pattern.pattern for route in ROUTES).lower()
        for banned in ("push", "subscribe", "callback", "webhook", "notify", "dispatch"):
            self.assertNotIn(banned, patterns)
        reminder_routes = [route.name for route in ROUTES
                           if "reminder" in route.pattern.pattern]
        self.assertEqual(sorted(reminder_routes),
                         ["reminders.ack", "reminders.delivery.ack", "reminders.due",
                          "reminders.inbox"])

    def test_a_full_delivery_flow_makes_no_egress_attempt(self):
        GUARD.uninstall()
        self.addCleanup(GUARD.uninstall)
        self.assertTrue(GUARD.install())
        before = GUARD.policy()["counters"]
        self.make_reminder()
        self.clock.advance(hours=2)
        delivery_id = self.inbox()[1]["events"][0]["delivery_id"]
        self.ack(delivery_id)
        self.call("GET", "/v1/status", token=self.operator_token)
        after = GUARD.policy()["counters"]
        self.assertEqual(after["attempts"], before["attempts"])
        self.assertEqual(after["denials"], before["denials"])
        self.assertTrue(GUARD.policy()["guard_installed"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
