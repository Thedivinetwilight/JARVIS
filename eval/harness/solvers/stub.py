"""Deterministic non-model solver, with an explicit capability *profile*.

Stage 0 introduced this solver as the reference system under test. Stage 1 keeps
that role (profile ``reference``) and adds two degenerate profiles used by the
solver ladder to prove the suite can tell weak behaviour from strong:

``reference``  full logic: IDF retrieval with citations, policy-aware execution,
               read-back verification and repair, schema extraction, majority
               reconciliation, backtracking search, trust-ordered consolidation.
``naive``      a plausible but shallow implementation: last-span retrieval,
               no conflict detection, greedy first-fit planning, memory ordered
               only by recency, and stored skills ignored in favour of a guess.
``no_verify``  the reference logic without read-back verification or citations:
               it produces work and declares success without checking it.

None of these is a model. They exist to exercise the harness; a profile's score is
a property of these hand-written programs, never a capability claim.
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..planning import validate_assignment
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

PROFILES = ("reference", "naive", "no_verify")

INVOICE_PATTERNS = {
    "invoice_id": re.compile(r"INV-\d{4}"),
    "date": re.compile(r"\d{4}-\d{2}-\d{2}"),
    "total": re.compile(r"[\d,]+\.\d{2}"),
    "currency": re.compile(r"\b(?:USD|EUR|INR|GBP|SGD|AUD)\b"),
    "po": re.compile(r"PO-\d{4}"),
    "vendor": re.compile(r"issued by (.+?) on \d{4}-\d{2}-\d{2}"),
}

TRUST_RANK = {"verified": 2, "reported": 1, "untrusted": 0}


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


class StubSolver:
    name = "stub"

    def __init__(self, mutations: tuple[str, ...] = (),
                 profile: str = "reference") -> None:
        if profile not in PROFILES:
            raise ValueError(f"unknown profile {profile!r}; choose from {PROFILES}")
        self.mutations = tuple(mutations)
        self.profile = profile

    # -- dispatch --------------------------------------------------------
    def solve(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        block = task.get("block")
        handler = {
            "B1": self._solve_b1, "B2": self._solve_b2, "B3": self._solve_b3,
            "B8": self._solve_b8, "B9": self._solve_b9, "B10": self._solve_b10,
        }.get(block)
        if handler is None:
            return SolveResult(status="failed", message=f"unsupported block {block!r}")
        return handler(task, ctx)

    @property
    def verifies(self) -> bool:
        return self.profile == "reference"

    # -- B1: retrieval with citations ------------------------------------
    def _solve_b1(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        spans = [(doc, span) for doc in task.get("_corpus", [])
                 for span in doc.get("spans", [])]
        if not spans:
            return SolveResult(status="failed", message="empty corpus")

        if self.profile == "naive":
            doc, span = spans[-1]
            return SolveResult(status="done", message=span["text"],
                               claims=[{"text": span["text"], "doc_id": doc["doc_id"],
                                        "span_id": span["span_id"]}], steps=1)

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
            scored.append((matched / math.sqrt(max(len(tokens), 1)), doc, span))
        scored.sort(key=lambda item: (-item[0], item[2]["span_id"]))
        best_score, best_doc, best_span = scored[0]

        claims = []
        if not self.verifies:
            # No citation trail: the answer may even be right, but it cannot be checked.
            return SolveResult(status="done", message=best_span["text"], claims=[], steps=1)
        if ctx.mutated("cite_top1_only") or best_score >= CITATION_THRESHOLD:
            claims.append({"text": best_span["text"], "doc_id": best_doc["doc_id"],
                           "span_id": best_span["span_id"]})
        if not claims:
            return SolveResult(status="partial",
                               message="No supporting evidence found in the corpus.",
                               steps=1)
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
            try:
                ctx.call(step["tool"], dict(step["args"]),
                         idempotency_key=f"{task['id']}:{step['id']}")
            except BudgetExceeded as exc:
                return SolveResult(status="partial", message=str(exc),
                                   steps=ctx.tool_calls)
            except ToolError as exc:
                return SolveResult(status="failed", message=str(exc),
                                   steps=ctx.tool_calls)

        if not self.verifies or ctx.mutated("skip_verification"):
            return SolveResult(status="done", message="Steps applied.",
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
                                       + "; ".join(problems), steps=ctx.tool_calls)
        return SolveResult(status="done", message="All steps applied and verified.",
                           steps=ctx.tool_calls)

    # -- B8: breadth ------------------------------------------------------
    def _solve_b8(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        sub = task.get("subfamily")
        if sub == "extraction":
            return self._solve_extraction(task, ctx)
        if sub == "synthesis":
            return self._solve_synthesis(task, ctx)
        if sub == "planning":
            return self._solve_planning(task, ctx)
        return SolveResult(status="failed", message=f"unknown subfamily {sub!r}")

    def _solve_extraction(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        spans = [(span["span_id"], span["text"]) for doc in task.get("documents", [])
                 for span in doc.get("spans", [])]
        joined = "\n".join(text for _, text in spans)
        fields: dict[str, str] = {}
        keys = ("invoice_id", "date") if self.profile == "naive" else tuple(INVOICE_PATTERNS)
        for key in keys:
            match = INVOICE_PATTERNS[key].search(joined)
            if match:
                value = match.group(1) if INVOICE_PATTERNS[key].groups else match.group(0)
                fields[key] = value.strip().replace(",", "")

        cited = None
        if fields.get("invoice_id"):
            cited = next((span_id for span_id, text in spans
                          if fields["invoice_id"] in text), spans[0][0] if spans else None)
        claims = []
        if cited and self.verifies:
            claims = [{"text": fields.get("invoice_id", ""), "span_id": cited}]
        return SolveResult(status="done" if fields else "failed",
                           message="extracted " + ", ".join(sorted(fields)),
                           claims=claims, notes={"fields": fields}, steps=1)

    def _solve_synthesis(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        pattern = re.compile(task["pattern"])
        values: list[tuple[str, str]] = []
        for doc in task["documents"]:
            match = pattern.search(doc["text"])
            if match:
                values.append((doc["doc_id"], match.group(0)))
        if not values:
            return SolveResult(status="failed", message="no source carried the value")

        if self.profile == "naive":
            doc_id, value = values[0]
            return SolveResult(status="done", message=f"value is {value}",
                               notes={"conflicts": [], "value": value,
                                      "sources": len(values)}, steps=1)

        counts: dict[str, int] = {}
        for _, value in values:
            counts[value] = counts.get(value, 0) + 1
        majority = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        conflicts = [] if ctx.mutated("skip_conflict_detection") else \
            [doc_id for doc_id, value in values if value != majority]
        return SolveResult(status="done", message=f"value is {majority}",
                           notes={"conflicts": conflicts, "value": majority,
                                  "sources": len(values)},
                           steps=1)

    def _solve_planning(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        assignment = (self._greedy_assignment(task) if self.profile == "naive"
                      else self._search_assignment(task))
        if assignment is None:
            return SolveResult(status="failed", message="no assignment found")
        problems = validate_assignment(task, assignment)
        if problems and self.verifies:
            return SolveResult(status="failed",
                               message="assignment violated constraints: "
                                       + "; ".join(problems[:3]),
                               notes={"assignment": assignment}, steps=1)
        return SolveResult(status="done", message="assignment produced",
                           notes={"assignment": assignment}, steps=1)

    def _candidates(self, task: dict[str, Any]) -> list[tuple[str, str]]:
        rooms = sorted(room["room_id"] for room in task["rooms"])
        return [(room, slot) for slot in task["slots"] for room in rooms]

    def _partial_ok(self, task: dict[str, Any], assignment: dict[str, Any],
                    meeting_id: str) -> bool:
        probe = dict(assignment)
        return _local_check(task, probe, meeting_id)

    def _greedy_assignment(self, task: dict[str, Any]) -> dict[str, Any] | None:
        assignment: dict[str, Any] = {}
        for meeting in task["meetings"]:
            placed = False
            for room, slot in self._candidates(task):
                trial = dict(assignment)
                trial[meeting["meeting_id"]] = {"room": room, "slot": slot}
                if _local_check(task, trial, meeting["meeting_id"]):
                    assignment = trial
                    placed = True
                    break
            if not placed:
                return None
        return assignment

    def _search_assignment(self, task: dict[str, Any]) -> dict[str, Any] | None:
        order = sorted(task["meetings"],
                       key=lambda m: (-m["min_capacity"], -m["duration_h"],
                                      m["meeting_id"]))
        candidates = self._candidates(task)

        def backtrack(position: int, assignment: dict[str, Any]) -> dict[str, Any] | None:
            if position == len(order):
                return dict(assignment)
            meeting_id = order[position]["meeting_id"]
            for room, slot in candidates:
                trial = dict(assignment)
                trial[meeting_id] = {"room": room, "slot": slot}
                if _local_check(task, trial, meeting_id):
                    found = backtrack(position + 1, trial)
                    if found is not None:
                        return found
            return None

        return backtrack(0, {})

    # -- B9: skill reuse --------------------------------------------------
    def _solve_b9(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        skill = task.get("skill", {})
        use_skill = (self.profile != "naive"
                     and not ctx.mutated("disable_skill_reuse")
                     and bool(skill.get("steps")))
        steps = skill.get("steps", []) if use_skill else [task["fallback"]]

        for step in steps:
            try:
                ctx.call(step["tool"], dict(step["args"]),
                         idempotency_key=f"{task['id']}:{step.get('id', 'fallback')}")
            except BudgetExceeded as exc:
                return SolveResult(status="partial", message=str(exc),
                                   steps=ctx.tool_calls)
            except ToolError as exc:
                return SolveResult(status="failed", message=str(exc),
                                   steps=ctx.tool_calls)

        if not self.verifies:
            return SolveResult(status="done", message="bundle written",
                               notes={"used_skill": use_skill}, steps=ctx.tool_calls)

        problems = verify_world(task.get("verify", {}), ctx.world_snapshot())
        if problems:
            return SolveResult(status="failed",
                               message="acceptance checks failed: " + "; ".join(problems),
                               notes={"used_skill": use_skill}, steps=ctx.tool_calls)
        return SolveResult(status="done", message="bundle written and checked",
                           notes={"used_skill": use_skill}, steps=ctx.tool_calls)

    # -- B10: memory consolidation ---------------------------------------
    def _solve_b10(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        naive = self.profile == "naive" or ctx.mutated("accept_poisoned_memory")
        facts = task["seed_memory"]
        now = task["now"]

        if naive:
            # Recency only: trust and expiry are ignored, which is exactly how a
            # poisoned or stale claim becomes "authoritative".
            alive = sorted(facts, key=lambda f: (f["valid_from"], f["fact_id"]))
            surviving = alive[-1]
            superseded = [f["fact_id"] for f in alive[:-1]]
            expired: list[str] = []
        else:
            expired = [f["fact_id"] for f in facts
                       if f.get("valid_until") and f["valid_until"] < now]
            candidates = [f for f in facts
                          if f["fact_id"] not in expired and f["trust"] != "untrusted"]
            if not candidates:
                return SolveResult(status="failed", message="no trustworthy fact remains")
            ranked = sorted(candidates,
                            key=lambda f: (TRUST_RANK[f["trust"]], f["valid_from"],
                                           f["fact_id"]))
            surviving = ranked[-1]
            superseded = [f["fact_id"] for f in candidates
                          if f["fact_id"] != surviving["fact_id"]]

        state = {"surviving": [surviving["fact_id"]],
                 "superseded": sorted(superseded), "expired": sorted(expired)}
        return SolveResult(status="done", message=surviving["claim"], notes={
            "memory_state": state, "surviving_claim": surviving["claim"],
            "naive_consolidation": naive,
        }, steps=1)


def _local_check(task: dict[str, Any], assignment: dict[str, Any],
                 meeting_id: str) -> bool:
    """Partial validity: only the rules that involve already-placed meetings."""
    rooms = {room["room_id"]: room for room in task["rooms"]}
    meetings = {m["meeting_id"]: m for m in task["meetings"]}
    index = {slot: i for i, slot in enumerate(task["slots"])}
    slots = task["slots"]

    entry = assignment[meeting_id]
    if entry["room"] not in rooms or entry["slot"] not in index:
        return False
    meeting = meetings[meeting_id]
    room = rooms[entry["room"]]
    start = index[entry["slot"]]
    duration = int(meeting["duration_h"])
    if room["capacity"] < meeting["min_capacity"]:
        return False
    if start + duration > len(slots):
        return False
    busy = set(room.get("busy", []))
    for offset in range(duration):
        if slots[start + offset] in busy:
            return False
    for other_id, other in assignment.items():
        if other_id == meeting_id:
            continue
        other_meeting = meetings[other_id]
        other_start = index[other["slot"]]
        if other["room"] != entry["room"]:
            continue
        span_a = range(start, start + duration)
        span_b = range(other_start, other_start + int(other_meeting["duration_h"]))
        if set(span_a) & set(span_b):
            return False
    for constraint in task["constraints"]:
        named = [constraint["meeting"]] if constraint.get("meeting") else \
            list(constraint.get("meetings", []))
        if meeting_id not in named:
            continue
        if constraint["kind"] == "before":
            if index[assignment[meeting_id]["slot"]] >= index[constraint["slot"]]:
                return False
        elif constraint["kind"] == "after":
            if index[assignment[meeting_id]["slot"]] <= index[constraint["slot"]]:
                return False
        elif constraint["kind"] == "at":
            if index[assignment[meeting_id]["slot"]] != index[constraint["slot"]]:
                return False
        elif constraint["kind"] == "different_rooms":
            first, second = named[:2]
            if first in assignment and second in assignment and \
                    assignment[first]["room"] == assignment[second]["room"]:
                return False
    return True


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
