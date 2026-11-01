"""Poll-loop mechanics: cadence, dedup, acknowledgement policy, offline and resume behaviour."""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from . import support

from jarvis_companion.config import CompanionConfig
from jarvis_companion.errors import DisplayZoneMissing, ProtocolError, ServerUnreachable
from jarvis_companion.poller import MAX_BACKOFF_SECONDS
from jarvis_companion.state import Journal


class CadenceTests(unittest.TestCase):
    def test_the_loop_polls_every_configured_interval(self):
        companion, clock, _, _, _ = support.make_companion(
            config=CompanionConfig("http://127.0.0.1:7420", poll_seconds=10))
        report = companion.run(max_ticks=3)
        self.assertEqual(report.ticks, 3)
        self.assertEqual(clock.sleeps, [10.0, 10.0])

    def test_a_faster_cadence_is_used_as_configured(self):
        companion, clock, _, _, api = support.make_companion(
            config=CompanionConfig("http://127.0.0.1:7420", poll_seconds=4))
        companion.run(max_ticks=3)
        self.assertEqual(clock.sleeps, [4.0, 4.0])
        self.assertEqual(api.inbox_calls, 3)

    def test_a_gap_longer_than_the_cadence_is_treated_as_sleep_and_resume(self):
        companion, clock, _, _, _ = support.make_companion()
        ticks: list = []
        original = companion.tick

        def capture(**kwargs):
            tick = original(**kwargs)
            ticks.append(tick)
            if len(ticks) == 1:
                clock.advance(45)  # the machine slept for 45 seconds
            return tick

        companion.tick = capture  # type: ignore[assignment]
        companion.run(max_ticks=2)
        self.assertEqual(ticks[0].resumed, False)
        self.assertEqual(ticks[1].resumed, True)
        self.assertEqual(clock.sleeps, [])  # no sleep after a resume: poll immediately


class OfflineTests(unittest.TestCase):
    def test_an_unreachable_server_backs_off_and_recovers(self):
        api = support.FakeApi()
        api.error = ServerUnreachable("connection refused")
        companion, clock, _, _, _ = support.make_companion(api=api)
        report = companion.run(max_ticks=2)
        self.assertEqual([t.status for t in report.tick_reports], ["unreachable", "unreachable"])
        self.assertIn(20.0, clock.sleeps)  # interval * 2^1
        self.assertIn(40.0, clock.sleeps)  # interval * 2^2
        self.assertTrue(all(value <= MAX_BACKOFF_SECONDS for value in clock.sleeps))

    def test_recovery_resets_the_backoff(self):
        api = support.FakeApi()
        api.error = ServerUnreachable("down")
        companion, clock, _, _, _ = support.make_companion(api=api)
        states: list[str] = []
        original = companion.tick

        def scripted(**kwargs):
            if len(states) == 1:
                api.error = None  # the server comes back
            tick = original(**kwargs)
            states.append(tick.status)
            return tick

        companion.tick = scripted  # type: ignore[assignment]
        report = companion.run(max_ticks=3)
        self.assertEqual(states, ["unreachable", "ok", "ok"])
        self.assertEqual(report.ticks, 3)

    def test_a_tick_reports_the_failure_instead_of_raising(self):
        api = support.FakeApi()
        api.error = ServerUnreachable("nope")
        companion, _, _, _, _ = support.make_companion(api=api)
        tick = companion.tick()
        self.assertEqual(tick.status, "unreachable")
        self.assertEqual(tick.shown, 0)
        self.assertIn("nope", tick.message)


class DedupTests(unittest.TestCase):
    def test_a_duplicate_event_in_one_payload_is_shown_once(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"), support.event(delivery_id="dlv_1"))
        companion, _, notifier, _, _ = support.make_companion(api=api)
        tick = companion.tick()
        self.assertEqual(tick.shown, 1)
        self.assertEqual(tick.duplicates, 1)
        self.assertEqual(len(notifier.shown), 1)

    def test_the_same_delivery_is_never_shown_twice_across_ticks(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        companion, _, notifier, _, _ = support.make_companion(api=api)
        first = companion.tick()
        second = companion.tick()
        third = companion.tick()
        self.assertEqual((first.shown, second.shown, third.shown), (1, 0, 0))
        self.assertEqual((second.duplicates, third.duplicates), (1, 1))
        self.assertEqual(len(notifier.shown), 1)

    def test_a_new_companion_over_the_same_journal_does_not_repeat(self):
        directory = pathlib.Path(tempfile.mkdtemp(prefix="companion-dedup-"))
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        journal = Journal(directory / "journal.sqlite3")
        first, _, notifier, _, _ = support.make_companion(api=api, journal=journal)
        self.assertEqual(first.tick().shown, 1)
        journal.close()

        reopened = Journal(directory / "journal.sqlite3")
        self.addCleanup(reopened.close)
        second, _, notifier2, _, _ = support.make_companion(api=api, journal=reopened)
        tick = second.tick()
        self.assertEqual(tick.shown, 0)
        self.assertEqual(tick.duplicates, 1)
        self.assertEqual(notifier2.shown, [])


class NotificationVerdictTests(unittest.TestCase):
    def test_a_failed_show_is_retried_then_left_alone(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        notifier = support.RecordingNotifier("failed")
        companion, _, _, journal, _ = support.make_companion(api=api, notifier=notifier)
        verdicts = []
        for _ in range(4):
            verdicts.append(companion.tick().failed)
        self.assertEqual(verdicts, [1, 1, 1, 0])
        self.assertEqual(notifier.calls, 3)
        record = journal.get("dlv_1")
        self.assertEqual(record.verdict, "failed")
        self.assertEqual(record.show_attempts, 3)

    def test_a_failed_show_that_then_succeeds_is_shown_once_more_and_accepted(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        notifier = support.RecordingNotifier("failed")
        companion, _, _, journal, _ = support.make_companion(api=api, notifier=notifier)
        companion.tick()
        notifier.set_verdict("accepted")
        tick = companion.tick()
        self.assertEqual(tick.shown, 1)
        self.assertEqual(journal.get("dlv_1").verdict, "accepted")

    def test_a_denied_permission_is_recorded_and_never_called_shown(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        notifier = support.RecordingNotifier("accepted", availability=("denied", "switched off"))
        companion, _, _, journal, _ = support.make_companion(api=api, notifier=notifier)
        tick = companion.tick()
        self.assertEqual((tick.shown, tick.denied), (0, 1))
        self.assertEqual(notifier.calls, 0, "the OS was asked even though notifications are denied")
        self.assertEqual(journal.get("dlv_1").verdict, "denied")
        self.assertIn("switched off", journal.get("dlv_1").verdict_reason)

    def test_permission_returning_later_lets_the_reminder_through(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        notifier = support.RecordingNotifier("accepted", availability=("denied", "off"))
        companion, _, _, journal, _ = support.make_companion(api=api, notifier=notifier)
        companion.tick()
        notifier.set_verdict("accepted", availability=("ok", "enabled again"))
        tick = companion.tick()
        self.assertEqual(tick.shown, 1)
        self.assertEqual(journal.get("dlv_1").verdict, "accepted")

    def test_an_unsupported_platform_is_reported_as_unsupported(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        from jarvis_companion.notifier import NullNotifier

        companion, _, _, journal, _ = support.make_companion(api=api,
                                                            notifier=NullNotifier("no backend"))
        tick = companion.tick()
        self.assertEqual((tick.shown, tick.unsupported), (0, 1))
        self.assertEqual(journal.get("dlv_1").verdict, "unsupported")


class AcknowledgementTests(unittest.TestCase):
    def test_polling_never_acknowledges_anything(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        companion, _, _, journal, _ = support.make_companion(api=api)
        companion.tick()
        self.assertEqual(api.ack_calls, [])
        self.assertFalse(journal.get("dlv_1").acknowledged)

    def test_an_explicit_acknowledgement_confirms_with_the_server(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        companion, _, _, journal, _ = support.make_companion(api=api)
        companion.tick()
        result = companion.acknowledge("dlv_1")
        self.assertEqual(result["stage"], "acknowledged")
        self.assertTrue(result["server_confirmed"])
        self.assertEqual(api.ack_calls, ["dlv_1"])
        self.assertTrue(journal.get("dlv_1").acknowledged)

    def test_an_acknowledgement_made_offline_is_queued_and_sent_later(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        companion, _, _, journal, _ = support.make_companion(api=api)
        companion.tick()
        api.ack_error = ServerUnreachable("offline")
        result = companion.acknowledge("dlv_1")
        self.assertEqual(result["stage"], "queued_offline")
        self.assertFalse(result["server_confirmed"])
        self.assertEqual(journal.pending_acks(), ["dlv_1"])
        api.ack_error = None
        tick = companion.tick()
        self.assertEqual(tick.acks_confirmed, 1)
        self.assertEqual(journal.pending_acks(), [])
        self.assertTrue(journal.get("dlv_1").acknowledged)

    def test_an_acknowledgement_the_server_closed_is_dropped_with_a_reason(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        companion, _, _, journal, _ = support.make_companion(api=api)
        companion.tick()
        api.ack_closed.add("dlv_1")
        result = companion.acknowledge("dlv_1")
        self.assertEqual(result["stage"], "delivery_closed")
        self.assertEqual(journal.pending_acks(), [])
        self.assertFalse(journal.get("dlv_1").acknowledged)

    def test_repeating_an_acknowledgement_is_a_no_op(self):
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1"))
        companion, _, _, _, _ = support.make_companion(api=api)
        companion.tick()
        companion.acknowledge("dlv_1")
        again = companion.acknowledge("dlv_1")
        self.assertEqual(again["stage"], "already_acknowledged")
        self.assertEqual(api.ack_calls, ["dlv_1"])


class ContractRefusalTests(unittest.TestCase):
    def test_a_payload_without_a_display_zone_stops_the_client(self):
        api = support.FakeApi(support.inbox_payload([support.event()], zone=None))
        companion, _, notifier, _, _ = support.make_companion(api=api)
        with self.assertRaises(DisplayZoneMissing) as caught:
            companion.tick()
        self.assertIn("display_timezone", str(caught.exception))
        self.assertEqual(notifier.shown, [])

    def test_an_event_rendered_in_another_zone_is_refused(self):
        payload = support.inbox_payload(
            [support.event(zone="Europe/Berlin")], zone="Asia/Kolkata")
        api = support.FakeApi(payload)
        companion, _, notifier, _, _ = support.make_companion(api=api)
        with self.assertRaises(ProtocolError) as caught:
            companion.tick()
        self.assertIn("different zone", str(caught.exception))
        self.assertEqual(notifier.shown, [])

    def test_an_event_missing_fields_is_refused_rather_than_guessed(self):
        broken = support.event()
        del broken["fire_at_local"]["iso"]
        api = support.FakeApi(support.inbox_payload([broken]))
        companion, _, notifier, _, _ = support.make_companion(api=api)
        with self.assertRaises(ProtocolError):
            companion.tick()
        self.assertEqual(notifier.shown, [])

    def test_an_empty_inbox_is_not_an_error(self):
        companion, _, _, _, _ = support.make_companion()
        tick = companion.tick()
        self.assertEqual((tick.status, tick.events, tick.shown), ("ok", 0, 0))


class PrivacyTests(unittest.TestCase):
    def test_the_journal_file_never_contains_the_reminder_title(self):
        directory = pathlib.Path(tempfile.mkdtemp(prefix="companion-privacy-"))
        journal_path = directory / "journal.sqlite3"
        api = support.FakeApi()
        api.queue(support.event(delivery_id="dlv_1", title="call the plumber about the boiler"))
        journal = Journal(journal_path)
        self.addCleanup(journal.close)
        companion, _, _, _, _ = support.make_companion(api=api, journal=journal)
        companion.tick()
        journal.close()
        raw = journal_path.read_bytes()
        self.assertNotIn(b"plumber", raw)
        self.assertIn(b"dlv_1", raw)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
