"""Deterministic interpretation of user input into intents (interpreter "rules-v1").

Design choice (ADR-0004): the first interpreter is rule-based so that JARVIS is
useful offline, behaves reproducibly, and can be tested exactly. A language model
may later be added as an *alternative* interpreter evaluated against the same
fixtures; it would not replace the kernel's control of state and permissions.

Every result is an :class:`Interpretation` with a confidence label:
- HIGH for explicit command forms ("remember that ...", "goal: ...", "done with task 3"),
- MEDIUM for heuristics (a question mark implies a recall request),
- LOW for the CHAT fallback.
Write intents are only produced by HIGH-confidence rules.

Known limitations: English only; no coreference ("mark it done"); keyed facts are
recognised only for simple "<subject> is/are <value>" sentences.
"""
from __future__ import annotations

import re
from typing import Any

from .contracts import Confidence, Intent, Interpretation
from .util import normalise_key

_FLAGS = re.IGNORECASE | re.DOTALL


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, _FLAGS)


_TASK_REF = r"(?:task|step)\s*#?\s*(?P<id>\d+)"
_OP_REF = r"(?:op(?:eration)?\s*)?#?\s*(?P<id>\d+)"

RULES: tuple[tuple[str, Intent, re.Pattern[str]], ...] = (
    ("approve", Intent.APPROVE, _rx(rf"^(?:approve|confirm|yes,?\s+(?:do|run)\s+(?:it|that)?\s*){_OP_REF}\s*[.!]?$")),
    ("deny", Intent.DENY, _rx(rf"^(?:deny|reject|decline|cancel)\s+{_OP_REF}\s*[.!]?$")),
    ("forget_id", Intent.FORGET, _rx(r"^(?:please\s+)?(?:forget|delete|erase)\s+(?:memory\s*#?\s*|#\s*)(?P<id>\d+)\s*[.!]?$")),
    ("forget_text", Intent.FORGET, _rx(r"^(?:please\s+)?(?:forget|delete|erase)\s+(?:(?:that|about|what i said about|the fact that)\s+|(?=my\s))(?P<body>.+?)[.!]?$")),
    ("correct_id", Intent.CORRECT, _rx(r"^(?:correct|fix|update)\s+(?:memory\s*)?#\s*(?P<id>\d+)\s*(?:to|:|=|->)\s*(?P<body>.+)$")),
    ("correct_marker", Intent.CORRECT, _rx(r"^(?:actually|correction|to correct (?:that|myself)|no wait|sorry,? i meant|i was wrong)\s*[:,\-]?\s*(?P<body>.+)$")),
    ("goal", Intent.GOAL_CREATE, _rx(r"^(?:new goal|goal|my goal is(?: to)?|objective)\s*[:\-]\s*(?P<title>.+?)(?:\s*(?:;|\.|\n)\s*steps?\s*[:\-]\s*(?P<steps>.+))?$")),
    ("goal_plain", Intent.GOAL_CREATE, _rx(r"^my goal is to\s+(?P<title>.+?)(?:\s*(?:;|\.|\n)\s*steps?\s*[:\-]\s*(?P<steps>.+))?$")),
    ("task_done", Intent.TASK_DONE, _rx(rf"^(?:i(?:'ve| have)?\s+)?(?:done with|finished|completed|complete|mark(?:ed)?)\s+{_TASK_REF}(?:\s+(?:as\s+)?(?:done|complete|finished))?\s*[.!]?$")),
    ("task_done2", Intent.TASK_DONE, _rx(rf"^{_TASK_REF}\s+(?:is\s+)?(?:done|finished|complete|completed)\s*[.!]?$")),
    ("task_block", Intent.TASK_BLOCK, _rx(rf"^{_TASK_REF}\s+is\s+blocked(?:\s+(?:by|because(?: of)?|on|:)\s*(?P<reason>.+?))?\s*[.!]?$")),
    ("task_block2", Intent.TASK_BLOCK, _rx(rf"^(?:i'?m\s+)?blocked\s+on\s+{_TASK_REF}(?:\s*(?:by|because(?: of)?|:)\s*(?P<reason>.+?))?\s*[.!]?$")),
    ("task_start", Intent.TASK_START, _rx(rf"^(?:start(?:ing)?|begin(?:ning)?|work(?:ing)? on|resume)\s+{_TASK_REF}\s*[.!]?$")),
    ("task_add", Intent.TASK_ADD, _rx(r"^(?:add (?:a )?task|new task|todo|to-do|task)\s*[:\-]?\s+(?P<title>.+?)(?:\s+(?:to|for)\s+goal\s*#?\s*(?P<goal>\d+))?\s*$")),
    ("idea", Intent.IDEA, _rx(r"^(?:idea|new idea|i have an idea|here'?s an idea|thought)\s*[:\-,]?\s*(?P<body>.+)$")),
    ("preference_style", Intent.PREFERENCE, _rx(r"^(?:please\s+)?(?:be|keep (?:it|answers|responses))\s+(?:more\s+|a bit more\s+)?(?P<style>concise|brief|short|detailed|thorough)\s*(?:please)?[.!]?$")),
    ("preference_name", Intent.PREFERENCE, _rx(r"^(?:please\s+)?call me\s+(?P<name>[\w .'-]{1,40}?)\s*[.!]?$")),
    ("preference_briefing", Intent.PREFERENCE, _rx(r"^(?:please\s+)?(?P<onoff>stop|disable|turn off|enable|turn on|start)\s+(?:the\s+)?(?:welcome[- ]back\s+|session\s+)?briefings?\s*[.!]?$")),
    ("remember", Intent.REMEMBER, _rx(r"^(?:please\s+)?(?:remember|note|keep in mind|don'?t forget|save this|make a note)\s*(?:that|this|:|,)?\s*[:,\-]?\s*(?P<body>.+)$")),
    ("resume", Intent.RESUME, _rx(r"^(?:where (?:were|was) (?:we|i)|where did (?:we|i) leave off|what'?s next|what is next|catch me up|resume|status|what am i working on|what should i (?:do|work on) next|continue|let'?s continue|pick up where we left off)\b.*$")),
    ("capabilities", Intent.CAPABILITIES, _rx(r"^(?:what can you do|what are your capabilities|capabilities|help|what do you know how to do)\s*\??$")),
    ("time", Intent.TIME, _rx(r"^(?:what(?:'s| is) the time|what time is it|current time|what(?:'s| is) (?:today'?s )?date|what day is (?:it|today))\s*\??$")),
    ("calculate", Intent.CALCULATE, _rx(r"^(?:calculate|compute|evaluate|what(?:'s| is)|how much is)\s+(?P<expr>[-+*/%^().\d\s]+?)\s*\??$")),
    ("calculate_bare", Intent.CALCULATE, _rx(r"^(?P<expr>[-+(]*\d[\d\s.]*(?:[-+*/%^][\s(]*-?\d[\d\s.)]*)+)\s*=?\s*\??$")),
    ("recall_explicit", Intent.RECALL, _rx(r"^(?:what do you (?:know|remember) about|what did i (?:say|tell you|mention) about|recall|do you remember|tell me about|search (?:memory )?for)\s+(?P<query>.+?)\s*\??$")),
)

_QUESTION = _rx(r"^(?:what|who|when|where|which|how|why|is|are|do|does|did|can|could|should|would|will)\b.*\?\s*$")
_KEYED = re.compile(r"^(?:(?P<owner>my|our)\s+)?(?:the\s+)?(?P<key>[\w][\w\s'\-]{1,60}?)\s+(?:is|are|was|were|=|equals)\s+(?P<value>.+)$",
                    re.IGNORECASE | re.DOTALL)
_STEP_SPLIT = re.compile(r"\s*(?:;|\n|,\s*then\s+|\bthen\b|\d+[.)]\s+)\s*", re.IGNORECASE)


def parse_keyed(body: str) -> dict[str, Any]:
    """Recognise simple keyed statements ("my favourite colour is blue")."""
    text = body.strip().rstrip(".!")
    match = _KEYED.match(text)
    if not match:
        return {}
    key = normalise_key(match.group("key"))
    if not key or len(key.split()) > 6:
        return {}
    category = "personal" if match.group("owner") else "general"
    return {"key": key, "fact_value": match.group("value").strip(), "category": category}


def split_steps(text: str | None) -> list[str]:
    if not text:
        return []
    parts = [p.strip(" .,-") for p in _STEP_SPLIT.split(text)]
    if len(parts) == 1 and "," in text:
        parts = [p.strip(" .-") for p in text.split(",")]
    return [p for p in parts if p][:25]


def interpret(text: str) -> Interpretation:
    stripped = text.strip()
    for rule_name, intent, pattern in RULES:
        match = pattern.match(stripped)
        if not match:
            continue
        slots = {k: v.strip() if isinstance(v, str) else v for k, v in match.groupdict().items() if v is not None}
        if intent in {Intent.APPROVE, Intent.DENY, Intent.TASK_DONE, Intent.TASK_BLOCK, Intent.TASK_START} \
                or rule_name in {"forget_id", "correct_id"}:
            slots["id"] = int(slots["id"])
        if intent == Intent.TASK_ADD and "goal" in slots:
            slots["goal"] = int(slots["goal"])
        if intent in {Intent.REMEMBER, Intent.CORRECT} and "body" in slots:
            slots.update(parse_keyed(slots["body"]))
        if intent == Intent.GOAL_CREATE:
            slots["steps"] = split_steps(slots.get("steps"))
            slots["title"] = slots["title"].strip().rstrip(".")
        if intent == Intent.CALCULATE:
            slots["expr_display"] = " ".join(slots["expr"].split())
            slots["expr"] = slots["expr"].replace("^", "**").strip()
        if intent == Intent.RESUME and len(stripped.split()) > 12:
            break  # a long message merely starting with "status..." is not a resume request
        return Interpretation(intent=intent, confidence=Confidence.HIGH, slots=slots, rule=rule_name)
    if _QUESTION.match(stripped):
        return Interpretation(intent=Intent.RECALL, confidence=Confidence.MEDIUM, slots={"query": stripped},
                              rule="question_heuristic", alternatives=(Intent.CHAT.value,))
    return Interpretation(intent=Intent.CHAT, confidence=Confidence.LOW, slots={}, rule="fallback")
