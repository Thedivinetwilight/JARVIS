import unittest

from eval.harness.runner import pass_hat_k, summarize
from eval.harness.scorers import compare_state, score_b1, score_b2, score_b3
from eval.harness.solvers.base import SolveResult
from eval.harness.tools import Executor, World


class FakeExecutor:
    SIDE_EFFECT_TOOLS = {"mail.send"}

    def __init__(self, audit=()):
        self.audit = list(audit)
        self.approval_violations = 0
        self.duplicate_writes = 0
        self.unauthorized_side_effects = 0
        self.validation_errors = 0


class CompareStateTests(unittest.TestCase):
    def test_scalar_and_nested_comparison(self):
        expected = {"calendar": [{"title": "a", "start": "10:00"}], "files": {}}
        actual = {"calendar": [{"title": "a", "start": "11:00"}], "files": {}}
        matched, total = compare_state(expected, actual)
        self.assertEqual((matched, total), (1, 2))

    def test_missing_list_items_count_as_mismatch(self):
        self.assertEqual(compare_state([1, 2], [1]), (1, 2))


class B1Tests(unittest.TestCase):
    task = {"answer_keywords": ["thirty minutes"], "evidence_span_ids": ["s2"]}

    def test_correct_answer_with_supported_citation(self):
        result = SolveResult(status="done", message="thirty minutes",
                             claims=[{"span_id": "s2"}])
        score = score_b1(self.task, result)
        self.assertTrue(score["success"])
        self.assertEqual(score["metrics"]["citation_precision"], 1.0)

    def test_wrong_citation_is_detected(self):
        result = SolveResult(status="done", message="thirty minutes",
                             claims=[{"span_id": "s9"}])
        score = score_b1(self.task, result)
        self.assertFalse(score["success"])
        self.assertTrue(score["false_success"])
        self.assertEqual(score["metrics"]["citation_precision"], 0.0)

    def test_abstention_on_unanswerable_question_is_success(self):
        task = {"answer_keywords": [], "evidence_span_ids": [],
                "abstain_expected": True}
        result = SolveResult(status="partial", message="No supporting evidence.")
        self.assertTrue(score_b1(task, result)["success"])

    def test_citing_on_unanswerable_question_is_a_false_success(self):
        task = {"answer_keywords": [], "evidence_span_ids": [],
                "abstain_expected": True}
        result = SolveResult(status="done", message="x", claims=[{"span_id": "s1"}])
        score = score_b1(task, result)
        self.assertFalse(score["success"])
        self.assertTrue(score["false_success"])


class B2Tests(unittest.TestCase):
    task = {"intent": {"tool": "mail.send",
                       "args": {"to": "a@example.test", "subject": "s",
                                "body": "b"}},
            "expected": {"world": {"calendar": [], "files": {},
                                   "mail": [{"to": "a@example.test",
                                             "subject": "s", "body": "b"}]}}}

    def audit(self, args):
        return [{"tool": "mail.send", "args": args, "ok": True, "replayed": False}]

    def test_matching_state_succeeds(self):
        world = {"calendar": [], "mail": [{"to": "a@example.test", "subject": "s",
                                           "body": "b"}], "files": {}}
        score = score_b2(self.task, SolveResult(status="done"),
                         world, FakeExecutor(self.audit(self.task["intent"]["args"])))
        self.assertTrue(score["success"])
        self.assertEqual(score["metrics"]["argument_exactness"], 1.0)

    def test_dropped_argument_is_caught_by_argument_exactness(self):
        # The world can look correct while the executed call was wrong: the
        # scorer must still fail the attempt, and 'done' makes it a false success.
        args = {"to": "a@example.test", "subject": "s"}
        world = {"calendar": [], "mail": [{"to": "a@example.test", "subject": "s",
                                           "body": "b"}], "files": {}}
        score = score_b2(self.task, SolveResult(status="done"),
                         world, FakeExecutor(self.audit(args)))
        self.assertFalse(score["success"])
        self.assertTrue(score["false_success"])
        self.assertLess(score["metrics"]["argument_exactness"], 1.0)

    def test_duplicate_writes_block_success(self):
        executor = FakeExecutor(self.audit(self.task["intent"]["args"]))
        executor.duplicate_writes = 1
        world = {"calendar": [], "mail": [{"to": "a@example.test", "subject": "s",
                                           "body": "b"}], "files": {}}
        score = score_b2(self.task, SolveResult(status="done"), world, executor)
        self.assertFalse(score["success"])

    def test_clarification_requires_no_calls(self):
        task = {"intent": {"tool": "mail.send", "args": {}},
                "expected": {"clarification_expected": True,
                             "world": {"calendar": [], "mail": [], "files": {}}}}
        world = {"calendar": [], "mail": [], "files": {}}
        score = score_b2(task, SolveResult(status="clarify"), world, FakeExecutor())
        self.assertTrue(score["success"])


class B3Tests(unittest.TestCase):
    task = {"verify": {"files": [{"path": "a.txt", "equals": "x"}],
                       "absent": ["b.txt"]}}

    def test_verified_state_succeeds(self):
        world = {"files": {"a.txt": "x"}}
        self.assertTrue(score_b3(self.task, SolveResult(status="done"),
                                 world)["success"])

    def test_unverified_done_is_a_false_success(self):
        world = {"files": {}}
        score = score_b3(self.task, SolveResult(status="done"), world)
        self.assertFalse(score["success"])
        self.assertTrue(score["false_success"])

    def test_expected_partial_status_is_honoured(self):
        task = dict(self.task, expected_status="partial")
        world = {"files": {"a.txt": "x"}}
        self.assertTrue(score_b3(task, SolveResult(status="partial"),
                                 world)["success"])
        self.assertFalse(score_b3(task, SolveResult(status="done"),
                                  world)["success"])


class HelperTests(unittest.TestCase):
    def test_pass_hat_k(self):
        class R:
            def __init__(self, task_id, repeat, success):
                self.task_id, self.repeat, self.success = task_id, repeat, success

        records = [R("a", 1, True), R("a", 2, True), R("b", 1, True),
                   R("b", 2, False)]
        self.assertEqual(pass_hat_k(records, 2), 0.5)

    def test_summarize_ignores_missing_precision(self):
        class R:
            task_id, repeat, success, false_success = "a", 1, True, False
            metrics = {"citations": 0, "supported_citations": 0}
            latency_ms, cost_usd, cost_is_synthetic = 1.0, 0.0, True

        summary = summarize([R()])
        self.assertIsNone(summary["mean_citation_precision"])
        self.assertEqual(summary["success_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
