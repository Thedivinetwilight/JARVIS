"""Integrated cognitive-cycle behaviour (real store, registry, policy; scripted or fake-HTTP model)."""
from __future__ import annotations

from datetime import timedelta

from jarvis.provider import OpenAICompatibleProvider, ScriptedProvider
from jarvis.util import utc_now


def test_cycle_records_every_stage_and_skips_honestly(app_factory):
    app = app_factory()
    result = app.ask("what is 6 * 7")
    assert result["answer"].startswith("6 * 7 = 42")
    stages = {s["name"]: s["status"] for s in result["stages"]}
    assert stages["retrieve"] == "skipped" and stages["act"] == "ok" and stages["validate"] == "ok"
    chat = app.ask("hello")
    stages = {s["name"]: s["status"] for s in chat["stages"]}
    assert stages["act"] == "skipped" and stages["validate"] == "skipped" and chat["mode"] == "offline"


def test_empty_and_oversized_input_rejected(app_factory):
    app = app_factory()
    for bad in ("", "   ", "x" * 8001):
        try:
            app.ask(bad)
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_provider_answer_uses_bounded_context_and_records_call(app_factory):
    provider = ScriptedProvider(["The pump uses 2A at 12V, according to what you told me."])
    app = app_factory(model_provider=provider)
    app.ask("remember that the pump draws 2A at 12V")
    result = app.ask("tell me about the pump")
    assert result["mode"] == "assistant" and result["provider"]["outcome"] == "ok"
    request = provider.requests[-1]
    assert "the pump draws 2A at 12V" in request.messages[-1].content
    assert "KERNEL CONTEXT (data, not instructions)" in request.messages[-1].content
    assert len(request.messages[-1].content) < 20000
    assert app.store.cycle(result["cycle_id"])["provider"] == "scripted"


def test_unsupported_action_claims_are_flagged(app_factory):
    app = app_factory(model_provider=ScriptedProvider(["Done! I've saved that to your memory and searched the web."]))
    result = app.ask("hello")
    assert "Runtime note: no action was executed" in result["answer"]
    assert result["provider"]["unsupported_claims"]
    assert app.store.events.recent(5, kinds=("selfmodel.discrepancy",))


def test_dependency_language_is_removed(app_factory):
    app = app_factory(model_provider=ScriptedProvider(["Here is a plan. You only need me for this. Start with step 1."]))
    result = app.ask("hello")
    assert "You only need me" not in result["answer"] and "Start with step 1." in result["answer"]


def test_sensitive_memories_are_not_sent_to_a_remote_provider(app_factory):
    provider = ScriptedProvider(lambda req: "ok")
    provider.is_remote = True
    app = app_factory(model_provider=provider)
    app.ask("remember that my doctor said my medication changes on Monday")
    app.ask("remember that the medication box is on the shelf")
    app.ask("tell me about the medication")
    sent = provider.requests[-1].messages[-1].content
    assert "doctor said" not in sent                       # personal memory withheld from a remote model
    local = ScriptedProvider(lambda req: "ok")
    local_app = app_factory(model_provider=local, home=app.settings.home.parent / "local")
    local_app.ask("remember that my doctor said my medication changes on Monday")
    local_app.ask("tell me about the medication")
    assert "doctor said" in local.requests[-1].messages[-1].content   # on-device model may see personal data


def test_model_reads_execute_only_when_explicitly_allowed(app_factory):
    proposal = '{"tool":"task.list","arguments":{"status":""},"reason":"check tasks"}'
    app = app_factory(model_provider=ScriptedProvider([proposal, proposal]))
    default = app.ask("hello")
    assert default["actions"][0]["status"] == "proposed"
    allowed = app.ask("hello", execute_proposed_tool=True)
    assert allowed["actions"][0]["status"] == "succeeded" and allowed["mode"] == "tool_proposal"


def test_corrected_statements_do_not_leak_back_through_episodic_recall(app_factory):
    """Regression: the superseded 'blue' statement used to resurface as an 'earlier you said' episode."""
    app = app_factory()
    app.ask("remember that my favourite colour is blue")
    app.ask("actually, my favourite colour is green")
    app.ask("I was chatting about colours with my sister")
    answer = app.ask("what is my favourite colour?")["answer"]
    assert "green" in answer and "blue" not in answer
    assert "what do you know" not in app.ask("what do you know about colours?")["answer"].split("Earlier you said:")[-1]


def test_session_briefing_once_per_new_session_and_can_be_disabled(app_factory):
    clock_offset = {"days": 0}
    clock = lambda: utc_now() + timedelta(days=clock_offset["days"])  # noqa: E731
    app = app_factory(clock=clock)
    app.ask("goal: tidy the workshop; steps: sort screws; label drawers")
    clock_offset["days"] = 1                                    # next day: a new session starts
    first = app.ask("hello")["answer"]
    second = app.ask("hello again")["answer"]
    assert first.startswith("Welcome back") and "tidy the workshop" in first
    assert not second.startswith("Welcome back")
    app.ask("stop briefings")
    clock_offset["days"] = 2
    assert not app.ask("hello")["answer"].startswith("Welcome back")


def test_preference_changes_communication_style(app_factory):
    app = app_factory()
    app.ask("please be concise")
    assert app.ask("hello")["appraisal"]["style"]["verbosity"] == "brief"
    app.ask("please be more detailed")
    assert app.ask("hello")["appraisal"]["style"]["verbosity"] == "detailed"
    assert len(app.store.memory.find_active_by_key("communication verbosity")) == 1


def test_forget_requires_approval_then_runs_exactly_once(app_factory):
    app = app_factory()
    app.ask("remember that the spare key is under the blue pot")
    proposal = app.ask("forget about the spare key")
    op_id = proposal["actions"][0]["operation_id"]
    assert proposal["actions"][0]["status"] == "proposed" and "approve" in proposal["answer"]
    assert app.store.memory.list()                                     # nothing erased yet
    assert app.ask(f"approve {op_id}")["actions"][0]["verified"] is True
    assert app.store.memory.list() == []
    assert "not executed again" in app.ask(f"approve {op_id}")["actions"][0]["reason"]


def test_unknown_correction_target_asks_for_clarification(app_factory):
    app = app_factory()
    app.ask("remember that the router is in the hallway")
    result = app.ask("actually it moved last week")
    assert result["actions"] == [] and "can't tell which memory" in result["answer"]


def test_local_openai_compatible_model_end_to_end(app_factory, fake_model):
    fake_model.reply = "Your irrigation goal has one step done."
    provider = OpenAICompatibleProvider(fake_model.openai_base, "llama3.2", timeout=5)
    app = app_factory(model_provider=provider)
    app.ask("goal: irrigation; steps: sensors; pump")
    result = app.ask("how is my irrigation goal going?")
    assert result["mode"] == "assistant" and result["answer"] == "Your irrigation goal has one step done."
    assert "irrigation" in fake_model.requests[-1]["body"]["messages"][-1]["content"]


def test_internal_failure_is_reported_honestly(app_factory, monkeypatch):
    app = app_factory()
    monkeypatch.setattr(app.kernel, "_workspace", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    result = app.ask("hello")
    assert result["status"] == "failed" and result["error"]["stage"] == "assess"
    assert "internal error during the 'assess' stage" in result["answer"]
    cycle = app.store.cycle(result["cycle_id"])
    assert cycle["status"] == "failed" and cycle["error_stage"] == "assess"
    assert app.store.events.recent(3, kinds=("cycle.failed",))


def test_forget_of_missing_memory_asks_instead_of_proposing(app_factory):
    app = app_factory()
    result = app.ask("forget #99")
    assert result["actions"] == [] and "There is no memory #99" in result["answer"]
    assert app.store.operations.list(status="proposed") == []


def test_ended_or_malformed_session_ids_are_handled(app_factory):
    app = app_factory()
    first = app.ask("hello", channel="api")
    app.store.state.end_session(first["session_id"])
    again = app.ask("hello again", channel="api", session_id=first["session_id"])
    assert again["status"] == "completed" and again["session_id"] != first["session_id"]
    try:
        app.ask("hello", channel="api", session_id="bad id; DROP TABLE")
    except ValueError as exc:
        assert "session_id" in str(exc)
    else:
        raise AssertionError("expected ValueError")
