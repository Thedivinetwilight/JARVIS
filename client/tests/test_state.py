"""The local journal: dedup, the four stages, and no reminder content on disk."""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile
import unittest

from . import support  # noqa: F401  (sets sys.path)

from jarvis_companion.state import Journal


class JournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix="companion-state-"))
        self.journal = Journal(self.directory / "journal.sqlite3")

    def tearDown(self) -> None:
        self.journal.close()

    def _receipt(self, delivery_id: str = "dlv_1", reminder_id: str = "rmd_1") -> bool:
        return self.journal.record_receipt(delivery_id=delivery_id, reminder_id=reminder_id,
                                           fire_at="2026-11-01T10:00:00Z")

    def test_one_delivery_is_recorded_once(self):
        self.assertTrue(self._receipt())
        self.assertFalse(self._receipt())
        self.assertEqual(self.journal.counts()["accepted"], 0)

    def test_an_accepted_notification_is_never_repeated(self):
        self._receipt()
        self.assertTrue(self.journal.should_show("dlv_1")[0])
        self.journal.note_verdict("dlv_1", "accepted", reason="os accepted")
        may_show, why = self.journal.should_show("dlv_1")
        self.assertFalse(may_show)
        self.assertEqual(why, "already_accepted")

    def test_a_failed_show_is_retried_but_not_for_ever(self):
        self._receipt()
        for attempt in (1, 2, 3):
            may_show, why = self.journal.should_show("dlv_1", max_attempts=3)
            self.assertTrue(may_show, why)
            self.journal.note_verdict("dlv_1", "failed", reason=f"attempt {attempt}")
        may_show, why = self.journal.should_show("dlv_1", max_attempts=3)
        self.assertFalse(may_show)
        self.assertEqual(why, "attempts_exhausted")
        self.assertEqual(self.journal.get("dlv_1").show_attempts, 3)

    def test_an_unknown_verdict_is_refused(self):
        self._receipt()
        with self.assertRaises(ValueError):
            self.journal.note_verdict("dlv_1", "shown")

    def test_pending_acknowledgements_are_queued_and_confirmed(self):
        self._receipt()
        self.journal.enqueue_ack("dlv_1", error="offline")
        self.assertEqual(self.journal.pending_acks(), ["dlv_1"])
        self.journal.note_ack_failure("dlv_1", "still offline")
        self.assertEqual(self.journal.pending_acks(), ["dlv_1"])
        self.journal.mark_acknowledged("dlv_1", at="2026-11-01T10:06:00Z")
        self.assertEqual(self.journal.pending_acks(), [])
        record = self.journal.get("dlv_1")
        self.assertTrue(record.acknowledged)
        self.assertEqual(record.ack_at, "2026-11-01T10:06:00Z")
        self.assertEqual(self.journal.counts()["acknowledged"], 1)

    def test_a_closed_delivery_is_dropped_from_the_queue(self):
        self.journal.enqueue_ack("dlv_gone")
        self.journal.drop_ack("dlv_gone")
        self.assertEqual(self.journal.pending_acks(), [])

    def test_last_unacknowledged_prefers_a_shown_delivery(self):
        self._receipt("dlv_1")
        self.assertIsNone(self.journal.last_unacknowledged())
        self.journal.note_verdict("dlv_1", "accepted")
        self.assertEqual(self.journal.last_unacknowledged().delivery_id, "dlv_1")
        self.journal.mark_acknowledged("dlv_1")
        self.assertIsNone(self.journal.last_unacknowledged())

    def test_the_journal_cannot_hold_a_reminder_title(self):
        """There is no column for it, so no code path can quietly start storing one."""
        connection = sqlite3.connect(str(self.directory / "journal.sqlite3"))
        try:
            columns: list[str] = []
            for table in ("deliveries", "pending_acks", "meta"):
                columns.extend(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
        finally:
            connection.close()
        for forbidden in ("title", "notes", "text", "body", "content"):
            self.assertNotIn(forbidden, columns)

    def test_meta_is_a_small_key_value_store(self):
        self.assertIsNone(self.journal.get_meta("last_poll_at"))
        self.journal.set_meta("last_poll_at", "2026-11-01T10:00:00Z")
        self.assertEqual(self.journal.get_meta("last_poll_at"), "2026-11-01T10:00:00Z")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
