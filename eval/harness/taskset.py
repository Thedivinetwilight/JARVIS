"""Task-set loading and validation.

A task set is a directory with `tasks.json` (and, for B1, `corpus.json`). Files are
immutable once published: new items require a new version string.
"""
from __future__ import annotations

import json
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
        else:
            raise TaskSetError(f"{directory}: unsupported block {block}")


def load_tasksets(root: Path | str = "eval/tasks") -> dict[str, TaskSet]:
    root = Path(root)
    sets: dict[str, TaskSet] = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        ts = load_taskset(directory)
        sets[ts.block] = ts
    if not sets:
        raise TaskSetError(f"no task sets found under {root}")
    return sets
