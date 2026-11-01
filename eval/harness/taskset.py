"""Task-set loading and validation.

A task set is a directory with `tasks.json` (and, for B1, `corpus.json`). Files are
immutable once published: new items require a new version string.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import sha256_obj

REQUIRED_BLOCK_FIELDS = {"version", "block", "synthetic", "data_class", "tasks"}


class TaskSetError(ValueError):
    """Raised when a task set is malformed or violates the synthetic-data policy."""


@dataclass(frozen=True)
class TaskSet:
    block: str
    version: str
    path: Path
    tasks: tuple[dict[str, Any], ...]
    corpus: tuple[dict[str, Any], ...] = ()

    @property
    def sha(self) -> str:
        return sha256_obj({"block": self.block, "version": self.version,
                           "tasks": list(self.tasks), "corpus": list(self.corpus)})

    def __len__(self) -> int:
        return len(self.tasks)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TaskSetError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TaskSetError(f"invalid JSON in {path}: {exc}") from exc


def load_taskset(directory: Path | str) -> TaskSet:
    directory = Path(directory)
    payload = _read_json(directory / "tasks.json")
    if not isinstance(payload, dict):
        raise TaskSetError(f"{directory}/tasks.json must be an object")
    missing = REQUIRED_BLOCK_FIELDS - payload.keys()
    if missing:
        raise TaskSetError(f"{directory}/tasks.json missing fields: {sorted(missing)}")
    if payload["synthetic"] is not True or payload["data_class"] != "synthetic":
        raise TaskSetError(
            f"{directory}: only synthetic fixtures are permitted in Stage 0 "
            "(set synthetic=true and data_class='synthetic')"
        )
    block = str(payload["block"])
    tasks = payload["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise TaskSetError(f"{directory}: 'tasks' must be a non-empty list")
    corpus: list[dict[str, Any]] = []
    if block == "B1":
        corpus_payload = _read_json(directory / "corpus.json")
        corpus = corpus_payload.get("documents", [])
        if not corpus:
            raise TaskSetError(f"{directory}: B1 requires corpus.json with documents")
    _validate(block, tasks, corpus, directory)
    return TaskSet(block=block, version=str(payload["version"]), path=directory,
                   tasks=tuple(tasks), corpus=tuple(corpus))


def _validate(block: str, tasks: list[dict[str, Any]], corpus: list[dict[str, Any]],
              directory: Path) -> None:
    ids: set[str] = set()
    span_ids = {span["span_id"] for doc in corpus for span in doc.get("spans", [])}
    for task in tasks:
        tid = task.get("id")
        if not tid or not isinstance(tid, str):
            raise TaskSetError(f"{directory}: task without a string id")
        if tid in ids:
            raise TaskSetError(f"{directory}: duplicate task id {tid}")
        ids.add(tid)
        if block == "B1":
            for key in ("question", "answer_keywords", "evidence_span_ids"):
                if key not in task:
                    raise TaskSetError(f"{directory}: B1 task {tid} missing '{key}'")
            unknown = set(task["evidence_span_ids"]) - span_ids
            if unknown:
                raise TaskSetError(f"{directory}: task {tid} cites unknown spans {sorted(unknown)}")
        elif block == "B2":
            for key in ("instruction", "intent", "policy", "expected"):
                if key not in task:
                    raise TaskSetError(f"{directory}: B2 task {tid} missing '{key}'")
            if "tool" not in task["intent"]:
                raise TaskSetError(f"{directory}: B2 task {tid} intent missing 'tool'")
        elif block == "B3":
            for key in ("goal", "plan", "verify", "budget"):
                if key not in task:
                    raise TaskSetError(f"{directory}: B3 task {tid} missing '{key}'")
            # Every written file must be covered by an assertion, otherwise removing a
            # step would be undetectable by the verifier (see X-10 drop_last_step).
            verify = task["verify"]
            verified_paths = ({entry["path"] for entry in verify.get("files", [])}
                              | set(verify.get("absent", [])))
            written = {step.get("args", {}).get("path")
                       for step in task["plan"]
                       if step.get("tool") in ("files.write", "files.append")}
            written.discard(None)
            unverified = written - verified_paths
            if unverified:
                raise TaskSetError(
                    f"{directory}: B3 task {tid} writes unverified files {sorted(unverified)}"
                )
        elif block == "B8":
            _validate_b8(directory, task)
        elif block == "B9":
            _validate_b9(directory, task)
        elif block == "B10":
            _validate_b10(directory, task)
        else:
            raise TaskSetError(f"{directory}: unsupported block {block}")


B8_SUBFAMILIES = {"extraction", "synthesis", "planning"}
TRUST_LEVELS = {"verified", "reported", "untrusted"}


def _require(directory: Path, task: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key not in task:
            raise TaskSetError(
                f"{directory}: task {task.get('id')} missing '{key}'")


def _validate_b8(directory: Path, task: dict[str, Any]) -> None:
    _require(directory, task, ("id", "subfamily", "instruction", "expected"))
    sub = task["subfamily"]
    if sub not in B8_SUBFAMILIES:
        raise TaskSetError(f"{directory}: B8 task {task['id']} has unknown "
                           f"subfamily {sub!r}")
    expected = task["expected"]
    if sub == "extraction":
        _require(directory, task, ("documents",))
        span_ids = {span["span_id"] for doc in task["documents"]
                    for span in doc.get("spans", [])}
        if not span_ids:
            raise TaskSetError(f"{directory}: B8 task {task['id']} has no spans")
        if not expected.get("fields"):
            raise TaskSetError(f"{directory}: B8 task {task['id']} has no expected fields")
        unknown = set(expected.get("evidence_span_ids", [])) - span_ids
        if unknown:
            raise TaskSetError(f"{directory}: B8 task {task['id']} cites unknown "
                               f"spans {sorted(unknown)}")
        # An extracted answer with no citable span is exactly the failure B1/B8
        # exist to catch, so the fixture may not encode it.
        if not expected.get("evidence_span_ids"):
            raise TaskSetError(f"{directory}: B8 task {task['id']} must cite a span")
    elif sub == "synthesis":
        _require(directory, task, ("question", "pattern", "documents"))
        docs = task["documents"]
        if len(docs) < 2:
            raise TaskSetError(f"{directory}: B8 task {task['id']} needs >=2 sources")
        doc_ids = {doc["doc_id"] for doc in docs}
        try:
            re.compile(task["pattern"])
        except re.error as exc:
            raise TaskSetError(
                f"{directory}: B8 task {task['id']} has invalid pattern: {exc}") from exc
        if not expected.get("answer"):
            raise TaskSetError(f"{directory}: B8 task {task['id']} has no expected answer")
        unknown = set(expected.get("conflict_doc_ids", [])) - doc_ids
        if unknown:
            raise TaskSetError(f"{directory}: B8 task {task['id']} names unknown "
                               f"conflict sources {sorted(unknown)}")
    else:  # planning
        _require(directory, task, ("slots", "rooms", "meetings", "constraints"))
        room_ids = {room["room_id"] for room in task["rooms"]}
        meeting_ids = {m["meeting_id"] for m in task["meetings"]}
        if not room_ids or not meeting_ids or not task["slots"]:
            raise TaskSetError(f"{directory}: B8 task {task['id']} is not runnable")
        for constraint in task["constraints"]:
            named = [constraint.get("meeting")] if constraint.get("meeting") else []
            named += list(constraint.get("meetings", []))
            unknown = {m for m in named if m not in meeting_ids}
            if unknown:
                raise TaskSetError(f"{directory}: B8 task {task['id']} constraint names "
                                   f"unknown meetings {sorted(unknown)}")
            if constraint.get("kind") not in {"before", "after", "at", "different_rooms"}:
                raise TaskSetError(f"{directory}: B8 task {task['id']} has unknown "
                                   f"constraint {constraint.get('kind')!r}")


def _validate_b9(directory: Path, task: dict[str, Any]) -> None:
    _require(directory, task, ("id", "instruction", "skill", "verify"))
    skill = task["skill"]
    if not skill.get("name") or not skill.get("steps"):
        raise TaskSetError(f"{directory}: B9 task {task['id']} has no skill steps")
    step_ids: set[str] = set()
    for step in skill["steps"]:
        for key in ("id", "tool", "args"):
            if key not in step:
                raise TaskSetError(f"{directory}: B9 task {task['id']} skill step "
                                   f"missing '{key}'")
        if step["id"] in step_ids:
            raise TaskSetError(f"{directory}: B9 task {task['id']} duplicate skill step "
                               f"{step['id']!r}")
        step_ids.add(step["id"])
    if not task["verify"].get("files"):
        raise TaskSetError(
            f"{directory}: B9 task {task['id']} has no acceptance assertions; the family "
            "measures whether the stored skill is applied, so the checks are mandatory")


def _validate_b10(directory: Path, task: dict[str, Any]) -> None:
    _require(directory, task, ("id", "now", "question", "seed_memory", "expected"))
    facts = task["seed_memory"]
    if not facts:
        raise TaskSetError(f"{directory}: B10 task {task['id']} has no seeded facts")
    fact_ids = [fact["fact_id"] for fact in facts]
    if len(set(fact_ids)) != len(fact_ids):
        raise TaskSetError(f"{directory}: B10 task {task['id']} has duplicate fact ids")
    for fact in facts:
        if fact.get("trust") not in TRUST_LEVELS:
            raise TaskSetError(f"{directory}: B10 task {task['id']} fact "
                               f"{fact['fact_id']} has trust {fact.get('trust')!r}")
        if not fact.get("claim") or not fact.get("valid_from"):
            raise TaskSetError(f"{directory}: B10 task {task['id']} fact "
                               f"{fact['fact_id']} needs claim and valid_from")
    expected = task["expected"]
    for key in ("answer_keywords", "surviving", "superseded", "expired", "forbidden"):
        if key not in expected:
            raise TaskSetError(f"{directory}: B10 task {task['id']} expected missing {key!r}")
    known = set(fact_ids)
    for key in ("surviving", "superseded", "expired"):
        unknown = set(expected[key]) - known
        if unknown:
            raise TaskSetError(f"{directory}: B10 task {task['id']} expected {key} names "
                               f"unknown facts {sorted(unknown)}")
    sets = [set(expected["surviving"]), set(expected["superseded"]), set(expected["expired"])]
    if sum(len(s) for s in sets) != len(set().union(*sets)):
        raise TaskSetError(f"{directory}: B10 task {task['id']} reuses a fact across "
                           "surviving/superseded/expired")


def load_tasksets(root: Path | str = "eval/tasks") -> dict[str, TaskSet]:
    root = Path(root)
    sets: dict[str, TaskSet] = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        ts = load_taskset(directory)
        sets[ts.block] = ts
    if not sets:
        raise TaskSetError(f"no task sets found under {root}")
    return sets
