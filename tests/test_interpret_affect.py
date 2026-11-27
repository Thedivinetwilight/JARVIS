"""Deterministic interpreter and emotional appraisal."""
from __future__ import annotations

import pytest

from jarvis.affect import acknowledgement, appraise, check_in, wellbeing_guard
from jarvis.contracts import Intent
from jarvis.interpret import interpret


@pytest.mark.parametrize(("text", "intent", "slots"), [
    ("remember that my favourite colour is blue", Intent.REMEMBER, {"key": "favourite colour", "category": "personal"}),
    ("actually, my favourite colour is green", Intent.CORRECT, {"key": "favourite colour"}),
    ("correct #3 to the controller runs at 48V", Intent.CORRECT, {"id": 3}),
    ("forget #4", Intent.FORGET, {"id": 4}),
    # Pre-release audit P7: these requests used to fall through to chat, so nothing was forgotten
    # and the request itself was stored as a recallable conversational episode.
    ("forget my old address", Intent.FORGET, {"body": "my old address"}),
    ("delete memory 12", Intent.FORGET, {"id": 12}),
    ("please erase memory #3.", Intent.FORGET, {"id": 3}),
    ("goal: build it; steps: a; b; c", Intent.GOAL_CREATE, {"title": "build it", "steps": ["a", "b", "c"]}),
    ("done with task 2", Intent.TASK_DONE, {"id": 2}),
    ("task 5 is blocked by missing parts", Intent.TASK_BLOCK, {"id": 5, "reason": "missing parts"}),
    ("add task order relays to goal 1", Intent.TASK_ADD, {"title": "order relays", "goal": 1}),
    ("idea: water by soil moisture", Intent.IDEA, {"body": "water by soil moisture"}),
    ("please be more concise", Intent.PREFERENCE, {"style": "concise"}),
    ("where were we?", Intent.RESUME, {}),
    ("what is 2^10?", Intent.CALCULATE, {"expr": "2**10", "expr_display": "2^10"}),
    ("approve 7", Intent.APPROVE, {"id": 7}),
    ("do you remember my favourite colour", Intent.RECALL, {"query": "my favourite colour"}),
])
def test_interpreter_intents(text, intent, slots):
    result = interpret(text)
    assert result.intent == intent and result.confidence.value == "high"
    for key, value in slots.items():
        assert result.slots[key] == value


@pytest.mark.parametrize(("text", "intent"), [
    ("I always forget my keys", Intent.CHAT),            # statements about forgetting are not commands
    ("forgetting my keys again", Intent.CHAT),
    ("don't forget my meds at 9", Intent.REMEMBER),
])
def test_forget_commands_do_not_capture_statements(text, intent):
    assert interpret(text).intent == intent


def test_questions_and_fallback():
    question = interpret("what voltage does the pump use?")
    assert question.intent == Intent.RECALL and question.confidence.value == "medium"
    chat = interpret("I'm thinking about the demo")
    assert chat.intent == Intent.CHAT and chat.confidence.value == "low"
    long_status = interpret("status of the project is that the build passes but the docs are out of date again")
    assert long_status.intent != Intent.RESUME


def test_stated_inferred_absent_and_mixed():
    stated = appraise("I'm really stressed about the demo tomorrow")
    assert [(s.label, s.basis) for s in stated.stated] == [("stress", "stated")] and stated.style.tone == "supportive"
    assert "stressed" in acknowledgement(stated)
    inferred = appraise("ugh, the build is still broken again!!")
    assert not inferred.stated and {p.label for p in inferred.possible} >= {"frustration"}
    assert all(p.confidence.value in {"low", "medium"} for p in inferred.possible)
    assert check_in(inferred) and "misread" in check_in(inferred)
    absent = appraise("I'm not stressed, just curious how it works")
    assert [(s.label, s.basis) for s in absent.stated] == [("stress", "stated_absent")] and not absent.possible
    mixed = appraise("I'm excited but honestly a bit worried")
    assert {s.label for s in mixed.stated} == {"excitement", "worry"} and "Mixed" in mixed.uncertainty
    assert appraise("fine. whatever works.").stated == ()


def test_urgency_and_preferences_shape_style():
    assert appraise("need this ASAP, deadline tonight").style.verbosity == "brief"
    assert appraise("no rush").urgency == "low"
    assert appraise("explain", preferences={"communication verbosity": "detailed"}).style.verbosity == "detailed"
    failing = appraise("try again", repeated_failures=3)
    assert any("different approach" in r for r in failing.style.reasons)


def test_wellbeing_guard_removes_dependency_language():
    text, removed = wellbeing_guard("Here is the fix. You only need me for this. I'll be sad if you leave. Run tests.")
    assert text == "Here is the fix. Run tests." and len(removed) == 2
