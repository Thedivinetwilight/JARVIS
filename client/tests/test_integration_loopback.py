"""End to end against the genuine server over a loopback socket.

These tests are the closest thing to the product this repository can run without Windows: real
pairing, real routing, real attempt accounting, real acknowledgement. Only the OS notification call
and the Credential Manager are doubled, and every test says so by using the recording notifier.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest
import unittest.mock

from . import support

from jarvis_companion.config import CompanionConfig
from jarvis_companion.errors import ServerUnreachable
from jarvis_companion.poller import Companion
from jarvis_companion.state import Journal


class IntegrationBase(unittest.TestCase):
    display_timezone = "UTC"

    def setUp(self) -> None:
        self.server = support.LiveServer(display_timezone=self.display_timezone).start()
        self.addCleanup(self.server.stop)
        self.paired = support.paired_client(self.server)
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix="companion-it-"))
        self.journal = Journal(self.directory / "journal.sqlite3")
        self.addCleanup(self.journal.close)
        self.notifier = support.RecordingNotifier()
        self.clock = support.FakeClock()

    def companion(self, *, notifier=None, journal=None, api=None) -> Companion:
        return Companion(
            config=self.paired["config"], api=api or self.paired["api"],
            token=self.paired["token"], journal=journal or self.journal,
            notifier=notifier or self.notifier, clock=self.clock, sleep=self.clock.sleep,
            now_utc=lambda: "2026-11-01T10:00:00Z", log=lambda event, payload: None,
        )

    def remind(self, *, title="buy milk", notes=None, due_at="2026-11-01T10:00:00Z"):
        payload = self.server.remind(client_id=self.paired["client"]["id"], title=title,
                                     notes=notes, due_at=due_at)
        return payload


class PipelineTests(IntegrationBase):
    def test_a_reminder_routed_to_this_client_becomes_one_notification(self):
        self.remind()
        self.server.advance(seconds=3600)
        tick = self.companion().tick()
        self.assertEqual((tick.status, tick.events, tick.shown), ("ok", 1, 1))
        shown = self.notifier.shown[0]
        self.assertEqual(shown.title, "buy milk")
        self.assertEqual(shown.display_timezone, "UTC")
        self.assertIn("2026-11-01T10:00:00+00:00", shown.body)

    def test_showing_a_reminder_does_not_acknowledge_it(self):
        self.remind()
        self.server.advance(seconds=3600)
        self.companion().tick()
        states = [row["state"] for row in self.server.deliveries()]
        self.assertEqual(states, ["delivered"])
        self.assertFalse(self.journal.get(self.server.deliveries()[0]["id"]).acknowledged)

    def test_acknowledging_from_the_client_closes_it_on_the_server(self):
        self.remind()
        self.server.advance(seconds=3600)
        companion = self.companion()
        companion.tick()
        delivery_id = self.server.deliveries()[0]["id"]
        result = companion.acknowledge(delivery_id)
        self.assertTrue(result["server_confirmed"])
        self.assertEqual([row["state"] for row in self.server.deliveries()], ["acknowledged"])

    def test_polling_repeatedly_does_not_inflate_the_attempt_count(self):
        self.remind()
        self.server.advance(seconds=3600)
        companion = self.companion()
        attempts = []
        for _ in range(6):
            companion.tick()
            attempts.append(self.server.deliveries()[0]["attempts"])
        self.assertEqual(attempts, [1, 1, 1, 1, 1, 1])
        self.assertEqual(len(self.notifier.shown), 1, "a second toast was raised for one reminder")

    def test_the_notes_never_reach_the_notification(self):
        self.remind(title="buy milk", notes="the code to the side gate is 4411")
        self.server.advance(seconds=3600)
        self.companion().tick()
        body = self.notifier.shown[0].body + self.notifier.shown[0].title
        self.assertNotIn("4411", body)

    def test_another_clients_reminder_is_never_shown_here(self):
        other = support.paired_client(self.server)
        self.server.remind(client_id=other["client"]["id"], title="not mine")
        self.server.advance(seconds=3600)
        tick = self.companion().tick()
        self.assertEqual((tick.events, tick.shown), (0, 0))
        self.assertEqual(self.notifier.shown, [])


class TimezoneTests(IntegrationBase):
    display_timezone = "Asia/Kolkata"

    def test_the_notification_uses_the_servers_zone_not_this_machines(self):
        self.remind(due_at="2026-11-01T10:00:00Z")
        self.server.advance(seconds=3600)
        self.companion().tick()
        shown = self.notifier.shown[0]
        self.assertEqual(shown.display_timezone, "Asia/Kolkata")
        self.assertIn("2026-11-01T15:30:00+05:30", shown.body)
        self.assertIn("Asia/Kolkata", shown.body)

    def test_a_late_hand_over_reports_the_exact_delay(self):
        self.remind(due_at="2026-11-01T10:00:00Z")
        self.server.advance(seconds=3610, microseconds=500_000)
        self.companion().tick()
        self.assertTrue(self.notifier.shown[0].late)
        self.assertIn("10.500 s after the scheduled time", self.notifier.shown[0].body)


class LifecycleTests(IntegrationBase):
    def test_a_revoked_client_stops_the_loop_with_the_servers_reason(self):
        self.paired["api"].revoke_self(self.paired["token"], self.paired["client"]["id"])
        report = self.companion().run(max_ticks=3)
        self.assertEqual(report.stopped_reason, "token_revoked")
        self.assertEqual(report.ticks, 0)

    def test_a_queued_acknowledgement_is_sent_after_a_restart(self):
        self.remind()
        self.server.advance(seconds=3600)
        companion = self.companion()
        companion.tick()
        delivery_id = self.server.deliveries()[0]["id"]
        with unittest.mock.patch.object(self.paired["api"], "ack",
                                        side_effect=ServerUnreachable("offline")):
            queued = companion.acknowledge(delivery_id)
        self.assertEqual(queued["stage"], "queued_offline")
        self.assertEqual(self.journal.pending_acks(), [delivery_id])

        self.journal.close()
        reopened = Journal(self.directory / "journal.sqlite3")
        self.addCleanup(reopened.close)
        restarted = self.companion(journal=reopened)
        tick = restarted.tick()
        self.assertEqual(tick.acks_confirmed, 1)
        self.assertEqual([row["state"] for row in self.server.deliveries()], ["acknowledged"])

    def test_the_server_closing_a_delivery_is_reported_not_hidden(self):
        self.remind()
        self.server.advance(seconds=3600)
        companion = self.companion()
        companion.tick()
        delivery_id = self.server.deliveries()[0]["id"]
        self.server.advance(hours=25)
        companion.tick()  # the sweep closes it as expired
        result = companion.acknowledge(delivery_id)
        self.assertEqual(result["stage"], "delivery_closed")
        self.assertFalse(result["server_confirmed"])

    def test_a_run_of_three_polls_reports_its_counts(self):
        self.remind()
        self.server.advance(seconds=3600)
        report = self.companion().run(max_ticks=3)
        self.assertEqual(report.ticks, 3)
        self.assertEqual(report.shown, 1)
        self.assertEqual([t.shown for t in report.tick_reports], [1, 0, 0])
        self.assertEqual([t.duplicates for t in report.tick_reports], [0, 1, 1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
