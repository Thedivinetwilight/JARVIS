import unittest

from eval.harness.tools import (BudgetExceeded, Executor, PolicyError, ToolTimeout,
                                ValidationError, World)

EVENT = {"title": "Sync", "date": "2026-11-05", "start": "10:00",
         "duration_min": 30, "tz": "Asia/Kolkata"}


class ExecutorTests(unittest.TestCase):
    def test_valid_call_applies_state(self):
        world = World()
        executor = Executor(world)
        executor.call("calendar.create_event", EVENT, idempotency_key="k1")
        self.assertEqual(len(world.calendar), 1)
        self.assertEqual(executor.validation_errors, 0)

    def test_missing_timezone_is_a_validation_error(self):
        executor = Executor(World())
        args = {k: v for k, v in EVENT.items() if k != "tz"}
        with self.assertRaises(ValidationError):
            executor.call("calendar.create_event", args)
        self.assertEqual(executor.validation_errors, 1)
        self.assertEqual(len(executor.world.calendar), 0)

    def test_bad_date_and_duration_rejected(self):
        executor = Executor(World())
        with self.assertRaises(ValidationError):
            executor.call("calendar.create_event", EVENT | {"date": "05-11-2026"})
        with self.assertRaises(ValidationError):
            executor.call("calendar.create_event", EVENT | {"duration_min": 0})

    def test_path_traversal_rejected(self):
        executor = Executor(World())
        with self.assertRaises(ValidationError):
            executor.call("files.write", {"path": "../escape.txt", "content": "x"})

    def test_approval_is_required_when_policy_demands_it(self):
        executor = Executor(World(), policy={"requires_approval": True})
        with self.assertRaises(PolicyError):
            executor.call("mail.send", {"to": "a@example.test", "subject": "s",
                                        "body": "b"})
        self.assertEqual(executor.approval_violations, 1)
        self.assertEqual(executor.world.mail, [])

    def test_approved_call_succeeds(self):
        executor = Executor(World(), policy={"requires_approval": True})
        executor.call("mail.send", {"to": "a@example.test", "subject": "s",
                                    "body": "b"}, approval_granted=True)
        self.assertEqual(len(executor.world.mail), 1)
        self.assertEqual(executor.approval_violations, 0)

    def test_irreversible_block_policy_denies_the_call(self):
        executor = Executor(World(), policy={"irreversible": True,
                                             "on_irreversible": "block"})
        with self.assertRaises(PolicyError):
            executor.call("mail.send", {"to": "a@example.test", "subject": "s",
                                        "body": "b"})
        self.assertEqual(executor.unauthorized_side_effects, 1)
        self.assertEqual(executor.world.mail, [])

    def test_idempotent_replay_does_not_duplicate(self):
        world = World()
        executor = Executor(world, faults=[{"tool": "mail.send",
                                            "kind": "timeout_once"}])
        args = {"to": "a@example.test", "subject": "s", "body": "b"}
        with self.assertRaises(ToolTimeout):
            executor.call("mail.send", args, idempotency_key="k")
        executor.call("mail.send", args, idempotency_key="k")
        self.assertEqual(len(world.mail), 1)
        self.assertEqual(executor.duplicate_writes, 0)
        self.assertTrue(executor.audit[-1]["replayed"])

    def test_fresh_key_after_timeout_is_a_duplicate(self):
        world = World()
        executor = Executor(world, faults=[{"tool": "mail.send",
                                            "kind": "timeout_once"}])
        args = {"to": "a@example.test", "subject": "s", "body": "b"}
        with self.assertRaises(ToolTimeout):
            executor.call("mail.send", args, idempotency_key="k1")
        executor.call("mail.send", args, idempotency_key="k2")
        self.assertEqual(len(world.mail), 2)
        self.assertEqual(executor.duplicate_writes, 1)

    def test_budget_is_enforced(self):
        executor = Executor(World(), call_budget=1)
        executor.call("files.write", {"path": "a.txt", "content": "x"})
        with self.assertRaises(BudgetExceeded):
            executor.call("files.write", {"path": "b.txt", "content": "x"})
        self.assertNotIn("b.txt", executor.world.files)

    def test_unknown_tool_is_rejected(self):
        executor = Executor(World())
        with self.assertRaises(ValidationError):
            executor.call("shell.exec", {"cmd": "rm -rf /"})

    def test_audit_redacts_secrets(self):
        executor = Executor(World())
        executor.call("files.write", {"path": "n.txt",
                                      "content": "token=supersecretvalue123"})
        self.assertNotIn("supersecretvalue123", str(executor.audit))
        self.assertIn("[REDACTED]", str(executor.audit))


if __name__ == "__main__":
    unittest.main()
