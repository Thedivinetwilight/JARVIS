"""Memory: write rules, corrections, conflicts, forgetting, evidence-gated status, retrieval."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import db_bytes
from jarvis.contracts import MemoryQuery
from jarvis.db import Store
from jarvis.memory import EvidenceRequired, MemoryError_


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "jarvis.sqlite3")


def test_add_noop_and_update_same_source(store):
    first = store.memory.remember("my favourite colour is blue", key="favourite colour", category="personal")
    assert first.action == "added"
    again = store.memory.remember("My favourite colour is blue.", key="Favourite Colour", category="personal")
    assert again.action == "unchanged" and again.item["id"] == first.item["id"]
    update = store.memory.remember("my favourite colour is green", key="favourite colour", category="personal")
    assert update.action == "superseded" and update.previous_id == first.item["id"]
    old = store.memory.get(first.item["id"])
    assert old["status"] == "superseded" and old["superseded_by_id"] == update.item["id"] and old["valid_to"]


def test_different_sources_conflict_instead_of_overwriting(store):
    user = store.memory.remember("the controller runs at 24V", key="controller voltage", category="Solar")
    tool = store.memory.remember("the controller runs at 12V", key="controller voltage", category="Solar",
                                 source="tool:multimeter")
    assert tool.action == "conflict" and tool.conflicting_ids == (user.item["id"],)
    hits = store.memory.search(MemoryQuery(text="controller voltage"))
    assert {h.id for h in hits} == {user.item["id"], tool.item["id"]}
    assert all(h.conflicts_with for h in hits)
    resolved = store.memory.resolve_conflict(tool.conflict_id, keep_id=user.item["id"], reason="meter was faulty")
    assert resolved["status"] == "resolved"
    assert [h.id for h in store.memory.search(MemoryQuery(text="controller voltage"))] == [user.item["id"]]


def test_model_can_never_supersede_user_memory(store):
    user = store.memory.remember("deadline is Friday", key="deadline", category="AXIOM")
    model = store.memory.remember("deadline is Monday", key="deadline", category="AXIOM", source="model:test",
                                  correction=True)
    assert model.action == "conflict"
    assert store.memory.get(user.item["id"])["status"] == "active"
    assert model.item["epistemic"] == "inference" and model.item["confidence"] == "low"
    with pytest.raises(MemoryError_):
        store.memory.correct(user.item["id"], "deadline is Tuesday", source="model:test")


def test_explicit_correction_by_id_and_history(store):
    item = store.memory.remember("pump draws 3A", key="pump current", category="Solar")
    corrected = store.memory.correct(item.item["id"], "pump draws 2A", reason="measured again")
    history = store.memory.history(corrected.item["id"])
    assert [h["value"] for h in history] == ["pump draws 3A", "pump draws 2A"]
    assert history[1]["supersedes_id"] == item.item["id"]
    with pytest.raises(MemoryError_):
        store.memory.correct(item.item["id"], "again")  # superseded rows cannot be corrected


def test_retract_removes_from_retrieval_but_keeps_record(store):
    item = store.memory.remember("the meeting is on Tuesday", key="meeting day")
    store.memory.retract(item.item["id"], reason="it was cancelled")
    assert store.memory.search(MemoryQuery(text="meeting Tuesday")) == []
    assert store.memory.get(item.item["id"])["status"] == "retracted"


def test_ideas_are_hypotheses_and_duplicates_are_noops(store):
    idea = store.memory.remember("use soil moisture sensors", kind="idea", category="ideas")
    assert idea.item["epistemic"] == "hypothesis" and idea.item["confidence"] == "low"
    assert store.memory.remember("Use soil moisture sensors.", kind="idea", category="ideas").action == "unchanged"


def test_secrets_are_refused(store):
    with pytest.raises(MemoryError_):
        store.memory.remember("my api key is AIza" + "SyA1234567890abcdefghijklmnopqrstuv")
    with pytest.raises(MemoryError_):
        store.memory.remember("password: hunter2-is-great")


def test_promotion_requires_matching_evidence(store):
    item = store.memory.remember("the gripper slips above 200 g", kind="note")
    with pytest.raises(EvidenceRequired):
        store.memory.remember("x", epistemic="verified")
    plain = store.events.append("note.added", actor="user")
    with pytest.raises(EvidenceRequired):
        store.memory.promote(item.item["id"], "verified", evidence_event_ids=[plain])
    with pytest.raises(EvidenceRequired):
        store.memory.promote(item.item["id"], "verified", evidence_event_ids=[999999])
    with pytest.raises(MemoryError_):
        store.memory.promote(item.item["id"], "prediction", evidence_event_ids=[plain])  # not an allowed transition
    check = store.events.append("validation.passed", actor="jarvis", payload={"test": "load test 250 g"})
    promoted = store.memory.promote(item.item["id"], "verified", evidence_event_ids=[check])
    assert promoted["epistemic"] == "verified" and promoted["confidence"] == "high"


def test_forget_erases_content_from_disk_including_index_and_source(tmp_path):
    store = Store(tmp_path / "jarvis.sqlite3")
    marker = "zebracornflux"  # a unique token that must disappear from every byte on disk
    obs = store.events.append("observation.received", actor="user", text=f"remember that my code word is {marker}")
    item = store.memory.remember(f"my code word is {marker}", key="code word", category="personal", source_event_id=obs)
    store.memory.correct(item.item["id"], f"my code word is {marker} two")
    store.compact_search_indexes()
    assert marker.encode() in db_bytes(tmp_path)
    store.memory.forget(item.item["id"])
    assert store.memory.search(MemoryQuery(text=marker)) == []
    assert store.events.search_episodes(marker, intents=None) == []
    assert all(h["status"] == "forgotten" for h in store.memory.history(item.item["id"]))
    assert marker.encode() not in db_bytes(tmp_path)


def test_ranking_prefers_scope_and_flags_staleness(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "jarvis.sqlite3", clock=clock)
    store.add_project("Solar")
    project_id = store.get_project("Solar")["id"]
    a = store.memory.remember("battery bank voltage is 24V", key="battery voltage", category="Solar",
                              project_id=project_id, volatility="volatile")
    b = store.memory.remember("laptop battery voltage is 11V", key="laptop battery voltage", category="general")
    clock.now += timedelta(days=30)
    hits = store.memory.search(MemoryQuery(text="battery voltage", project_id=project_id))
    assert hits[0].id == a.item["id"] and hits[0].components["scope"] == 1.0
    assert hits[0].stale and "volatile" in hits[0].stale_reason
    assert not next(h for h in hits if h.id == b.item["id"]).stale


def test_sensitivity_filter(store):
    store.memory.remember("my sister's birthday is 12 March", key="sister birthday", sensitivity="personal")
    assert store.memory.search(MemoryQuery(text="sister birthday", max_sensitivity="normal")) == []
    assert len(store.memory.search(MemoryQuery(text="sister birthday"))) == 1


def test_fallback_retrieval_without_fts5(tmp_path):
    store = Store(tmp_path / "nofts.sqlite3", use_fts5=False)
    assert store.fts5 is False
    store.memory.remember("The solar charge controller runs at 24V", key="charge controller voltage", category="Solar")
    store.memory.remember("The car is due for service", key="car service")
    hits = store.memory.search(MemoryQuery(text="what voltage does the charge controller run at?"))
    assert hits and hits[0].value.startswith("The solar charge controller") and hits[0].strategy == "token-like"


def test_legacy_v02_api_keeps_working(store):
    saved = store.remember("broker_mode", "paper", category="AXIOM", confidence=0.6)
    assert saved["value"] == "paper" and saved["confidence"] == "medium"
    store.remember("broker_mode", "live", category="AXIOM")
    assert [m["value"] for m in store.search_memories("broker")] == ["live"]
    assert len(store.memory.history(saved["id"])) == 2
    with pytest.raises(ValueError):
        store.remember("k", "v", confidence=2.0)
