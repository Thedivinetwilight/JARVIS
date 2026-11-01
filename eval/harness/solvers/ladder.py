"""The model-free solver ladder (experiment X-13).

Four hand-written deterministic profiles, ordered by how much of the intended
capability they implement. The ladder answers one question only:

    does this suite separate weaker behaviour from stronger behaviour?

If two rungs score the same on every family, the suite cannot discriminate and
its numbers say nothing about anything else. That is the check Stage 1 exists to
perform before any model is chosen.

``oracle_ceiling`` is a **probe, not a system**: it reads the fixture's own gold
fields to produce the best possible score, which bounds what the suite can award.
It is never used for promotion gates and never reported as a capability result.
"""
from __future__ import annotations

from typing import Any

from ..planning import validate_assignment
from .base import SolveResult, ToolContext
from .stub import StubSolver

#: Rungs use the same names as `solvers.SOLVERS`, strongest to weakest, with the
#: oracle probe listed first because it bounds the suite rather than competing.
LADDER_ORDER = ("oracle_ceiling", "stub", "stub_naive", "stub_no_verify")
PROFILE_BY_RUNG = {"stub": "reference", "stub_naive": "naive",
                   "stub_no_verify": "no_verify"}


class OracleSolver(StubSolver):
    """Reads the fixture's gold fields. Exists to bound the suite, nothing else."""

    name = "oracle_ceiling"

    def __init__(self, mutations: tuple[str, ...] = ()) -> None:
        super().__init__(mutations=mutations, profile="reference")

    def solve(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        block = task.get("block")
        if block == "B2":
            return super().solve(task, ctx)          # policy behaviour is real logic
        if block == "B3" and task.get("expected_status") == "partial":
            return super().solve(task, ctx)          # budget honesty is real logic
        handler = {
            "B1": self._oracle_b1, "B3": self._oracle_write, "B8": self._oracle_b8,
            "B9": self._oracle_write, "B10": self._oracle_b10,
        }.get(block)
        if handler is None:
            return super().solve(task, ctx)
        return handler(task, ctx)

    def _oracle_b1(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        if task.get("abstain_expected"):
            return SolveResult(status="partial", message="No supporting evidence.", steps=1)
        gold = task.get("evidence_span_ids", [])
        spans = {span["span_id"]: (doc["doc_id"], span["text"])
                 for doc in task.get("_corpus", []) for span in doc.get("spans", [])}
        if not gold or gold[0] not in spans:
            return SolveResult(status="failed", message="fixture has no gold span")
        doc_id, text = spans[gold[0]]
        return SolveResult(status="done", message=text,
                           claims=[{"text": text, "doc_id": doc_id,
                                    "span_id": gold[0]}], steps=1)

    def _oracle_b8(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        expected = task["expected"]
        if task["subfamily"] == "extraction":
            cited = expected["evidence_span_ids"][0]
            fields = dict(expected["fields"])
            return SolveResult(status="done", message="extracted " + ", ".join(sorted(fields)),
                               claims=[{"text": fields.get("invoice_id", ""),
                                        "span_id": cited}],
                               notes={"fields": fields}, steps=1)
        if task["subfamily"] == "synthesis":
            return SolveResult(status="done", message=f"value is {expected['answer']}",
                               notes={"conflicts": list(expected["conflict_doc_ids"]),
                                      "value": expected["answer"]}, steps=1)
        assignment = self._search_assignment(task)
        if assignment is None:
            return SolveResult(status="failed", message="no assignment found")
        return SolveResult(status="done", message="assignment produced",
                           notes={"assignment": assignment}, steps=1)

    def _oracle_write(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        """Writes exactly what the acceptance checks demand."""
        verify = task.get("verify", {})
        for expectation in verify.get("files", []):
            content = expectation.get("equals")
            if content is None:
                content = "\n".join(expectation.get("contains", []))
            ctx.call("files.write", {"path": expectation["path"], "content": content},
                     idempotency_key=f"{task['id']}:oracle:{expectation['path']}")
        for path in verify.get("absent", []):
            ctx.call("files.delete", {"path": path},
                     idempotency_key=f"{task['id']}:oracle:del:{path}")
        return SolveResult(status="done", message="acceptance state written",
                           notes={"used_skill": True}, steps=ctx.tool_calls)

    def _oracle_b10(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        expected = task["expected"]
        facts = {fact["fact_id"]: fact for fact in task["seed_memory"]}
        surviving = expected["surviving"][0]
        return SolveResult(
            status="done", message=facts[surviving]["claim"],
            notes={"memory_state": {"surviving": list(expected["surviving"]),
                                    "superseded": list(expected["superseded"]),
                                    "expired": list(expected["expired"])},
                   "surviving_claim": facts[surviving]["claim"],
                   "naive_consolidation": False},
            steps=1)


def build_ladder_solver(name: str, mutations: tuple[str, ...] = ()) -> StubSolver:
    """Build one rung. `oracle_ceiling` is the probe; the rest are systems."""
    if name == "oracle_ceiling":
        return OracleSolver(mutations=mutations)
    if name not in PROFILE_BY_RUNG:
        raise KeyError(f"unknown ladder rung {name!r}; choose from {LADDER_ORDER}")
    return StubSolver(mutations=mutations, profile=PROFILE_BY_RUNG[name])
