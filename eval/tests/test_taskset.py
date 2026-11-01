import json
import tempfile
import unittest
from pathlib import Path

from eval.harness.taskset import TaskSetError, load_taskset, load_tasksets


def write(tmp: str, payload: dict) -> str:
    Path(tmp, "tasks.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp


BASE_B1 = {"version": "t", "block": "B1", "synthetic": True,
           "data_class": "synthetic", "tasks": []}


class TaskSetTests(unittest.TestCase):
    def test_published_task_sets_load(self):
        sets = load_tasksets(Path("eval/tasks"))
        self.assertEqual(set(sets), {"B1", "B2", "B3", "B8", "B9", "B10", "B4", "B16"})
        for ts in sets.values():
            self.assertGreater(len(ts), 0)

    def test_non_synthetic_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, BASE_B1 | {"synthetic": False, "data_class": "real"})
            with self.assertRaises(TaskSetError):
                load_taskset(tmp)

    def test_duplicate_ids_are_refused(self):
        task = {"id": "t1", "question": "q", "answer_keywords": ["a"],
                "evidence_span_ids": []}
        payload = BASE_B1 | {"tasks": [task, task]}
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, payload)
            Path(tmp, "corpus.json").write_text(
                json.dumps({"documents": [{"doc_id": "d", "spans": []}]}),
                encoding="utf-8")
            with self.assertRaises(TaskSetError):
                load_taskset(tmp)

    def test_unknown_span_is_refused(self):
        payload = BASE_B1 | {"tasks": [{"id": "t1", "question": "q",
                                        "answer_keywords": ["a"],
                                        "evidence_span_ids": ["missing"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, payload)
            Path(tmp, "corpus.json").write_text(
                json.dumps({"documents": [{"doc_id": "d", "spans": [
                    {"span_id": "s1", "text": "hello"}]}]}), encoding="utf-8")
            with self.assertRaises(TaskSetError):
                load_taskset(tmp)

    def test_b3_requires_a_verifier_for_every_write(self):
        payload = {"version": "t", "block": "B3", "synthetic": True,
                   "data_class": "synthetic", "tasks": [{
                       "id": "t1", "goal": "g", "budget": 3,
                       "plan": [{"id": "s1", "tool": "files.write",
                                 "args": {"path": "a.txt", "content": "x"}}],
                       "verify": {"files": []}}]}
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, payload)
            with self.assertRaises(TaskSetError):
                load_taskset(tmp)

    def test_task_set_sha_is_stable(self):
        sets = load_tasksets(Path("eval/tasks"))
        again = load_tasksets(Path("eval/tasks"))
        self.assertEqual(sets["B3"].sha, again["B3"].sha)


if __name__ == "__main__":
    unittest.main()
