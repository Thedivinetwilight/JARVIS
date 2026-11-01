"""Deterministic non-model solver used while no model is selected.

Stage 0 must not call a model (no credentials, and model choice is an open
question). This solver is therefore deliberately simple and deliberately
imperfect: keyword retrieval with IDF weighting and templated execution. It is
good enough to exercise the harness end to end, and weak enough that its scores
must never be read as a model capability result.
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..tools import (BudgetExceeded, PolicyError, ToolError, ToolTimeout,
                     ValidationError)
from .base import SolveResult, ToolContext, call_with_retry

TOKEN = re.compile(r"[a-z0-9]+")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for",
    "from", "how", "in", "is", "it", "of", "on", "or", "that", "the", "to",
    "was", "were", "what", "when", "which", "who", "why", "with",
}

CITATION_THRESHOLD = 1.0


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


class StubSolver:
    name = "stub"

    def __init__(self, mutations: tuple[str, ...] = ()) -> None:
        self.mutations = tuple(mutations)

    def solve(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        block = task.get("block")
        if block == "B1":
            return self._solve_b1(task, ctx)
        if block == "B2":
            return self._solve_b2(task, ctx)
        if block == "B3":
            return self._solve_b3(task, ctx)
        return SolveResult(status="failed", message=f"unsupported block {block!r}")

    # -- B1: retrieval with citations ------------------------------------
    def _solve_b1(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        spans = [(doc, span) for doc in task.get("_corpus", [])
                 for span in doc.get("spans", [])]
        if not spans:
            return SolveResult(status="failed", message="empty corpus")

        tokenized = {span["span_id"]: tokenize(span["text"]) for _, span in spans}
        n_docs = len(spans)
        df: dict[str, int] = {}
        for tokens in tokenized.values():
            for token in set(tokens):
                df[token] = df.get(token, 0) + 1
        idf = {t: math.log((n_docs + 1) / (df[t] + 0.5)) for t in df}

        query = tokenize(task["question"])
        scored = []
        for doc, span in spans:
            tokens = tokenized[span["span_id"]]
            matched = sum(idf.get(t, 0.0) for t in set(query) & set(tokens))
            norm = matched / math.sqrt(max(len(tokens), 1))
            scored.append((norm, doc["doc_id"], span))
        scored.sort(key=lambda item: (-item[0], item[2]["span_id"]))
        best_score, best_doc, best_span = scored[0]

        claims = []
        if ctx.mutated("cite_top1_only") or best_score >= CITATION_THRESHOLD:
            claims.append({"text": best_span["text"], "doc_id": best_doc,
                           "span_id": best_span["span_id"]})

        if not claims:
            return SolveResult(
                status="partial",
                message="No supporting evidence found in the corpus.",
                claims=[], steps=1,
            )
        return SolveResult(status="done", message=best_span["text"], claims=claims,
                           steps=1)

    # -- B2: single-tool execution under policy --------------------------
    def _solve_b2(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        policy = task.get("policy", {})
        if policy.get("requires_clarification"):
            return SolveResult(
                status="clarify",
                message="Which event should I move, and what target time do you want?",
            )

        if policy.get("on_irreversible") == "block" and not ctx.mutated(
                "attempt_blocked_action"):
            return SolveResult(
                status="blocked",
                message="Policy forbids irreversible actions; nothing was attempted.",
            )

        intent = task["intent"]
        tool = intent["tool"]
        args = dict(intent["args"])
        if ctx.mutated("drop_timezone"):
            args.pop("tz", None)

        granted = False
        if policy.get("requires_approval") or policy.get("on_irreversible") == "ask":
            granted = ctx.request_approval(f"{tool} would change real state")
            if not granted:
                return SolveResult(status="blocked",
                                   message="Approval was requested and refused.")

        key = f"{task['id']}:v1"

        try:
            call_with_retry(ctx, tool, args, idempotency_key=key,
                            approval_granted=granted)
        except BudgetExceeded as exc:
            return SolveResult(status="partial", message=str(exc))
        except ToolTimeout as exc:  # pragma: no cover - defensive
            return SolveResult(status="failed", message=str(exc))
        except (ValidationError, PolicyError) as exc:
            return SolveResult(status="failed", message=str(exc))
        except ToolError as exc:
            return SolveResult(status="failed", message=str(exc))

        return SolveResult(status="done", message=f"{tool} executed",
                           steps=ctx.tool_calls)

    # -- B3: multi-step state mutation with read-back verification -------
    def _solve_b3(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        plan = list(task.get("plan", []))
        if ctx.mutated("drop_last_step") and len(plan) > 1:
            plan = plan[:-1]

        for step in plan:
            tool = step["tool"]
            args = dict(step["args"])
            try:
                ctx.call(tool, args, idempotency_key=f"{task['id']}:{step['id']}")
            except BudgetExceeded as exc:
                return SolveResult(status="partial", message=str(exc),
                                   steps=ctx.tool_calls)
            except ToolError as exc:
                return SolveResult(status="failed", message=str(exc),
                                   steps=ctx.tool_calls)

        if ctx.mutated("skip_verification"):
            return SolveResult(status="done",
                               message="Steps applied; verification skipped.",
                               steps=ctx.tool_calls)

        world = ctx.world_snapshot()
        problems = verify_world(task.get("verify", {}), world)
        if problems and task.get("repair"):
            for step in task["repair"]:
                try:
                    ctx.call(step["tool"], dict(step["args"]),
                             idempotency_key=f"{task['id']}:{step['id']}")
                except BudgetExceeded as exc:
                    return SolveResult(status="partial", message=str(exc),
                                       steps=ctx.tool_calls)
                except ToolError as exc:
                    return SolveResult(status="failed", message=str(exc),
                                       steps=ctx.tool_calls)
            world = ctx.world_snapshot()
            problems = verify_world(task.get("verify", {}), world)
        if problems:
            return SolveResult(status="failed",
                               message="Read-back verification failed: "
                                       + "; ".join(problems),
                               steps=ctx.tool_calls)
        return SolveResult(status="done", message="All steps applied and verified.",
                           steps=ctx.tool_calls)


def verify_world(verify: dict[str, Any], world: dict[str, Any]) -> list[str]:
    """Independent read-back check shared by the solver and the scorer."""
    problems: list[str] = []
    files = world.get("files", {})
    for expectation in verify.get("files", []):
        path = expectation["path"]
        if path not in files:
            problems.append(f"missing file {path}")
            continue
        content = files[path]
        if "equals" in expectation and content != expectation["equals"]:
            problems.append(f"{path} content mismatch")
        for needle in expectation.get("contains", []):
            if needle not in content:
                problems.append(f"{path} missing {needle!r}")
    for path in verify.get("absent", []):
        if path in files:
            problems.append(f"{path} should not exist")
    return problems
