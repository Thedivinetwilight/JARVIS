"""The one real, model-free everyday-assistance capability: local tasks and reminders."""

from __future__ import annotations

import unittest

from app.jarvis.tasks import MAX_NOTES, MAX_TITLE
from app.tests.support import ApiTestCase


class TaskTests(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.device, self.token = self.pair_device(label="kitchen tablet")

    def create(self, **payload):
        return self.call("POST", "/v1/tasks", token=self.token, body=payload)

    def test_a_task_can_be_created_read_back_and_completed(self):
        response, payload = self.create(title="  buy   milk ", notes="two litres")
        self.assertEqual(response.status, 201, payload)
        task = payload["task"]
        self.assertEqual(task["title"], "buy milk")  # whitespace normalised
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["source"], "structured")
        self.assertIsNone(task["completed_at"])
        self.assertFalse(task["voice_input"]["available"])

        listed, payload = self.call("GET", "/v1/tasks", token=self.token)
        self.assertEqual(listed.status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["tasks"][0]["id"], task["id"])
        self.assertEqual(payload["summary"]["count"], 1)

        done, payload = self.call("POST", f"/v1/tasks/{task['id']}/complete", token=self.token)
        self.assertEqual(done.status, 200)
        self.assertEqual(payload["task"]["status"], "completed")
        self.assertIsNotNone(payload["task"]["completed_at"])

        # Completing twice is idempotent rather than an error: the timestamp does not move.
        first_stamp = done_payload_completed_at = payload["task"]["completed_at"]
        again, second = self.call("POST", f"/v1/tasks/{task['id']}/complete", token=self.token)
        self.assertEqual(again.status, 200)
        self.assertEqual(second["task"]["completed_at"], first_stamp)

    def test_task_listing_filters_by_status(self):
        open_task = self.create(title="open one")[1]["task"]
        completed = self.create(title="done one")[1]["task"]
        self.call("POST", f"/v1/tasks/{completed['id']}/complete", token=self.token)

        _, payload = self.call("GET", "/v1/tasks", token=self.token)
        self.assertEqual(payload["filter"], "open")
        self.assertEqual([task["id"] for task in payload["tasks"]], [open_task["id"]])
        _, payload = self.call("GET", "/v1/tasks?status=completed", token=self.token)
        self.assertEqual([task["id"] for task in payload["tasks"]], [completed["id"]])
        _, payload = self.call("GET", "/v1/tasks?status=all", token=self.token)
        self.assertEqual(payload["count"], 2)
        bad, payload = self.call("GET", "/v1/tasks?status=archived", token=self.token)
        self.assertEqual(bad.status, 400)
        self.assertEqual(payload["error"]["code"], "status_invalid")

    def test_deleting_a_task_is_soft_and_removes_its_reminder(self):
        task = self.create(title="temporary", due_at="2026-11-05T08:00:00Z")[1]["task"]
        self.assertIn("reminder_id", task)
        response, payload = self.call("DELETE", f"/v1/tasks/{task['id']}", token=self.token)
        self.assertEqual(response.status, 200)
        self.assertTrue(payload["deleted"])
        gone, payload = self.call("GET", "/v1/tasks", token=self.token)
        self.assertEqual(payload["count"], 0)
        missing, payload = self.call("POST", f"/v1/tasks/{task['id']}/complete", token=self.token)
        self.assertEqual(missing.status, 404)
        self.assertEqual(payload["error"]["code"], "task_not_found")
        # Soft delete: the row is still there for the owner, but no longer served.
        with self.app.store.connection() as connection:
            row = connection.execute("SELECT deleted_at FROM tasks WHERE id = ?",
                                     (task["id"],)).fetchone()
            reminders = connection.execute("SELECT COUNT(*) AS n FROM reminders").fetchone()["n"]
        self.assertIsNotNone(row["deleted_at"])
        self.assertEqual(reminders, 0)

    def test_validation_refuses_empty_and_oversized_fields(self):
        for payload, code in (
            ({}, "title_required"),
            ({"title": "   "}, "title_required"),
            ({"title": "x" * (MAX_TITLE + 1)}, "title_too_long"),
            ({"title": "ok", "notes": "n" * (MAX_NOTES + 1)}, "notes_too_long"),
            ({"title": "ok", "due_at": "next tuesday"}, "due_at_invalid"),
            ({"title": "ok", "due_at": "2077-01-01T00:00:00Z"}, "due_at_invalid"),
            ({"title": "ok", "source": "telepathy"}, "source_invalid"),
            ({"title": 7}, "bad_request"),
        ):
            with self.subTest(payload=str(payload)[:40]):
                response, body = self.create(**payload)
                self.assertEqual(response.status, 400)
                self.assertEqual(body["error"]["code"], code)

    def test_a_maximum_length_title_is_accepted(self):
        response, payload = self.create(title="y" * MAX_TITLE)
        self.assertEqual(response.status, 201)
        self.assertEqual(len(payload["task"]["title"]), MAX_TITLE)

    def test_voice_sourced_tasks_are_recorded_without_claiming_understanding(self):
        response, payload = self.create(title="dictated item", source="voice")
        self.assertEqual(response.status, 201)
        note = payload["task"]["voice_input"]
        self.assertTrue(note["available"])
        self.assertIn("not transcribed", note["note"])

    def test_tasks_are_local_only_and_never_leave_the_data_directory(self):
        self.create(title="local only")
        with self.app.store.connection() as connection:
            rows = connection.execute("SELECT title FROM tasks").fetchall()
        self.assertEqual([row["title"] for row in rows], ["local only"])
        self.assertTrue(self.config.storage.database_path.is_file())
        self.assertIn(self.config.storage.data_dir, self.config.storage.database_path.parents)


class ReminderTests(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.device, self.token = self.pair_device()

    def test_a_due_task_produces_a_reminder_at_its_due_time(self):
        response, payload = self.call(
            "POST", "/v1/tasks", token=self.token,
            body={"title": "call the plumber", "due_at": "2026-11-01T10:00:00Z"},
        )
        self.assertEqual(response.status, 201)
        reminder_id = payload["task"]["reminder_id"]

        early, payload = self.call("GET", "/v1/reminders/due", token=self.token)
        self.assertEqual(early.status, 200)
        self.assertEqual(payload["count"], 0)

        self.clock.advance(hours=2)
        due, payload = self.call("GET", "/v1/reminders/due", token=self.token)
        self.assertEqual(due.status, 200)
        self.assertEqual(payload["count"], 1)
        reminder = payload["reminders"][0]
        self.assertEqual(reminder["id"], reminder_id)
        self.assertEqual(reminder["title"], "call the plumber")
        self.assertFalse(reminder["delivery"]["available"])
        self.assertIn("no delivery channel", reminder["delivery"]["note"])

    def test_acknowledging_a_reminder_removes_it_from_the_due_list(self):
        _, payload = self.call("POST", "/v1/tasks", token=self.token,
                               body={"title": "water the plants",
                                     "due_at": "2026-11-01T10:00:00Z"})
        reminder_id = payload["task"]["reminder_id"]
        self.clock.advance(hours=2)
        ack, payload = self.call("POST", f"/v1/reminders/{reminder_id}/ack", token=self.token)
        self.assertEqual(ack.status, 200)
        self.assertIsNotNone(payload["reminder"]["acknowledged_at"])
        _, payload = self.call("GET", "/v1/reminders/due", token=self.token)
        self.assertEqual(payload["count"], 0)
        # Acknowledging twice is idempotent.
        again, _ = self.call("POST", f"/v1/reminders/{reminder_id}/ack", token=self.token)
        self.assertEqual(again.status, 200)

    def test_acknowledging_an_unknown_reminder_is_a_404(self):
        response, payload = self.call("POST", "/v1/reminders/rmd_missing/ack", token=self.token)
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"]["code"], "reminder_not_found")

    def test_completed_tasks_still_produce_reminders_until_acknowledged(self):
        _, payload = self.call("POST", "/v1/tasks", token=self.token,
                               body={"title": "done but noisy",
                                     "due_at": "2026-11-01T10:00:00Z"})
        task = payload["task"]
        self.call("POST", f"/v1/tasks/{task['id']}/complete", token=self.token)
        self.clock.advance(hours=2)
        _, payload = self.call("GET", "/v1/reminders/due", token=self.token)
        self.assertEqual(payload["count"], 1)

    def test_deleting_the_task_removes_the_reminder_from_the_due_list(self):
        _, payload = self.call("POST", "/v1/tasks", token=self.token,
                               body={"title": "cancel me", "due_at": "2026-11-01T10:00:00Z"})
        task = payload["task"]
        self.clock.advance(hours=2)
        self.call("DELETE", f"/v1/tasks/{task['id']}", token=self.token)
        _, payload = self.call("GET", "/v1/reminders/due", token=self.token)
        self.assertEqual(payload["count"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
