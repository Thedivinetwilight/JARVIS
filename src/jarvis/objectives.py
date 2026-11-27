"""Objective-driven autonomy: turn an owner's request into a verified outcome.

Mission 002. An **objective** is the owner's request plus everything needed to judge it:
what outcome is wanted, which constraints apply, what counts as success, the plan that
pursues it, and the evidence-based outcome at the end.

Design
------
* **Rules first.** A deterministic planner handles the objective families JARVIS actually
  supports, offline and reproducibly (ADR-0020). It decomposes multi-clause requests,
  inserts dependencies, and refuses what it cannot do instead of inventing a capability.
* **Model second, validated always.** When no rule matches and a model provider is
  configured, the provider may *propose* a plan. Its output is untrusted: every step is
  validated against the registry (capability exists, arguments match the tool schema,
  dependencies sane), risk is read from the registry and never from the model, and one
  bounded repair attempt is allowed (ADR-0021).
* **Execution is the existing runtime.** Plans are persisted through ``plans.PlanStore`` and
  executed by ``plans.PlanExecutor``; every step goes through the permission-checked
  ``ToolRegistry``. Nothing here can bypass policy, scopes or egress.
* **Completion is evidence.** An objective is COMPLETED only when required steps succeeded,
  their tool postconditions verified, and the objective-level criteria were met. A model
  saying "done" is not evidence and is never consulted for this.

Bounds: ``MAX_STEPS`` per plan, ``MAX_REPLANS`` per objective, a wall-clock budget, a step
limit across replans, and cancellation checked between steps.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .charter import untrusted_block
from .contracts import (ActionOutcome, ActionRequest, ObjectiveOutcome, OperationStatus, Origin)
from .leases import LeaseError, LeaseStore
from .interpret import parse_keyed
from .plans import (MAX_REPLANS, MAX_STEPS, PlanError, PlanExecutor, PlanRecord, PlanStore)
from .redact import redact, redact_text
from .reminders import parse_due
from .util import to_iso

MAX_SUBTASKS = 5                 # clauses a request may be split into
MAX_PLAN_ATTEMPTS = 2            # model plan + one repair
MAX_OBJECTIVE_STEPS = 24         # steps executed across replans
MAX_PLAN_REVISIONS = 2           # structural re-decompositions per objective (so 3 plans max)
LEASE_TTL_SECONDS = 60           # single-writer lease on an objective; renewed every iteration
DEFAULT_BUDGET_SECONDS = 120.0
MAX_BUDGET_SECONDS = 900.0
MIN_BUDGET_SECONDS = 1.0
MAX_REQUEST_CHARS = 4000
PLANNERS = ("rules", "model")


class PlanningError(RuntimeError):
    """The objective cannot be planned as asked (blocked), with a reason."""


class PlanInvalid(PlanningError):
    """A proposed plan is structurally unexecutable."""


# --------------------------------------------------------------------------------------
# Plan specification (in memory; persisted through PlanStore)
# --------------------------------------------------------------------------------------
@dataclass
class StepSpec:
    capability: str
    arguments: dict[str, Any]
    purpose: str
    expected_output: str = ""
    success_criteria: str = ""
    depends_on: tuple[int, ...] = ()
    optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"capability": self.capability, "arguments": self.arguments, "purpose": self.purpose,
                "expected_output": self.expected_output, "success_criteria": self.success_criteria,
                "depends_on": list(self.depends_on), "optional": self.optional}


@dataclass
class PlanSpec:
    objective: str
    steps: list[StepSpec]
    criteria: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""                       # set for objectives that need no tool at all
    notes: list[str] = field(default_factory=list)
    planner: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {"objective": self.objective, "steps": [s.to_dict() for s in self.steps],
                "criteria": self.criteria, "answer": self.answer, "notes": self.notes,
                "planner": self.planner}


@dataclass
class PlanningOutcome:
    """Either a plan, a question that must be asked, or an honest block."""

    kind: str                                  # plan | awaiting_information | blocked
    plan: PlanSpec | None = None
    question: str = ""
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind == "plan" and self.plan is not None


# --------------------------------------------------------------------------------------
# Objective record
# --------------------------------------------------------------------------------------
#: Outcomes that end an objective. A pause carries an outcome too, because the owner must be
#: told what happened, but it can be resumed once the blocker is gone.
TERMINAL_OUTCOMES = frozenset({"completed", "partially_completed", "failed", "blocked", "cancelled"})
PAUSED_OUTCOMES = frozenset({"needs_approval", "awaiting_information"})
ALL_OUTCOMES = TERMINAL_OUTCOMES | PAUSED_OUTCOMES
@dataclass
class Objective:
    id: str
    request: str
    objective: str
    desired_outcome: str
    constraints: list[str]
    success_criteria: list[dict[str, Any]]
    memory_ids: list[int]
    plan_id: str | None
    status: str                 # planned | running | awaiting_approval | ... | finished states
    outcome: str | None
    outcome_reason: str
    priority: int
    deadline: str | None
    budget: dict[str, Any]
    verification: dict[str, Any]
    planner: str
    origin: str
    attempts: int
    replans: int
    created_at: str
    updated_at: str
    finished_at: str | None
    revision: int = 1
    schedule_id: str | None = None
    scheduled_for: str | None = None

    @property
    def finished(self) -> bool:
        """A terminal outcome. ``needs_approval``/``awaiting_information`` are pauses, not ends."""
        return self.outcome in TERMINAL_OUTCOMES

    @property
    def paused(self) -> bool:
        return self.outcome in PAUSED_OUTCOMES

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "request": self.request, "objective": self.objective,
            "desired_outcome": self.desired_outcome, "constraints": self.constraints,
            "success_criteria": self.success_criteria, "memory_ids": self.memory_ids,
            "plan_id": self.plan_id, "status": self.status, "outcome": self.outcome,
            "outcome_reason": self.outcome_reason, "priority": self.priority,
            "deadline": self.deadline, "budget": self.budget, "verification": self.verification,
            "planner": self.planner, "origin": self.origin, "attempts": self.attempts,
            "replans": self.replans, "revision": self.revision,
            "schedule_id": self.schedule_id, "scheduled_for": self.scheduled_for,
            "created_at": self.created_at,
            "updated_at": self.updated_at, "finished_at": self.finished_at,
        }


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------
_REFERENCE = re.compile(r"^\{\{step(\d+)\.([a-z_][a-z0-9_.]*)\}\}$")


def _references(arguments: Any) -> Iterable[int]:
    """Step numbers referenced by ``{{stepN.path}}`` placeholders anywhere in the arguments."""
    if isinstance(arguments, str):
        match = _REFERENCE.match(arguments)
        if match:
            yield int(match.group(1))
    elif isinstance(arguments, dict):
        for value in arguments.values():
            yield from _references(value)
    elif isinstance(arguments, (list, tuple)):
        for value in arguments:
            yield from _references(value)


def _materialise_references(arguments: Any, schema: dict[str, Any]) -> Any:
    """Replace ``{{stepN.path}}`` placeholders with a value of the declared argument type."""
    if isinstance(arguments, str):
        if _REFERENCE.match(arguments):
            return 0
        return arguments
    if isinstance(arguments, dict):
        result: dict[str, Any] = {}
        for key, value in arguments.items():
            declared = schema.get(key, str)
            if isinstance(value, str) and _REFERENCE.match(value):
                result[key] = (0 if declared is int else "" if declared is str
                               else [] if declared is list else True)
            else:
                result[key] = _materialise_references(value, {})
        return result
    if isinstance(arguments, list):
        return [_materialise_references(item, {}) for item in arguments]
    return arguments


def validate_plan_spec(spec: PlanSpec, registry, *, allow_empty: bool = False) -> None:
    """Reject a plan that cannot be executed. Cheap, structural, no side effects.

    Checks: step count, non-empty unless answer-only, known capability, arguments exact
    against the tool schema (types included), dependency indices that exist and point
    backwards (so the graph is acyclic by construction), and no self-dependency.
    """
    if not allow_empty and not spec.steps:
        raise PlanInvalid("a plan needs at least one step")
    if len(spec.steps) > MAX_STEPS:
        raise PlanInvalid(f"a plan may have at most {MAX_STEPS} steps, got {len(spec.steps)}")
    seen: set[int] = set()
    for index, step in enumerate(spec.steps, start=1):
        if step.capability not in registry.names():
            raise PlanInvalid(f"step {index}: unknown capability '{step.capability}'")
        if not step.purpose.strip():
            raise PlanInvalid(f"step {index}: every step needs a purpose")
        for dependency in step.depends_on:
            if dependency not in seen:
                raise PlanInvalid(
                    f"step {index}: dependency {dependency} must reference an earlier step")
        references = set(_references(step.arguments))
        for reference in references:
            if reference not in seen:
                raise PlanInvalid(
                    f"step {index}: reference {{{{step{reference}.…}}}} must point to an earlier step")
        # A placeholder stands in for a value of the tool's declared type, so the schema check
        # uses a type-correct probe and execution resolves the real value.
        schema = registry.get(step.capability).schema
        probe = _materialise_references(step.arguments, schema)
        try:
            registry.validate(step.capability, probe)
        except Exception as exc:                     # ToolError and type errors alike
            raise PlanInvalid(f"step {index} ({step.capability}): {exc}") from None
        seen.add(index)
    for criterion in spec.criteria:
        if not isinstance(criterion, dict) or criterion.get("kind") not in _CRITERION_KINDS:
            raise PlanInvalid(f"unknown success criterion: {criterion!r}")


# --------------------------------------------------------------------------------------
# Success criteria (evaluated from recorded evidence, never from model prose)
# --------------------------------------------------------------------------------------
_CRITERION_KINDS = frozenset({"required_steps_verified", "capability_succeeded",
                              "min_succeeded", "answer_provided", "no_failed_required_step"})


def default_criteria(steps: list[StepSpec]) -> list[dict[str, Any]]:
    criteria: list[dict[str, Any]] = [{"kind": "required_steps_verified"}]
    for step in steps:
        if not step.optional:
            criteria.append({"kind": "capability_succeeded", "capability": step.capability})
    return criteria



# --------------------------------------------------------------------------------------
# Structural re-decomposition helpers
# --------------------------------------------------------------------------------------
def _canonical(arguments: Any) -> str:
    """Stable signature of a step's arguments, including any unresolved references."""
    try:
        return json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):                                  # pragma: no cover - defensive
        return repr(arguments)


def _spec_from_step(step) -> StepSpec:
    """A persisted step expressed as a plan spec (used to re-derive success criteria)."""
    return StepSpec(step.capability, dict(step.arguments), step.purpose,
                    step.expected_output, step.success_criteria,
                    tuple(step.depends_on or ()), bool(step.optional))


def _remap_references(value: Any, mapping: dict[int, int]) -> tuple[Any, list[int]]:
    """Rewrite ``{{stepN.field}}`` for a revised plan; report references that cannot move."""
    broken: list[int] = []

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            match = _REFERENCE.match(node)
            if match:
                old = int(match.group(1))
                if old not in mapping:
                    broken.append(old)
                    return node
                return f"{{{{step{mapping[old]}.{match.group(2)}}}}}"
            return node
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(value), broken


# --------------------------------------------------------------------------------------
# Deterministic rules planner
# --------------------------------------------------------------------------------------
_SPLIT = re.compile(r"\s*(?:;|\bthen\b|\.\s+|\n+)\s*", re.IGNORECASE)
_AND = re.compile(r"\s+and\s+(?:then\s+)?", re.IGNORECASE)
_TIME = re.compile(r"^(?:what(?:'s| is) the time|what time is it|current time|what(?:'s| is) the date|"
                   r"what day is (?:it|today))\s*\??$", re.IGNORECASE)
_CALC = re.compile(r"^(?:calculate|compute|evaluate|how much is)\s+([-+*/%^().\d\s]+?)\s*\??$", re.IGNORECASE)
_REMEMBER = re.compile(r"^(?:please\s+)?(?:remember|note|save|keep in mind|don'?t forget)"
                       r"\s*(?:that|this|:)?\s*(?P<body>.+)$", re.IGNORECASE | re.DOTALL)
_RECALL = re.compile(r"^(?:what do you (?:know|remember) about|what did i (?:say|tell you) about|"
                     r"what(?:'s| is| was) my|recall|search (?:my )?memory for|find (?:my|the))\s+"
                     r"(?P<query>.+?)\s*\??$", re.IGNORECASE)
_GOAL = re.compile(r"^(?:new goal|goal|objective)\s*[:\-]\s*(?P<title>.+?)"
                   r"(?:\s*(?:;|\.|\n)\s*(?:steps?|tasks?)\s*[:\-]\s*(?P<steps>.+))?$", re.IGNORECASE | re.DOTALL)
_GOAL_AND_TASK = re.compile(
    r"^(?:create|add|set up|make)\s+(?:a\s+)?goal\s+(?:called|named|for|to)?\s*[:\-]?\s*"
    r"(?P<goal>.+?)\s*,?\s*and\s+(?:then\s+)?add\s+(?:a\s+)?task\s*"
    r"(?:called|named|for)?\s*[:\-]?\s*(?P<task>.+)$",
    re.IGNORECASE | re.DOTALL)
_TASK_ADD = re.compile(r"^(?:add\s+(?:a\s+)?task|new task|todo|to-do|task)\s*[:\-]?\s*(?P<title>.+?)"
                       r"(?:\s+(?:to|for)\s+goal\s*#?\s*(?P<goal>\d+))?\s*$", re.IGNORECASE)
_REMIND = re.compile(r"^(?:please\s+)?remind\s+me(?:\s+(?:to|that|about))?\s+(?P<title>.+?)"
                     r"(?:\s+(?P<when>(?:in|at|by)\s+.+|today(?:\s+(?:at\s+)?.+)?|tomorrow(?:\s+(?:at\s+)?.+)?|"
                     r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+at\s+.+)?))?\s*[.!]?$",
                     re.IGNORECASE)
_PROJECTS = re.compile(r"^(?:list|show)(?:\s+me)?\s+(?:my\s+)?projects\s*[.!]?$", re.IGNORECASE)
_TASKS = re.compile(r"^(?:list|show)(?:\s+me)?\s+(?:my\s+)?(?:open\s+)?tasks(?:\s+(?P<status>todo|in_progress|"
                    r"blocked|done))?\s*[.!]?$", re.IGNORECASE)
_GOALS = re.compile(r"^(?:list|show)(?:\s+me)?\s+(?:my\s+)?goals\s*[.!]?$", re.IGNORECASE)
_FILES = re.compile(r"^(?:list|show)(?:\s+me)?\s+(?:the\s+)?files(?:\s+(?:in|under)\s+(?P<path>.+?))?\s*[.!]?$",
                    re.IGNORECASE)
_URL = re.compile(r"""https?://[^\s<>"')]+""", re.IGNORECASE)
_WS_DOCS = re.compile(
    r"^(?:please\s+)?(?:find|search(?:\s+for)?|look\s+for|show\s+me|get)\s+(?:all\s+)?(?:the\s+)?"
    r"(?:documents?|files?|notes?|docs)\s+"
    r"(?:about|on|regarding|relating\s+to|related\s+to|mentioning|containing|that\s+mention)\s+"
    r"(?P<query>.+?)(?:,?\s+(?P<summ>and\s+summari[sz]e(?:\s+them|\s+the\s+results?|\s+it)?))?\s*[.!]?$",
    re.IGNORECASE)
_WS_SUMMARY_ONLY = re.compile(
    r"^(?:please\s+)?summari[sz]e\s+(?:the\s+)?(?:documents?|files?|notes?|findings|information|content)\s+"
    r"(?:about|on|regarding|relating\s+to|related\s+to|mentioning|containing)\s+(?P<query>.+?)"
    r"(?:\s+(?:in|from|under)\s+(?:the\s+)?workspace(?:\s+roots?)?)?\s*[.!]?$", re.IGNORECASE)
_WS_SEARCH = re.compile(
    r"^(?:please\s+)?(?:search|grep|look\s+through)\s+(?:the\s+)?workspace(?:\s+roots?)?\s+for\s+"
    r"(?P<query>.+?)\s*[.!]?$", re.IGNORECASE)
_WS_FIND = re.compile(
    r"^(?:please\s+)?(?:find|list)\s+(?:all\s+)?(?:the\s+)?files?\s+(?:named|called|matching)\s+"
    r"(?P<pattern>.+?)\s*[.!]?$", re.IGNORECASE)
_WS_READ = re.compile(
    r"^(?:please\s+)?(?:read|open|show(?:\s+me)?)\s+(?:the\s+)?(?:file\s+)?"
    r"(?P<path>[\w./\\-]+\.[A-Za-z0-9]{1,10})\s*[.!]?$", re.IGNORECASE)
_WS_STAT = re.compile(
    r"^(?:what(?:'s| is)|show|get)\s+(?:me\s+)?(?:the\s+)?(?:size|metadata|details|info)\s+"
    r"(?:of|for)\s+(?:the\s+)?(?:file\s+)?(?P<path>[\w./\\-]+\.[A-Za-z0-9]{1,10})\s*[?]?$",
    re.IGNORECASE)
_RESEARCH = re.compile(r"^(?:research|look up|find out about|search the web for|google)\s+(?P<topic>.+)$",
                       re.IGNORECASE)
_UNTRUSTED = re.compile(r"(ignore (?:all |the |your )?(?:previous|prior|above) instructions|"
                        r"disregard (?:the |your )?(?:rules|policy|charter)|"
                        r"you are now|reveal (?:the |your )?(?:api key|secret|token)|"
                        r"run (?:this )?(?:shell )?command|execute (?:the )?command|rm -rf|sudo )",
                        re.IGNORECASE)


class RulesPlanner:
    """Deterministic decomposition for the objective families JARVIS supports."""

    def __init__(self, registry, store) -> None:
        self.registry = registry
        self.store = store

    # ------------------------------------------------------------------ entry
    def plan(self, request: str, *, now=None) -> PlanningOutcome:
        text = " ".join(request.split()).strip()
        if not text:
            return PlanningOutcome("awaiting_information", question="What would you like me to do?")
        if len(text) > MAX_REQUEST_CHARS:
            return PlanningOutcome("blocked", reason=f"request is longer than {MAX_REQUEST_CHARS} characters")

        clauses = [re.sub(r"^(?:and|then)\s+", "", c.strip(" .!"))
                   for c in _SPLIT.split(text) if c.strip(" .!")]
        if len(clauses) > MAX_SUBTASKS:
            return PlanningOutcome(
                "blocked", reason=f"request has {len(clauses)} clauses; at most {MAX_SUBTASKS} are supported")

        steps: list[StepSpec] = []
        notes: list[str] = []
        for clause in clauses:
            built = self._clause(clause, steps, notes, now=now)
            if isinstance(built, PlanningOutcome):        # question or block: stop and report
                return built
            if built:
                steps = built

        if not steps:
            return PlanningOutcome("blocked",
                                   reason="no capability in this build can pursue that objective",
                                   notes=["capabilities: " + ", ".join(self.registry.names())])
        if len(steps) > MAX_STEPS:
            return PlanningOutcome("blocked", reason=f"objective needs more than {MAX_STEPS} steps")
        objective = _objective_sentence(text)
        return PlanningOutcome(
            "plan",
            plan=PlanSpec(objective=objective, steps=steps, criteria=default_criteria(steps),
                          notes=notes, planner="rules"))

    # ------------------------------------------------------------------ clauses
    def _clause(self, clause: str, prior: list[StepSpec], notes: list[str],
                *, now) -> list[StepSpec] | PlanningOutcome | None:
        """Map one clause to steps, extending ``prior``. Returns None when nothing matched."""
        # A clause may still be two commands joined by "and"; split only if both halves match.
        if not self._matches_any(clause):
            halves = [h.strip() for h in _AND.split(clause) if h.strip()]
            if len(halves) == 2 and all(self._matches_any(h) for h in halves):
                current = list(prior)
                for half in halves:
                    extended = self._clause(half, current, notes, now=now)
                    if isinstance(extended, PlanningOutcome):
                        return extended
                    if extended:
                        current = extended
                return current

        # ---- Mission 003: retrieval and workspace intelligence -------------------------
        url_match = _URL.search(clause)
        if url_match:
            url = url_match.group(0).rstrip(".,;)")
            if not (self.registry.get("web.get") and self.registry.get("web.summarize")):
                return PlanningOutcome(
                    "blocked",
                    reason=("web retrieval is not configured in this build: no domain is "
                            "allow-listed, so the request cannot be sent. Configure "
                            "JARVIS_ALLOWLIST (and JARVIS_NETWORK_ENABLED=true) to enable it."),
                    notes=["no capability was invented or substituted"])
            wants_summary = bool(re.search(r"summari[sz]e|digest|tl;?dr", clause, re.IGNORECASE))
            if wants_summary:
                spec = StepSpec("web.summarize", {"url": url, "max_sentences": 5},
                                purpose=f"retrieve and summarize {url}",
                                expected_output="an extractive summary with provenance",
                                success_criteria="HTTP 200 and a summary were produced")
            else:
                spec = StepSpec("web.get", {"url": url},
                                purpose=f"retrieve {url}",
                                expected_output="readable page text with provenance",
                                success_criteria="HTTP 200 and text were returned")
            if not self._validated(spec, notes):
                return PlanningOutcome("blocked",
                                       reason=f"the retrieval step could not be built for {url}")
            return self._add(prior, spec)
        match = _WS_SUMMARY_ONLY.match(clause)
        if match:
            query = match.group("query").strip(" ,.")
            if not query:
                return PlanningOutcome("awaiting_information",
                                       question="What should I summarize the workspace documents about?")
            spec = StepSpec("workspace.summarize",
                            {"query": query, "max_sentences": 5, "limit": 10},
                            purpose=f"summarize workspace documents about '{query}'",
                            expected_output="an extractive summary with per-document provenance",
                            success_criteria="the workspace was searched and a summary was produced")
            if not self._validated(spec, notes):
                return PlanningOutcome("blocked", reason="workspace summarization is unavailable")
            return self._add(prior, spec)
        match = _WS_DOCS.match(clause)
        if match:
            query = match.group("query").strip(" ,.")
            if not query:
                return PlanningOutcome("awaiting_information",
                                       question="What should I look for in the workspace?")
            if match.group("summ"):
                spec = StepSpec("workspace.summarize",
                                {"query": query, "max_sentences": 5, "limit": 10},
                                purpose=f"find and summarize workspace documents about '{query}'",
                                expected_output="a summary with per-document provenance",
                                success_criteria="the workspace was searched and a summary was produced")
            else:
                spec = StepSpec("workspace.search", {"query": query, "limit": 20},
                                purpose=f"search workspace documents for '{query}'",
                                expected_output="ranked matching documents",
                                success_criteria="the workspace was searched")
            if not self._validated(spec, notes):
                return PlanningOutcome("blocked", reason="workspace search is unavailable")
            return self._add(prior, spec)
        match = _WS_SEARCH.match(clause)
        if match:
            query = match.group("query").strip(" ,.")
            spec = StepSpec("workspace.search", {"query": query, "limit": 20},
                            purpose=f"search the workspace for '{query}'",
                            expected_output="ranked matching documents",
                            success_criteria="the workspace was searched")
            if not self._validated(spec, notes):
                return PlanningOutcome("blocked", reason="workspace search is unavailable")
            return self._add(prior, spec)
        match = _WS_FIND.match(clause)
        if match:
            pattern = match.group("pattern").strip(" ,.")
            spec = StepSpec("workspace.find", {"pattern": pattern, "limit": 20},
                            purpose=f"find workspace files named '{pattern}'",
                            expected_output="matching file paths",
                            success_criteria="the workspace was scanned")
            if not self._validated(spec, notes):
                return PlanningOutcome("blocked", reason="workspace file search is unavailable")
            return self._add(prior, spec)
        match = _WS_STAT.match(clause)
        if match:
            path = match.group("path").strip()
            spec = StepSpec("workspace.stat", {"relative_path": path},
                            purpose=f"read metadata for {path}",
                            expected_output="file metadata",
                            success_criteria="the path exists inside a workspace root")
            if not self._validated(spec, notes):
                return PlanningOutcome("blocked", reason=f"'{path}' is not a readable workspace path")
            return self._add(prior, spec)
        match = _WS_READ.match(clause)
        if match:
            path = match.group("path").strip()
            spec = StepSpec("workspace.read", {"relative_path": path},
                            purpose=f"read {path}",
                            expected_output="the file text",
                            success_criteria="the file was read inside a workspace root")
            if not self._validated(spec, notes):
                return PlanningOutcome("blocked", reason=f"'{path}' is not a readable workspace path")
            return self._add(prior, spec)
        match = _TIME.match(clause)
        if match:
            return self._add(prior, StepSpec("time.now", {}, purpose="read the clock",
                                             expected_output="current UTC time",
                                             success_criteria="a UTC timestamp is returned"))
        match = _CALC.match(clause)
        if match:
            expression = match.group(1).strip()
            spec = StepSpec("math.calculate", {"expression": expression},
                            purpose=f"evaluate {expression}", expected_output="numeric result",
                            success_criteria="the expression is evaluated and independently re-checked")
            if not self._validated(spec, notes):
                return PlanningOutcome("awaiting_information",
                                       question=f"What arithmetic should I evaluate? I could not read "
                                                f"'{expression}' as an expression.")
            return self._add(prior, spec)
        if _REMEMBER.match(clause):
            body = _REMEMBER.match(clause).group("body").strip()
            if not body:
                return PlanningOutcome("awaiting_information",
                                       question="What should I remember? Tell me the fact itself.")
            keyed = parse_keyed(body)
            step = StepSpec("memory.remember",
                            {"value": body, "key": keyed.get("key", ""),
                             "category": keyed.get("category", "general"),
                             "kind": "fact" if keyed.get("key") else "note",
                             "sensitivity": "normal", "correction": False},
                            purpose="store the statement with provenance",
                            expected_output="a memory item id",
                            success_criteria="the item exists and is retrievable")
            return self._add(prior, step)
        match = _RECALL.match(clause)
        if match:
            query = match.group("query").strip()
            if clause.lower().startswith("recall "):
                query = match.group("query").strip()
            return self._add(prior, StepSpec("memory.search", {"query": query},
                                             purpose="search persistent memory",
                                             expected_output="ranked memory hits",
                                             success_criteria="the search ran; an empty result is a valid answer"))
        match = _GOAL_AND_TASK.match(clause)
        if match:
            goal_title = match.group("goal").strip(" ,.")
            task_title = match.group("task").strip()
            task_title = re.sub(r"\s+(?:to|for)\s+(?:it|that goal|this goal|the goal)\s*[.!]?$",
                                "", task_title, flags=re.IGNORECASE).strip(" ,.")
            goal_step = StepSpec("goal.create", {"title": goal_title, "steps": [], "project": ""},
                                 purpose=f"create the goal '{goal_title}'",
                                 expected_output="a goal with an id",
                                 success_criteria="the goal exists")
            task_step = StepSpec("task.add", {"title": task_title, "goal_id": "{{step1.id}}"},
                                 purpose=f"add the task '{task_title}' to that goal",
                                 expected_output="a task attached to the new goal",
                                 success_criteria="the task exists with that goal id",
                                 depends_on=(1,))
            steps = self._add(prior, goal_step)
            if steps is None:
                steps = list(prior)
            base = len(steps)
            task_step.depends_on = (base,)
            task_step.arguments = {"title": task_title, "goal_id": f"{{{{step{base}.id}}}}"}
            steps.append(task_step)
            return steps
        match = _GOAL.match(clause)
        if match:
            title = match.group("title").strip()
            raw_steps = (match.group("steps") or "").strip()
            if not title:
                return PlanningOutcome("awaiting_information", question="What is the goal?")
            ordered = [part.strip() for part in re.split(r"\s*(?:,|;|\band then\b|\bthen\b)\s*", raw_steps)
                       if part.strip()]
            return self._add(prior, StepSpec("goal.create", {"title": title, "steps": ordered,
                                                             "project": ""},
                                             purpose=f"create the goal '{title}'",
                                             expected_output="a goal with ordered steps",
                                             success_criteria="the goal and its steps exist"))
        match = _REMIND.match(clause)
        if match:
            title = match.group("title").strip(" ,.")
            when = (match.group("when") or "").strip(" ,.")
            if not title:
                return PlanningOutcome("awaiting_information",
                                       question="What should I remind you about?")
            due = parse_due(when, now or self.store.now()) if when else None
            if due is None:
                hint = "in 20 minutes" if not when else f"a time I can read (you said '{when}')"
                return PlanningOutcome(
                    "awaiting_information",
                    question=f"When should I remind you to {title}? Give me {hint} — for example "
                             f"\"in 20 minutes\", \"tomorrow at 9am\" or \"friday at 18:00\".")
            return self._add(prior, StepSpec(
                "reminder.create",
                {"title": title, "due_at": to_iso(due), "notes": "", "delivery_client_id": ""},
                purpose=f"remind the owner to {title}",
                expected_output="a pending reminder with a due time",
                success_criteria="the reminder row exists with that due time"))
        match = _TASK_ADD.match(clause)
        if match:
            title = match.group("title").strip(" ,.")
            if not title:
                return PlanningOutcome("awaiting_information", question="What is the task?")
            goal_id = int(match.group("goal") or 0)
            return self._add(prior, StepSpec("task.add", {"title": title, "goal_id": goal_id},
                                             purpose=f"add the task '{title}'",
                                             expected_output="a task row",
                                             success_criteria="the task exists"))
        if _PROJECTS.match(clause):
            return self._add(prior, StepSpec("project.list", {}, purpose="list projects",
                                             expected_output="the project list",
                                             success_criteria="the list was read"))
        match = _TASKS.match(clause)
        if match:
            return self._add(prior, StepSpec("task.list", {"status": (match.group("status") or "").lower()},
                                             purpose="list tasks", expected_output="the task list",
                                             success_criteria="the list was read"))
        if _GOALS.match(clause):
            return self._add(prior, StepSpec("goal.list", {}, purpose="list goals",
                                             expected_output="goals with progress",
                                             success_criteria="the list was read"))
        match = _FILES.match(clause)
        if match:
            return self._add(prior, StepSpec("workspace.list",
                                             {"relative_path": (match.group("path") or ".").strip()},
                                             purpose="list workspace files",
                                             expected_output="a file listing",
                                             success_criteria="the listing was read"))
        match = _RESEARCH.match(clause)
        if match:
            return PlanningOutcome(
                "blocked",
                reason=("no search provider is integrated, so I cannot research "
                        f"'{match.group('topic').strip()[:120]}'. I can fetch a specific page from an "
                        "allow-listed domain if you give me its URL, or search your workspace."),
                notes=["retrieval is a direct allow-listed GET (web.get/web.summarize), not a search "
                       "engine; nothing is silently substituted"])
        return None

    # ------------------------------------------------------------------ helpers
    def _matches_any(self, clause: str) -> bool:
        if _URL.search(clause):
            return True
        return any(pattern.match(clause) for pattern in
                   (_WS_SUMMARY_ONLY, _WS_DOCS, _WS_SEARCH, _WS_FIND, _WS_STAT, _WS_READ,
                    _TIME, _CALC, _REMEMBER, _RECALL, _GOAL_AND_TASK, _GOAL, _REMIND, _TASK_ADD,
                    _PROJECTS, _TASKS, _GOALS, _FILES, _RESEARCH))

    def _add(self, prior: list[StepSpec], step: StepSpec) -> list[StepSpec]:
        return [*prior, step]

    def _validated(self, step: StepSpec, notes: list[str]) -> bool:
        try:
            self.registry.validate(step.capability, step.arguments)
            return True
        except Exception as exc:                                  # noqa: BLE001 - reported, not raised
            notes.append(f"{step.capability} rejected the arguments: {exc}")
            return False


def _objective_sentence(text: str) -> str:
    """A short, single-sentence restatement. Rules only; no paraphrase by a model."""
    single = " ".join(text.split())
    if len(single) > 200:
        single = single[:197].rstrip() + "…"
    return single


# --------------------------------------------------------------------------------------
# Model-assisted planner (optional, strictly validated)
# --------------------------------------------------------------------------------------
_PLAN_SCHEMA_HINT = (
    'Return JSON only: {"steps":[{"capability":"<name from tools>","arguments":{...},'
    '"purpose":"...","expected_output":"...","success_criteria":"...","depends_on":[<earlier step numbers>],'
    '"optional":false}],"criteria":[{"kind":"required_steps_verified"}],"notes":["..."]}. '
    'Use only the listed tools and their exact argument names. Do not add tools, fields or prose.'
)


class ModelPlanner:
    """Asks a provider for a plan and validates it. Output is untrusted until validated."""

    def __init__(self, registry, provider) -> None:
        self.registry = registry
        self.provider = provider
        self.calls = 0

    def plan(self, request: str, *, feedback: str = "") -> tuple[PlanSpec | None, str]:
        from .provider import ModelMessage, ModelRequest

        inventory = json.dumps(self.registry.descriptions(), ensure_ascii=False)[:6000]
        system = ("You convert one owner objective into a bounded plan for JARVIS. "
                  + _PLAN_SCHEMA_HINT + " Tools: " + inventory)
        user = f"Objective: {request}"
        if feedback:
            user += f"\nYour previous answer was rejected: {feedback}\nReturn a corrected JSON plan."
        self.calls += 1
        response = self.provider.generate(ModelRequest(system=system,
                                                       messages=(ModelMessage("user", user),),
                                                       max_output_tokens=700, temperature=0.0))
        text = response.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None, "the answer was not JSON"
        steps_raw = payload.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            return None, "the plan had no steps"
        steps: list[StepSpec] = []
        for entry in steps_raw[:MAX_STEPS]:
            if not isinstance(entry, dict):
                return None, "a step was not an object"
            depends = entry.get("depends_on") or []
            if not isinstance(depends, list) or not all(isinstance(d, int) for d in depends):
                return None, "depends_on must be a list of step numbers"
            steps.append(StepSpec(
                capability=str(entry.get("capability") or ""),
                arguments=entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {},
                purpose=str(entry.get("purpose") or ""),
                expected_output=str(entry.get("expected_output") or ""),
                success_criteria=str(entry.get("success_criteria") or ""),
                depends_on=tuple(depends),
                optional=bool(entry.get("optional")),
            ))
        criteria = payload.get("criteria") if isinstance(payload.get("criteria"), list) else []
        criteria = [c for c in criteria if isinstance(c, dict)]
        spec = PlanSpec(objective=_objective_sentence(request), steps=steps,
                        criteria=criteria or default_criteria(steps),
                        notes=[str(n)[:200] for n in (payload.get("notes") or [])][:5],
                        planner="model")
        return spec, ""


# --------------------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------------------
@dataclass
class VerificationReport:
    met: bool
    checks: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"met": self.met, "checks": self.checks, "notes": self.notes}


def verify_plan(plan: PlanRecord, criteria: list[dict[str, Any]], store,
                *, answer: str = "") -> VerificationReport:
    """Judge the objective from recorded evidence: step states and journaled operations.

    A step counts as verified when its operation recorded ``verified = 1``. Steps whose tool
    defines no postcondition are reported as ``unverified`` rather than counted as verified.
    """
    checks: list[dict[str, Any]] = []
    notes: list[str] = []
    required = [s for s in plan.steps if not s.optional]
    succeeded = [s for s in plan.steps if s.status == "succeeded"]
    failed = [s for s in plan.steps if s.status == "failed"]
    verified_rows: dict[int, dict[str, Any]] = {}
    for step in succeeded:
        if step.operation_id is None:
            continue
        record = store.operations.get(step.operation_id)
        if record is not None:
            verified_rows[step.seq] = record
    for step in required:
        if step.status == "succeeded":
            record = verified_rows.get(step.seq)
            if record is None:
                checks.append({"kind": "step", "seq": step.seq, "capability": step.capability,
                               "status": "succeeded_without_evidence", "passed": False})
            elif record.get("verified") is None:
                checks.append({"kind": "step", "seq": step.seq, "capability": step.capability,
                               "status": "succeeded_unverified", "passed": True})
                notes.append(f"step {step.seq} ({step.capability}) ran but its tool defines no postcondition")
            elif bool(record.get("verified")):
                checks.append({"kind": "step", "seq": step.seq, "capability": step.capability,
                               "status": "verified", "passed": True})
            else:
                checks.append({"kind": "step", "seq": step.seq, "capability": step.capability,
                               "status": "verification_failed", "passed": False,
                               "detail": record.get("verification_note") or ""})
        else:
            checks.append({"kind": "step", "seq": step.seq, "capability": step.capability,
                           "status": step.status, "passed": False})

    for criterion in criteria:
        kind = criterion.get("kind")
        if kind == "required_steps_verified":
            passed = bool(required) and all(s.status == "succeeded" for s in required) and not failed
            checks.append({"kind": kind, "passed": passed})
        elif kind == "capability_succeeded":
            capability = criterion.get("capability")
            # A sanctioned substitution satisfies the criterion it replaced; the event log
            # records both capabilities, so this is not a silent downgrade.
            passed = any(s.status == "succeeded" and (s.capability == capability
                                                      or s.replaced_from == capability)
                         for s in succeeded)
            checks.append({"kind": kind, "capability": capability, "passed": passed})
        elif kind == "min_succeeded":
            minimum = int(criterion.get("count") or 1)
            passed = len(succeeded) >= minimum
            checks.append({"kind": kind, "count": minimum, "succeeded": len(succeeded), "passed": passed})
        elif kind == "answer_provided":
            passed = bool(answer.strip())
            checks.append({"kind": kind, "passed": passed})
        elif kind == "no_failed_required_step":
            passed = not any(s.status == "failed" for s in required)
            checks.append({"kind": kind, "passed": passed})
        else:
            checks.append({"kind": str(kind), "passed": False, "detail": "unknown criterion"})
    met = bool(checks) and all(check["passed"] for check in checks)
    return VerificationReport(met=met, checks=checks, notes=notes)


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------
_OBJECTIVE_COLUMNS = (
    "id, request, objective, desired_outcome, constraints_json, success_criteria_json, "
    "memory_ids_json, plan_id, status, outcome, outcome_reason, priority, deadline, "
    "budget_json, verification_json, planner, origin, attempts, replans, revision, "
    "schedule_id, scheduled_for, created_at, updated_at, finished_at"
)
_UNSET = object()

FINISHED_STATUSES = ALL_OUTCOMES      # the status column mirrors the outcome


class ObjectiveStore:
    """Persistence for objectives. Task state only: no long-term memory is written here."""

    def __init__(self, store) -> None:
        self.store = store

    # ---------------------------------------------------------------- writes
    def create(self, *, request: str, objective: str, desired_outcome: str,
               constraints: list[str], success_criteria: list[dict[str, Any]],
               memory_ids: list[int], status: str, outcome: str | None, outcome_reason: str,
               priority: int, deadline: str | None, budget: dict[str, Any],
               planner: str, origin: str, plan_id: str | None = None,
               schedule_id: str | None = None, scheduled_for: str | None = None) -> Objective:
        if outcome is not None and outcome not in {o.value for o in ObjectiveOutcome}:
            raise ValueError(f"Unknown outcome: {outcome}")
        now = to_iso(self.store.now())
        objective_id = "obj_" + secrets.token_hex(8)
        with self.store.write() as db:
            db.execute(
                "INSERT INTO objectives (id, request, objective, desired_outcome, constraints_json, "
                "success_criteria_json, memory_ids_json, plan_id, status, outcome, outcome_reason, "
                "priority, deadline, budget_json, verification_json, planner, origin, attempts, "
                "replans, revision, schedule_id, scheduled_for, created_at, updated_at, finished_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (objective_id, request, objective, desired_outcome, json.dumps(constraints),
                 json.dumps(success_criteria), json.dumps(memory_ids), plan_id, status, outcome,
                 outcome_reason, priority, deadline, json.dumps(budget), "{}", planner, origin,
                 0, 0, 1, schedule_id, scheduled_for, now, now,
                 now if outcome is not None else None))
        return self.get(objective_id)

    def get(self, objective_id: str) -> Objective | None:
        with self.store.read() as db:
            row = db.execute(f"SELECT {_OBJECTIVE_COLUMNS} FROM objectives WHERE id = ?",
                             (objective_id,)).fetchone()
        return None if row is None else self._row(row)

    def list(self, *, status: str | None = None, outcome: str | None = None,
             limit: int = 50) -> list[Objective]:
        sql, args, clauses = f"SELECT {_OBJECTIVE_COLUMNS} FROM objectives", [], []
        if status:
            clauses.append("status = ?")
            args.append(status)
        if outcome:
            clauses.append("outcome = ?")
            args.append(outcome)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 200)))
        with self.store.read() as db:
            rows = db.execute(sql, args).fetchall()
        return [self._row(row) for row in rows]

    def unfinished(self, *, limit: int = 50) -> list[Objective]:
        """Objectives that can still be acted on: never finished, or paused for a human."""
        placeholders = ",".join("?" * len(TERMINAL_OUTCOMES))
        with self.store.read() as db:
            rows = db.execute(
                f"SELECT {_OBJECTIVE_COLUMNS} FROM objectives WHERE status NOT IN ({placeholders}) "
                f"ORDER BY created_at LIMIT ?",
                (*sorted(TERMINAL_OUTCOMES), max(1, min(int(limit), 200)))).fetchall()
        return [self._row(row) for row in rows]

    def set_status(self, objective_id: str, status: str, *, plan_id: str | object = _UNSET,
                   outcome_reason: str | None = None) -> Objective:
        sets, args = ["status = ?", "updated_at = ?"], [status, to_iso(self.store.now())]
        if plan_id is not _UNSET:
            sets.append("plan_id = ?")
            args.append(plan_id)
        if outcome_reason is not None:
            sets.append("outcome_reason = ?")
            args.append(outcome_reason)
        args.append(objective_id)
        with self.store.write() as db:
            db.execute(f"UPDATE objectives SET {', '.join(sets)} WHERE id = ?", args)
        return self.get(objective_id)

    def record_attempt(self, objective_id: str) -> Objective:
        with self.store.write() as db:
            db.execute("UPDATE objectives SET attempts = attempts + 1, updated_at = ? WHERE id = ?",
                       (to_iso(self.store.now()), objective_id))
        return self.get(objective_id)

    def record_replan(self, objective_id: str, *, reason: str = "") -> Objective:
        with self.store.write() as db:
            db.execute(
                "UPDATE objectives SET replans = replans + 1, status = 'running', "
                "outcome_reason = ?, updated_at = ? WHERE id = ?",
                (reason, to_iso(self.store.now()), objective_id))
        return self.get(objective_id)

    def set_plan(self, objective_id: str, plan_id: str, *, revision: int) -> Objective:
        """Point the objective at its current plan revision (history stays in ``plans``)."""
        with self.store.write() as db:
            db.execute("UPDATE objectives SET plan_id = ?, revision = ?, updated_at = ? WHERE id = ?",
                       (plan_id, int(revision), to_iso(self.store.now()), objective_id))
        return self.get(objective_id)

    def set_criteria(self, objective_id: str, criteria: list[dict[str, Any]]) -> Objective:
        """Replace success criteria when a structural revision changes what the plan must do."""
        with self.store.write() as db:
            db.execute(
                "UPDATE objectives SET success_criteria_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(criteria), to_iso(self.store.now()), objective_id))
        return self.get(objective_id)

    def set_budget(self, objective_id: str, budget: dict[str, Any]) -> None:
        with self.store.write() as db:
            db.execute("UPDATE objectives SET budget_json = ?, updated_at = ? WHERE id = ?",
                       (json.dumps(budget), to_iso(self.store.now()), objective_id))

    def finish(self, objective_id: str, *, outcome: str, reason: str,
               verification: dict[str, Any]) -> Objective:
        if outcome not in {o.value for o in ObjectiveOutcome}:
            raise ValueError(f"Unknown outcome: {outcome}")
        now = to_iso(self.store.now())
        with self.store.write() as db:
            db.execute(
                "UPDATE objectives SET outcome = ?, outcome_reason = ?, verification_json = ?, "
                "status = ?, updated_at = ?, finished_at = ? WHERE id = ?",
                (outcome, reason, json.dumps(verification), outcome, now, now, objective_id))
        return self.get(objective_id)

    # ---------------------------------------------------------------- recovery
    def recover_interrupted(self, *, current_runtime: str | None = None) -> list[str]:
        """Mark objectives that were mid-flight when the process stopped.

        They are not failed: work already done is kept and the owner (or ``resume``) can
        continue. The status becomes ``interrupted`` and ``outcome`` stays NULL.
        """
        with self.store.write() as db:
            rows = db.execute(
                "SELECT id FROM objectives WHERE status IN ('running', 'planning')").fetchall()
            for row in rows:
                db.execute("UPDATE objectives SET status = 'interrupted', outcome_reason = ?, "
                           "updated_at = ? WHERE id = ?",
                           ("process stopped before the objective finished",
                            to_iso(self.store.now()), row["id"]))
        if rows:
            self.store.events.append("objective.interrupted", actor="system",
                                     target="objectives", payload={"ids": [r["id"] for r in rows]})
        return [r["id"] for r in rows]

    # ---------------------------------------------------------------- reading
    def events(self, objective_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """The objective's own event trail (planning, replanning, completion)."""
        with self.store.read() as db:
            rows = db.execute(
                "SELECT id, ts, kind, actor, decision, text, payload_json FROM events "
                "WHERE objective_id = ? ORDER BY id LIMIT ?",
                (objective_id, max(1, min(int(limit), 200)))).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    @staticmethod
    def _row(row) -> Objective:
        return Objective(
            id=row["id"], request=row["request"], objective=row["objective"],
            desired_outcome=row["desired_outcome"],
            constraints=json.loads(row["constraints_json"]),
            success_criteria=json.loads(row["success_criteria_json"]),
            memory_ids=json.loads(row["memory_ids_json"]), plan_id=row["plan_id"],
            status=row["status"], outcome=row["outcome"], outcome_reason=row["outcome_reason"],
            priority=row["priority"], deadline=row["deadline"],
            budget=json.loads(row["budget_json"]),
            verification=json.loads(row["verification_json"]), planner=row["planner"],
            origin=row["origin"], attempts=row["attempts"], replans=row["replans"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            finished_at=row["finished_at"],
            revision=row["revision"] if "revision" in row.keys() else 1,
            schedule_id=row["schedule_id"] if "schedule_id" in row.keys() else None,
            scheduled_for=row["scheduled_for"] if "scheduled_for" in row.keys() else None)


# --------------------------------------------------------------------------------------
# Failure diagnosis and alternatives
# --------------------------------------------------------------------------------------
TRANSIENT_ERRORS = frozenset(PlanExecutor.RETRYABLE_ERRORS) | {"temporarily_unavailable"}

#: Alternative capabilities for a failed step: determined by the owner's architecture, not a
#: model. Each entry returns the replacement arguments (or None when the mapping does not fit).
_ALTERNATIVES: dict[str, tuple[str, Callable[[dict[str, Any]], dict[str, Any] | None]]] = {
    "memory.remember": ("memory.save", lambda a: {"key": a.get("key") or "",
                                                  "value": a.get("value") or "",
                                                  "category": a.get("category") or "general"}),
    "memory.save": ("memory.remember", lambda a: {"value": a.get("value") or "",
                                                  "key": a.get("key") or "",
                                                  "category": a.get("category") or "general",
                                                  "kind": "fact", "sensitivity": "normal",
                                                  "correction": False}),
    "task.create": ("task.add", lambda a: {"title": a.get("title") or "", "goal_id": 0}),
    "task.add": ("task.create", lambda a: {"title": a.get("title") or "", "project": "",
                                           "description": "", "priority": 3}),
}


def diagnose(step) -> dict[str, Any]:
    """Classify one failed step into a recoverable category with a plain-language cause."""
    error = (step.error_type or "").strip()
    if step.status == "awaiting_approval" or error == "needs_approval":
        category, recoverable = "needs_approval", False
    elif error.startswith("approval_"):
        # An approval the owner denied is final. Substituting another capability for it would
        # be running the same denied write through the back door.
        category, recoverable = "approval_denied", False
    elif error == "UnknownCapability":
        category, recoverable = "missing_capability", True      # a mapped alternative may exist
    elif error == "InvalidArguments":
        category, recoverable = "invalid_arguments", False
    elif error == "PolicyDenied":
        category, recoverable = "policy_denied", False
    elif error == "dependency_not_met":
        category, recoverable = "blocked_dependency", False
    elif error == "unresolved_reference":
        category, recoverable = "unresolved_reference", False
    elif error == "verification_failed":
        category, recoverable = "verification_failed", True
    elif error in TRANSIENT_ERRORS or any(token in error for token in ("Timeout", "Unavailable",
                                                                      "Connection")):
        category, recoverable = "transient", True
    elif step.status == "failed":
        category, recoverable = "tool_error", False
    else:
        category, recoverable = "unknown", False
    return {"step": step.seq, "capability": step.capability, "category": category,
            "recoverable": recoverable,
            "cause": step.error_message or error or step.status}


# --------------------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------------------
class ObjectiveService:
    """Create, execute, verify and report objectives.

    The service owns no execution logic of its own: steps run through ``PlanExecutor`` ->
    ``ToolRegistry`` -> ``Policy``. What it adds is interpretation, memory context, evidence-
    based verification, bounded replanning, and an honest outcome.
    """

    def __init__(self, store, registry, plans: PlanStore, *, executor: PlanExecutor | None = None,
                 provider=None, settings=None) -> None:
        self.store = store
        self.registry = registry
        self.plans = plans
        self.executor = executor or PlanExecutor(plans, registry)
        self.provider = provider
        self.settings = settings
        self.objectives = ObjectiveStore(store)
        self.leases = LeaseStore(store)

    # ------------------------------------------------------------------ planning
    def _memory_context(self, request: str, limit: int = 5) -> tuple[list[int], list[str]]:
        from .contracts import MemoryQuery

        try:
            hits = self.store.memory.search(MemoryQuery(request, limit=limit))
        except Exception:                     # retrieval must never block the objective
            return [], []
        return [h.id for h in hits], [h.value for h in hits]

    def _context_notes(self, memories: list[str]) -> list[str]:
        if not memories:
            return []
        summary = "; ".join(redact_text(m)[:120] for m in memories[:3])
        return [f"retrieved {len(memories)} memory item(s) as context: {summary}"]

    def create(self, request: str, *, origin: Origin = Origin.USER_COMMAND,
               planning: str = "rules", budget_seconds: float | None = None,
               priority: int = 3, deadline: str | None = None,
               memory_limit: int = 5, schedule_id: str | None = None,
               scheduled_for: str | None = None) -> Objective:
        """Interpret a request and persist an objective, plan and criteria. Does not execute."""
        if not isinstance(request, str) or not request.strip():
            raise ValueError("Objective request cannot be empty")
        request = request.strip()
        if len(request) > MAX_REQUEST_CHARS:
            raise ValueError(f"Objective request is longer than {MAX_REQUEST_CHARS} characters")
        if planning not in {"rules", "model", "auto"}:
            raise ValueError("planning must be 'rules', 'model' or 'auto'")
        if budget_seconds is not None:
            budget = float(budget_seconds)
            if not 1.0 <= budget <= MAX_BUDGET_SECONDS:
                raise ValueError(f"budget_seconds must be within 1-{MAX_BUDGET_SECONDS:g}")
        else:
            budget = DEFAULT_BUDGET_SECONDS
        if not 0 <= int(priority) <= 5:
            raise ValueError("priority must be within 0-5")

        memory_ids, memories = self._memory_context(request, limit=memory_limit)
        planner = RulesPlanner(self.registry, self.store)
        outcome = planner.plan(request)
        used_model = False
        if not outcome.ok and planning in {"model", "auto"} and self.provider is not None:
            spec, why = self._plan_with_model(request, feedback=outcome.reason)
            if spec is not None:
                outcome = PlanningOutcome("plan", plan=spec, notes=outcome.notes + spec.notes)
                used_model = True
            elif planning == "model":
                outcome = PlanningOutcome("blocked", reason=why, notes=outcome.notes)
            else:
                outcome = PlanningOutcome(outcome.kind, question=outcome.question,
                                          reason=outcome.reason, notes=outcome.notes + [why])
        elif planning == "model" and not outcome.ok and self.provider is None:
            outcome = PlanningOutcome("blocked", reason=(
                f"{outcome.reason} (model planning was requested but no provider is configured)"),
                notes=outcome.notes)

        # A model-authored plan is untrusted even when the owner typed the objective: the
        # owner authorised the objective, not the specific writes the model invented.
        origin_value = Origin(origin).value
        if used_model and origin_value == Origin.USER_COMMAND.value:
            origin_value = Origin.MODEL_PROPOSAL.value

        notes = list(outcome.notes) + self._context_notes(memories)
        if used_model:
            notes.append("the plan was proposed by a model and validated against the registry; "
                         "write steps execute only after owner approval")
        objective_sentence = outcome.plan.objective if outcome.plan else _objective_sentence(request)
        desired = outcome.plan.answer if outcome.plan and outcome.plan.answer else \
            f"the objective '{objective_sentence}' is carried out"

        if outcome.kind == "plan" and outcome.plan is not None:
            criteria = outcome.plan.criteria or default_criteria(outcome.plan.steps)
            constraints = list(dict.fromkeys(notes))
            record = self.objectives.create(
                request=request, objective=objective_sentence, desired_outcome=desired,
                constraints=constraints, success_criteria=criteria, memory_ids=memory_ids,
                status="planned", outcome=None, outcome_reason="planned, not yet executed",
                priority=int(priority), deadline=deadline,
                budget={"max_seconds": budget, "used_seconds": 0.0,
                        "max_steps": MAX_OBJECTIVE_STEPS, "replans": MAX_REPLANS},
                planner="model" if used_model else "rules", origin=origin_value,
                schedule_id=schedule_id, scheduled_for=scheduled_for)
            step_dicts = []
            for step in outcome.plan.steps:
                item = step.to_dict()
                item["risk"] = getattr(self.registry.get(step.capability), "risk", "read") \
                    if self.registry.get(step.capability) is not None else "read"
                step_dicts.append(item)
            plan = self.plans.create_plan(title=objective_sentence, steps=step_dicts,
                                          objective_id=record.id)
            record = self.objectives.set_status(record.id, "planned", plan_id=plan.id,
                                                outcome_reason="planned, not yet executed")
            self.store.events.append(
                "objective.created", actor="user" if Origin(origin) == Origin.USER_COMMAND else "model",
                target=record.id, decision="planned", session_id=None, objective_id=record.id,
                text=objective_sentence,
                payload={"plan_id": plan.id, "steps": len(plan.steps), "planner": record.planner,
                         "memory_ids": memory_ids, "criteria": criteria})
            return record

        if outcome.kind == "awaiting_information":
            question = outcome.question or "Which detail should I use?"
            return self.objectives.create(
                request=request, objective=objective_sentence, desired_outcome=desired,
                constraints=list(dict.fromkeys(notes)), success_criteria=[],
                memory_ids=memory_ids, status="awaiting_information",
                outcome=ObjectiveOutcome.AWAITING_INFORMATION.value, outcome_reason=question,
                priority=int(priority), deadline=deadline,
                budget={"max_seconds": budget, "used_seconds": 0.0}, planner="rules",
                origin=origin_value, schedule_id=schedule_id, scheduled_for=scheduled_for)

        # blocked, or a model planning attempt that produced nothing usable
        reason = outcome.reason or "no deterministic plan and no model provider to propose one"
        return self.objectives.create(
            request=request, objective=objective_sentence, desired_outcome=desired,
            constraints=list(dict.fromkeys(notes)), success_criteria=[],
            memory_ids=memory_ids, status="blocked", outcome=ObjectiveOutcome.BLOCKED.value,
            outcome_reason=reason, priority=int(priority), deadline=deadline,
            budget={"max_seconds": budget, "used_seconds": 0.0}, planner="rules",
            origin=origin_value, schedule_id=schedule_id, scheduled_for=scheduled_for)

    def _plan_with_model(self, request: str, *, feedback: str = "") -> tuple[PlanSpec | None, str]:
        """Ask a provider for a plan, validate it, and allow exactly one repair.

        Provider output is untrusted: nothing is executed from it, capabilities and argument
        schemas are checked here, and risk is read from the registry later (never the model).
        """
        planner = ModelPlanner(self.registry, self.provider)
        try:
            spec, why = planner.plan(request, feedback=feedback)
        except Exception as exc:                     # provider failure is not a plan
            return None, f"the model planner failed: {type(exc).__name__}"
        if spec is None:
            return None, why
        try:
            validate_plan_spec(spec, self.registry)
        except PlanInvalid as exc:
            note = f"the first model plan was rejected: {exc}"
            try:
                spec, why = planner.plan(request, feedback=note)
            except Exception as exc2:
                return None, f"{note}; the repair attempt failed: {type(exc2).__name__}"
            if spec is None:
                return None, f"{note}; {why}"
            try:
                validate_plan_spec(spec, self.registry)
            except PlanInvalid as exc2:
                return None, f"{note}; the repair was rejected too: {exc2}"
        return spec, ""

    # ------------------------------------------------------------------ execution
    def submit(self, request: str, **kwargs) -> dict[str, Any]:
        """Create an objective and run it unless planning already settled it."""
        objective = self.create(request, **kwargs)
        if objective.outcome is not None:
            return self.view(objective.id)
        return self.run(objective.id)

    def run(self, objective_id: str, *, should_cancel: Callable[[], bool] | None = None,
            max_seconds: float | None = None) -> dict[str, Any]:
        """Execute an objective to a terminal state, or pause for approval.

        The loop is bounded by the objective's wall-clock budget and ``MAX_REPLANS``. Each
        iteration executes the persisted plan, judges it from evidence, and either finishes or
        performs exactly one sanctioned repair (retry a transient failure or retry a failed
        postcondition, or substitute a mapped alternative capability).
        """
        objective = self._require(objective_id)
        if objective.finished:
            return self.view(objective.id)
        if objective.outcome == ObjectiveOutcome.AWAITING_INFORMATION.value:
            return self.view(objective.id)       # needs a new request, not another run

        # Single writer per objective. Two workers may not execute the same objective
        # concurrently: the loser observes the holder instead of duplicating side effects.
        lease = self.leases.acquire(objective.id, owner=f"objective-runner-{secrets.token_hex(4)}",
                                    ttl=LEASE_TTL_SECONDS)
        if lease is None:
            holder = self.leases.held_by(objective.id)
            self.store.events.append(
                "objective.run_refused", actor="system", target=objective.id,
                objective_id=objective.id,
                payload={"reason": "another worker holds the objective lease",
                         "holder": holder.owner if holder else None})
            return self.view(objective.id)
        try:
            return self._run_leased(objective, lease, should_cancel=should_cancel,
                                    max_seconds=max_seconds)
        finally:
            try:
                self.leases.release(lease)
            except LeaseError:                    # already expired or taken over: nothing to free
                pass

    def _run_leased(self, objective: Objective, lease, *, should_cancel: Callable[[], bool] | None,
                    max_seconds: float | None) -> dict[str, Any]:
        """The execution loop, fenced by ``lease``. Every write below is lease-guarded."""
        if objective.plan_id is None:
            return self._finish(objective, ObjectiveOutcome.FAILED,
                                "no plan was persisted for this objective", None, None)

        budget_seconds = float(objective.budget.get("max_seconds", DEFAULT_BUDGET_SECONDS))
        if max_seconds is not None:
            budget_seconds = min(budget_seconds, float(max_seconds))
        started = time.monotonic()
        narrative: list[str] = []
        objective = self.objectives.record_attempt(objective.id)

        while True:
            if not self.leases.renew(lease):
                # Fencing: another worker owns this objective now. Its writes are authoritative,
                # so this worker stops without finishing the objective or touching the plan.
                self.store.events.append(
                    "objective.lease_lost", actor="system", target=objective.id,
                    objective_id=objective.id,
                    payload={"reason": "lease expired or was taken over; abandoning this run",
                             "owner": lease.owner, "token": lease.token})
                return self.view(objective.id)
            elapsed = time.monotonic() - started
            remaining = budget_seconds - elapsed
            plan = self.plans.get_plan(objective.plan_id)
            verification = verify_plan(plan, objective.success_criteria, self.store)
            if should_cancel is not None and should_cancel():
                self.plans.cancel(plan.id)
                return self._finish(objective, ObjectiveOutcome.CANCELLED,
                                    "cancelled before the next step ran", plan, verification,
                                    narrative, used=elapsed)
            if remaining <= 0:
                return self._finish(objective, ObjectiveOutcome.FAILED,
                                    f"time budget of {budget_seconds:g}s was exhausted",
                                    plan, verification, narrative, used=elapsed)

            objective = self.objectives.set_status(objective.id, "running")
            plan = self.executor.execute(plan.id, origin=Origin(objective.origin),
                                         should_cancel=should_cancel, max_seconds=remaining)
            verification = verify_plan(plan, objective.success_criteria, self.store)

            if plan.status == "cancelled":
                return self._finish(objective, ObjectiveOutcome.CANCELLED,
                                    "the plan was cancelled", plan, verification, narrative,
                                    used=time.monotonic() - started)
            if plan.status == "awaiting_approval":
                waiting = next((s for s in plan.steps if s.status == "awaiting_approval"), None)
                reason = (f"step {waiting.seq} ({waiting.capability}) proposed operation "
                          f"{waiting.operation_id}; it needs owner approval before the objective "
                          f"can continue" if waiting else "a step needs owner approval")
                return self._finish(objective, ObjectiveOutcome.NEEDS_APPROVAL, reason, plan,
                                    verification, narrative, used=time.monotonic() - started)

            required = [s for s in plan.steps if not s.optional]
            required_done = bool(required) and all(s.status == "succeeded" for s in required)
            optional_failed = any(s.status in {"failed", "skipped"} for s in plan.steps
                                  if s.optional)
            if required_done and verification.met:
                outcome = (ObjectiveOutcome.PARTIALLY_COMPLETED if optional_failed
                           else ObjectiveOutcome.COMPLETED)
                reason = ("all required steps verified" if not optional_failed
                          else "required steps verified; an optional step did not run")
                return self._finish(objective, outcome, reason, plan, verification, narrative,
                                    used=time.monotonic() - started)

            verdict = self._next_action(objective, plan, verification)
            action = verdict["action"]
            if action in {"stop", "blocked"}:
                # Mission 003: before giving up, try a *structural* revision — a genuinely
                # different decomposition that keeps verified work. Denials are never re-planned.
                revised = self._structural_replan(objective, plan, verdict)
                if revised is not None:
                    self.store.events.append(
                        "objective.replanned_structurally", actor="system", target=objective.id,
                        decision="replanned", objective_id=objective.id,
                        text=(f"plan revision {revised.revision}: {len(revised.steps)} step(s), "
                              f"replacing {plan.id}"),
                        payload={"from_plan": plan.id, "to_plan": revised.id,
                                 "revision": revised.revision,
                                 "diagnosis": verdict.get("diagnosis"),
                                 "carried_steps": [s.seq for s in revised.steps
                                                   if s.status == "succeeded"],
                                 "dropped": [s.capability for s in plan.steps
                                             if s.status in {"failed", "skipped"}]})
                    narrative.append(
                        f"structural replan (revision {revised.revision}) after: {verdict['reason']}")
                    objective = self.objectives.record_replan(
                        objective.id, reason=f"structural revision {revised.revision}: "
                                             f"{verdict['reason']}")
                    objective = self.objectives.set_plan(objective.id, revised.id,
                                                         revision=revised.revision)
                    continue
            if action == "replan":
                step = next((s for s in plan.steps if s.seq == verdict["step"]), None)
                if step is not None and verdict.get("alternative"):
                    replacement = verdict["alternative"]
                    self.plans.replace_step(
                        step.id, capability=replacement["capability"],
                        arguments=replacement["arguments"],
                        reason=f"alternative for {step.capability}")
                    self.store.events.append(
                        "objective.step_substituted", actor="system", target=objective.id,
                        decision="replanned", objective_id=objective.id,
                        payload={"from": step.capability, "to": replacement["capability"],
                                 "seq": step.seq})
                elif step is not None and verdict.get("reset"):
                    self.plans.reset_step_for_retry(step.id)
                self.plans.reopen(plan.id)
                objective = self.objectives.record_replan(objective.id, reason=verdict["reason"])
                narrative.append(verdict["reason"])
                self.store.events.append(
                    "objective.replanned", actor="system", target=objective.id,
                    decision="replanned", objective_id=objective.id, text=verdict["reason"],
                    payload={"diagnosis": verdict.get("diagnosis"), "replans": objective.replans})
                continue
            if action == "blocked":
                return self._finish(objective, ObjectiveOutcome.BLOCKED, verdict["reason"], plan,
                                    verification, narrative, used=time.monotonic() - started)
            # "Partially completed" needs verified evidence, not merely a handler that returned:
            # a step whose postcondition failed does not count as achieved work.
            achieved = any(check.get("kind") == "step" and check.get("passed")
                           for check in verification.checks)
            outcome = (ObjectiveOutcome.PARTIALLY_COMPLETED if achieved
                       else ObjectiveOutcome.FAILED)
            return self._finish(objective, outcome, verdict["reason"], plan, verification,
                                narrative, used=time.monotonic() - started)

    # ------------------------------------------------------------------ diagnosis
    def _next_action(self, objective: Objective, plan: PlanRecord,
                     verification: VerificationReport) -> dict[str, Any]:
        """Decide one bounded repair, an honest block, or a stop. No side effects here."""
        if objective.replans >= MAX_REPLANS:
            return {"action": "stop",
                    "reason": f"replan limit ({MAX_REPLANS}) reached; stopping rather than retrying"}
        failed = [s for s in plan.steps if s.status in {"failed", "skipped"} and not s.optional]
        if failed:
            step = failed[0]
            if step.status == "skipped":
                return {"action": "stop", "step": step.seq,
                        "reason": f"step {step.seq} ({step.capability}) could not run: "
                                  f"{step.error_message or step.error_type}"}
            diagnosis = diagnose(step)
            # Each repair family is allowed once; repetition is a stop, not another retry.
            if diagnosis["category"] == "transient" and objective.replans == 0:
                return {"action": "replan", "step": step.seq, "reset": True,
                        "diagnosis": diagnosis,
                        "reason": f"step {step.seq} ({step.capability}) failed transiently "
                                  f"({diagnosis['cause']}); retrying once"}
            if diagnosis["category"] == "verification_failed" and objective.replans == 0:
                return {"action": "replan", "step": step.seq, "reset": True,
                        "diagnosis": diagnosis,
                        "reason": f"step {step.seq} ({step.capability}) did not satisfy its "
                                  f"postcondition; retrying once"}
            if diagnosis["category"] in {"policy_denied", "approval_denied", "invalid_arguments",
                                         "unresolved_reference", "blocked_dependency"}:
                return {"action": "stop", "step": step.seq, "diagnosis": diagnosis,
                        "reason": f"step {step.seq} ({step.capability}) cannot be repaired "
                                  f"automatically: {diagnosis['cause']}"}
            alternative = (self._alternative(step)
                           if diagnosis["category"] in {"tool_error", "missing_capability"} else None)
            if alternative is not None:
                diagnosis["alternative"] = alternative["capability"]
                return {"action": "replan", "step": step.seq,
                        "diagnosis": diagnosis, "alternative": alternative,
                        "reason": f"step {step.seq} ({step.capability}) failed "
                                  f"({diagnosis['cause']}); substituting "
                                  f"{alternative['capability']}, a capability that exists"}
            if diagnosis["category"] == "missing_capability":
                return {"action": "blocked", "step": step.seq, "diagnosis": diagnosis,
                        "reason": f"step {step.seq} needs '{step.capability}', which the registry "
                                  f"does not provide, and no mapped alternative exists"}
            return {"action": "stop", "step": step.seq, "diagnosis": diagnosis,
                    "reason": f"step {step.seq} ({step.capability}) failed: {diagnosis['cause']}"}

        # No failed step but the objective's criteria are unmet: retry a step whose tool
        # reported a failed postcondition, if any; otherwise report honestly.
        for check in verification.checks:
            if (check.get("kind") == "step" and check.get("status") == "verification_failed"
                    and objective.replans == 0):
                return {"action": "replan", "step": check.get("seq"), "reset": True,
                        "diagnosis": {"category": "verification_failed",
                                      "cause": check.get("detail") or "postcondition failed"},
                        "reason": f"step {check.get('seq')} postcondition failed; retrying once"}
        if not verification.checks:
            return {"action": "stop", "reason": "no success criteria were defined"}
        unmet = [c for c in verification.checks if not c.get("passed")]
        return {"action": "stop",
                "reason": "objective criteria are not met: " +
                          ", ".join(str(c.get("kind")) for c in unmet[:4])}

    # ------------------------------------------------------------------ structural replanning
    def _structural_replan(self, objective: Objective, plan: PlanRecord,
                           verdict: dict[str, Any]) -> PlanRecord | None:
        """Re-decompose an objective into a revised plan, preserving verified work.

        This is a *structural* revision, not a retry: the objective is planned again from the
        request against the live registry, steps that already succeeded are carried over with
        their operation evidence, and material changes are required for the revision to count.
        Refused because a revision cannot be justified, or because the failure was a policy or
        approval decision — re-planning must never become a way around a denial.
        """
        diagnosis = verdict.get("diagnosis") or {}
        category = diagnosis.get("category")
        if category in {"policy_denied", "approval_denied"}:
            return None
        if objective.revision > MAX_PLAN_REVISIONS or objective.replans >= MAX_REPLANS:
            return None
        completed = [s for s in plan.steps if s.status == "succeeded"]
        done_positions: dict[tuple[str, str], int] = {}
        for position, step in enumerate(completed, start=1):
            done_positions.setdefault((step.capability, _canonical(step.arguments)), position)

        planner = RulesPlanner(self.registry, self.store)
        outcome = planner.plan(objective.request)
        if not outcome.ok or outcome.plan is None:
            return None                                   # nothing to re-decompose into
        specs = list(outcome.plan.steps)

        # Map the new decomposition onto positions in the revised plan: an already-succeeded
        # step keeps its work, everything else becomes a fresh pending step.
        positions: dict[int, int | None] = {}
        pending: list[tuple[PlanStepRecord, int]] = []
        next_position = len(completed) + 1
        for index, spec in enumerate(specs, start=1):
            signature = (spec.capability, _canonical(spec.arguments))
            if signature in done_positions:
                positions[index] = done_positions[signature]
                continue
            dependencies = list(spec.depends_on or [])
            if any(positions.get(dependency) is None for dependency in dependencies):
                positions[index] = None                   # depends on work that cannot be planned
                continue
            arguments, broken = _remap_references(spec.arguments, positions)
            if broken:
                positions[index] = None                   # references work that will not exist
                continue
            positions[index] = next_position
            pending.append(({
                "capability": spec.capability, "arguments": arguments, "purpose": spec.purpose,
                "expected_output": spec.expected_output, "success_criteria": spec.success_criteria,
                "depends_on": [positions[dependency] for dependency in dependencies],
                "optional": spec.optional}, next_position))
            next_position += 1

        if not pending:
            return None                                   # no new work: a retry in disguise

        # Materiality: the revision must change what will actually run.
        old_pending = {(s.capability, _canonical(s.arguments))
                       for s in plan.steps if s.status in {"failed", "skipped", "pending"}
                       and not s.optional}
        new_pending = {(step["capability"], _canonical(step["arguments"])) for step, _ in pending}
        if new_pending <= old_pending:
            return None

        carried = [{
            "capability": step.capability, "arguments": step.arguments, "purpose": step.purpose,
            "expected_output": step.expected_output,
            "success_criteria": step.success_criteria, "depends_on": [],
            "optional": step.optional} for step in completed]
        steps = carried + [step for step, _ in sorted(pending, key=lambda item: item[1])]
        try:
            revised = self.plans.create_plan(
                title=plan.title, steps=steps, goal_id=plan.goal_id,
                objective_id=objective.id, revision=objective.revision + 1,
                replaces_plan_id=plan.id)
        except Exception:                                 # pragma: no cover - defensive
            return None
        for position, step in enumerate(completed):
            revised = self.plans.get_plan(revised.id)
            self.plans.mark_step_succeeded(
                revised.steps[position].id, operation_id=step.operation_id,
                result_summary=step.result_summary or "carried over from the previous plan")
        # The revised plan defines different required work, so its criteria are re-derived from
        # the plan itself (never from prose). The change is recorded for audit.
        revised = self.plans.get_plan(revised.id)
        criteria = default_criteria([_spec_from_step(step) for step in revised.steps])
        self.objectives.set_criteria(objective.id, criteria)
        self.store.events.append(
            "objective.criteria_revised", actor="system", target=objective.id,
            decision="replanned", objective_id=objective.id,
            text="success criteria re-derived from the revised plan",
            payload={"from": [c.get("capability") for c in objective.success_criteria],
                     "to": [c.get("capability") for c in criteria],
                     "from_plan": plan.id, "to_plan": revised.id})
        self.plans.supersede(plan.id, revised.id)
        return self.plans.get_plan(revised.id)

    def _alternative(self, step) -> dict[str, Any] | None:
        """A registered capability that can stand in for a failed one, with translated args."""
        mapping = _ALTERNATIVES.get(step.capability)
        if mapping is None:
            return None
        capability, convert = mapping
        if self.registry.get(capability) is None:
            return None
        arguments = convert(dict(step.arguments))
        if arguments is None:
            return None
        try:
            self.registry.validate(capability, arguments)
        except Exception:
            return None
        return {"capability": capability, "arguments": arguments}

    # ------------------------------------------------------------------ reporting
    def _finish(self, objective: Objective, outcome: ObjectiveOutcome, reason: str,
                plan: PlanRecord | None, verification: VerificationReport | dict[str, Any] | None,
                narrative: list[str] | None = None, *, used: float | None = None) -> dict[str, Any]:
        report = (verification.to_dict() if isinstance(verification, VerificationReport)
                  else (verification or {"met": False, "checks": [], "notes": []}))
        if plan is not None and plan.status == "active":
            # A terminal objective must not leave an active plan behind.
            if outcome in {ObjectiveOutcome.COMPLETED}:
                self.plans.complete(plan.id)
            elif outcome in {ObjectiveOutcome.CANCELLED}:
                self.plans.cancel(plan.id)
            else:
                self.plans.fail(plan.id, reason)
        if used is not None:
            budget = dict(objective.budget)
            budget["used_seconds"] = round(float(used), 3)
            self.objectives.set_budget(objective.id, budget)
        record = self.objectives.finish(objective.id, outcome=outcome.value, reason=reason,
                                        verification=report)
        if narrative:
            report["narrative"] = list(narrative)
        self.store.events.append(
            "objective.finished", actor="system", target=record.id, decision=outcome.value,
            objective_id=record.id, text=reason,
            payload={"outcome": outcome.value, "plan_id": record.plan_id,
                     "replans": record.replans, "attempts": record.attempts,
                     "criteria_met": report.get("met", False)})
        return self.view(record.id)

    def view(self, objective_id: str) -> dict[str, Any]:
        """The externally visible objective: state, plan, evidence, answer and event trail."""
        objective = self._require(objective_id)
        plan = self.plans.get_plan(objective.plan_id) if objective.plan_id else None
        events = self.objectives.events(objective.id)
        return {
            "objective": objective.to_dict(),
            "plan": plan.to_dict() if plan else None,
            "verification": objective.verification,
            "answer": self._compose_answer(objective, plan),
            "events": events,
        }

    def list(self, *, status: str | None = None, outcome: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]:
        return [o.to_dict() for o in self.objectives.list(status=status, outcome=outcome, limit=limit)]

    def cancel(self, objective_id: str) -> dict[str, Any]:
        objective = self._require(objective_id)
        if objective.finished:
            return self.view(objective.id)
        plan = self.plans.get_plan(objective.plan_id) if objective.plan_id else None
        if plan is not None and plan.status in {"proposed", "active", "awaiting_approval"}:
            self.plans.cancel(plan.id)
            plan = self.plans.get_plan(plan.id)
        result = self._finish(objective, ObjectiveOutcome.CANCELLED,
                              "cancelled by the owner", plan,
                              {"met": False, "checks": [], "notes": ["cancelled by the owner"]})
        return result

    def resume(self, objective_id: str, **kwargs) -> dict[str, Any]:
        """Continue a paused objective: after approval, after a restart, or after information."""
        objective = self._require(objective_id)
        if objective.finished:
            return self.view(objective.id)
        return self.run(objective.id, **kwargs)

    def resume_after_restart(self) -> dict[str, Any]:
        """Record what an interrupted process left behind and list what can be resumed.

        Nothing is executed here: a restarted JARVIS reports unfinished objectives (and any
        operation still awaiting approval) instead of silently resuming writes.
        """
        interrupted = self.objectives.recover_interrupted()
        pending = [o.to_dict() for o in self.objectives.unfinished()]
        return {"interrupted": interrupted, "unfinished": pending,
                "awaiting_approval_operations": self.store.operations.list(status="proposed", limit=50)}

    # ------------------------------------------------------------------ helpers
    def _require(self, objective_id: str) -> Objective:
        objective = self.objectives.get(objective_id)
        if objective is None:
            raise LookupError(f"Objective not found: {objective_id}")
        return objective

    def _compose_answer(self, objective: Objective, plan: PlanRecord | None) -> str:
        """A deterministic report of what happened. Never generated by a model."""
        if objective.outcome == ObjectiveOutcome.AWAITING_INFORMATION.value:
            return objective.outcome_reason or "More information is needed."
        if objective.outcome == ObjectiveOutcome.BLOCKED.value:
            return objective.outcome_reason or "This objective is blocked."
        if plan is None:
            return objective.outcome_reason
        headline = {
            ObjectiveOutcome.COMPLETED.value: "Completed.",
            ObjectiveOutcome.PARTIALLY_COMPLETED.value: "Partially completed.",
            ObjectiveOutcome.FAILED.value: "Did not complete.",
            ObjectiveOutcome.NEEDS_APPROVAL.value: "Paused for approval.",
            ObjectiveOutcome.CANCELLED.value: "Cancelled.",
        }.get(objective.outcome or "", "In progress.")
        lines = [headline]
        findings: list[tuple[str, str]] = []
        for step in plan.steps:
            summary = step.result_summary or step.error_message or step.status
            lines.append(f"- {step.capability}: {summary}")
            if step.status == "succeeded" and step.operation_id is not None:
                record = self.store.operations.get(step.operation_id)
                journaled = (record or {}).get("result") or {}
                if not isinstance(journaled, dict):
                    continue
                if step.capability in {"workspace.summarize", "web.summarize"} \
                        and journaled.get("summary"):
                    sources = ", ".join(str(source) for source in (journaled.get("sources") or [])[:3])
                    findings.append((sources or "unknown source", str(journaled["summary"])[:600]))
                elif step.capability in {"workspace.search", "workspace.find"}:
                    hits = [str(path) for path in (journaled.get("paths") or [])[:5] if path]
                    if hits:
                        findings.append((f"{len(hits)} match(es)", ", ".join(hits)))
        for sources, summary in findings:
            lines.append(untrusted_block(sources, summary))
        checks = (objective.verification or {}).get("checks") or []
        if checks:
            passed = sum(1 for c in checks if c.get("passed"))
            lines.append(f"Verification: {passed}/{len(checks)} checks passed"
                         + ("" if (objective.verification or {}).get("met") else
                            " (objective not verified)"))
        for note in (objective.verification or {}).get("notes") or []:
            lines.append(f"Note: {note}")
        if objective.outcome not in {ObjectiveOutcome.COMPLETED.value,
                                     ObjectiveOutcome.PARTIALLY_COMPLETED.value}:
            lines.append(f"Reason: {objective.outcome_reason}")
        text = "\n".join(lines)
        return text if len(text) <= 1200 else text[:1197] + "..."
