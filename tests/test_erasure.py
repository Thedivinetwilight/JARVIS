"""Regression tests for the pre-release erasure audit (see docs/PRE_RELEASE_AUDIT.md).

Every test drives a real entry point (kernel cycle, CLI subprocess, tool registry, migration)
on an isolated database with unique marker strings, then looks for the markers in every table
(including FTS5 shadow tables), through FTS MATCH, and in the raw bytes of the database and WAL.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from conftest import db_bytes, run_cli
from jarvis.contracts import ActionRequest, MemoryQuery, Origin
from jarvis.db import Store
from jarvis.migrations import V02_BASELINE
from jarvis.tools import Tool


def residue(home: Path, markers: list[str]) -> dict[str, set[str]]:
    """Where (if anywhere) any marker still exists in the database at home/jarvis.sqlite3."""
    found: dict[str, set[str]] = {}
    conn = sqlite3.connect(home / "jarvis.sqlite3")
    try:
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            try:
                rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                for cell in row:
                    data = cell if isinstance(cell, bytes) else str(cell).encode()
                    for marker in markers:
                        if marker.encode() in data.lower():
                            found.setdefault(table, set()).add(marker)
        for fts in ("memory_fts", "episode_fts"):
            for marker in markers:
                if conn.execute(f"SELECT 1 FROM {fts} WHERE {fts} MATCH ?", (marker,)).fetchone():
                    found.setdefault(f"{fts} MATCH", set()).add(marker)
    finally:
        conn.close()
    raw = db_bytes(home).lower()
    for marker in markers:
        if marker.encode() in raw:
            found.setdefault("raw database/WAL bytes", set()).add(marker)
    return found


MARKERS = ["zqxjvwk", "91357", "qwvbnm", "24680"]


def test_forget_through_chat_leaves_no_copy_in_tables_indexes_or_file(app_factory, tmp_path):
    """Defects P1/P2/P6: replies quoting the memory, the recall question, the forget proposal,
    journal results (value + key) and journal arguments (key) survived `forget`."""
    app = app_factory()
    remembered = app.ask("remember that my qwvbnm dessert is zqxjvwk pudding 91357")
    first_id = remembered["actions"][0]["result"]["item"]["id"]
    assert "zqxjvwk" in app.ask("what is my qwvbnm dessert?")["answer"]
    corrected = app.ask(f"correct #{first_id} to zqxjvwk custard 24680")
    second_id = corrected["actions"][0]["result"]["item"]["id"]
    proposal = app.ask(f"forget #{second_id}")
    assert "zqxjvwk custard" in proposal["answer"]                  # the proposal quotes the value...
    assert residue(tmp_path / "home", MARKERS)                       # ...and the content is on disk
    done = app.ask(f"approve {proposal['actions'][0]['operation_id']}")
    action = done["actions"][0]
    assert action["status"] == "succeeded" and action["verified"] is True
    report = action["result"]
    assert report["forgotten_ids"] == [first_id, second_id]
    assert report["residue"] == {"derived": 0, "unlinked_user_mentions": 0, "possible_user_mentions": 0}
    assert report["linked_turns_wiped"] >= 6 and report["operations_scrubbed"] >= 2
    assert report["storage"]["wal_truncated"] is True
    assert residue(tmp_path / "home", MARKERS) == {}
    # After a restart nothing is recalled either, through any public path.
    app.close()
    again = app_factory()
    assert again.store.memory.search(MemoryQuery(text="zqxjvwk pudding")) == []
    assert again.store.search_memories("qwvbnm dessert") == []
    assert again.store.events.search_episodes("qwvbnm dessert zqxjvwk", intents=None) == []
    assert residue(tmp_path / "home", MARKERS) == {}
    assert "zqxjvwk" not in again.ask("what do you know about my dessert?")["answer"]


def test_forget_through_cli_erases_the_journal_copy(tmp_path):
    """Defect P1 on the CLI path: `memory correct` journaled the corrected value and key."""
    home = tmp_path / "home"
    saved = json.loads(run_cli(home, "memory", "save", "qwvbnm dessert", "zqxjvwk pudding 91357").stdout)
    corrected = json.loads(run_cli(home, "memory", "correct", str(saved["id"]), "zqxjvwk custard 24680").stdout)
    assert corrected["status"] == "succeeded"
    with sqlite3.connect(home / "jarvis.sqlite3") as db:
        journal = " ".join(str(r) for r in db.execute("SELECT arguments_json, result_json FROM operations"))
    assert "zqxjvwk" not in journal and "qwvbnm" not in journal       # content never journaled
    forgot = json.loads(run_cli(home, "memory", "forget", str(saved["id"]), "--yes").stdout)
    assert forgot["status"] == "succeeded" and forgot["verified"] is True
    assert residue(home, MARKERS) == {}


def test_session_summaries_lose_the_forgotten_text(app_factory, tmp_path):
    """Defect P3: a session summary copies the last user message (and the session.ended event
    repeats it); forgetting the memory left that copy behind."""
    app = app_factory()
    result = app.ask("remember that my qwvbnm dessert is zqxjvwk pudding 91357")
    app.store.state.end_session(result["session_id"], "user_exit")
    assert "zqxjvwk" in json.dumps(app.store.state.get_session(result["session_id"])["summary"])
    report = app.store.memory.forget(result["actions"][0]["result"]["item"]["id"])
    assert report["residue"]["derived"] == 0
    assert "zqxjvwk" not in json.dumps(app.store.state.get_session(result["session_id"])["summary"])
    assert residue(tmp_path / "home", MARKERS) == {}


def _v02_database(path: Path, category: str, key: str, value: str) -> None:
    """A database as JARVIS v0.2 (54b9e6b) wrote it, including its two audit formats for a
    memory write: ``memory.upsert`` with target ``<category>:<key>`` and ``tool.memory.save``
    whose details carry the (unredacted) key, serialised with json.dumps' default separators."""
    conn = sqlite3.connect(path)
    conn.executescript(V02_BASELINE)
    conn.execute("INSERT INTO memories(category,key,value,created_at,updated_at) VALUES (?,?,?,?,?)",
                 (category, key, value, "2026-10-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00"))
    details = json.dumps({"risk": "write", "reason": "Explicit user request",
                          "arguments": {"key": key, "value": "[REDACTED]", "category": category}})
    conn.executemany("INSERT INTO audit(timestamp,actor,action,target,decision,details_json) VALUES (?,?,?,?,?,?)",
                     [("2026-10-01T00:00:00+00:00", "user", "tool.memory.save", "memory.save", "allowed", details),
                      ("2026-10-01T00:00:00+00:00", "user", "memory.upsert", f"{category}:{key}", "allowed", "{}")])
    conn.commit()
    conn.close()


def test_migrated_v02_memory_is_erasable_even_without_compiled_in_secure_delete(tmp_path, monkeypatch):
    """Defects P4/P5: migration connections did not enable secure_delete, so on SQLite builds
    without SQLITE_SECURE_DELETE the dropped v0.2 tables kept the old rows in free pages; and the
    migrated v0.2 audit row kept the memory key in `events.target`."""
    home = tmp_path / "home"
    home.mkdir()
    _v02_database(home / "jarvis.sqlite3", "personal", "vremmix pin hint", "grokulent tangerine 77123")
    real_connect = sqlite3.connect

    def connect_like_a_build_without_secure_delete(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.execute("PRAGMA secure_delete=OFF")
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect_like_a_build_without_secure_delete)
    store = Store(home / "jarvis.sqlite3")
    report = store.memory.forget(store.memory.list()[0]["id"])
    monkeypatch.undo()
    assert residue(home, ["grokulent", "vremmix"]) == {}
    # The pre-migration backup still holds the data; the report says so instead of hiding it.
    assert report["backups_may_contain"] and len(report["backups_may_contain"]) == 1
    backup = home / "backups" / report["backups_may_contain"][0]
    assert b"grokulent" in backup.read_bytes()


def test_journal_never_stores_memory_or_file_content(app_factory, tmp_path):
    """Defect P1 (write side): results of memory tools and workspace.read, and memory keys and
    search queries in arguments, were journaled verbatim. The live caller still gets them."""
    app = app_factory()
    (tmp_path / "workspace" / "notes.txt").write_text("the qwvbnm notebook says zqxjvwk", encoding="utf-8")
    run = lambda cap, args: app.tools.run(ActionRequest(cap, args, Origin.USER_COMMAND))  # noqa: E731
    saved = run("memory.remember", {"value": "zqxjvwk pudding 91357", "key": "qwvbnm dessert", "category": "food",
                                    "kind": "fact", "sensitivity": "normal", "correction": False})
    assert saved.result["item"]["value"] == "zqxjvwk pudding 91357"          # live result is complete
    run("memory.correct", {"memory_id": saved.result["item"]["id"], "value": "zqxjvwk custard 24680",
                           "reason": "qwvbnm typo"})
    run("memory.save", {"key": "qwvbnm drink", "value": "zqxjvwk tea", "category": "food"})
    found = run("memory.search", {"query": "qwvbnm"})
    read = run("workspace.read", {"relative_path": "notes.txt"})
    assert found.ok and read.ok and "zqxjvwk" in read.result["text"]
    with app.store.read() as db:
        journal = [dict(r) for r in db.execute("SELECT capability, arguments_json, result_json FROM operations")]
        payloads = " ".join(r[0] for r in db.execute("SELECT payload_json FROM events WHERE kind LIKE 'operation.%'"))
    assert len(journal) == 5
    for row in journal:
        text = (row["arguments_json"] or "") + (row["result_json"] or "")
        assert "zqxjvwk" not in text and "qwvbnm" not in text, row
    assert "zqxjvwk" not in payloads and "qwvbnm" not in payloads
    # An idempotent replay returns the recorded, content-free summary (documented behaviour).
    first = app.tools.run(ActionRequest("memory.remember", {"value": "plain fact here", "key": "", "category": "",
                                                            "kind": "note", "sensitivity": "normal",
                                                            "correction": False}, Origin.USER_COMMAND,
                                        idempotency_key="k-1"))
    replay = app.tools.run(ActionRequest("memory.remember", {"value": "plain fact here", "key": "", "category": "",
                                                             "kind": "note", "sensitivity": "normal",
                                                             "correction": False}, Origin.USER_COMMAND,
                                         idempotency_key="k-1"))
    assert replay.replayed and replay.result["item"]["id"] == first.result["item"]["id"]
    assert "value" not in replay.result["item"]


def test_restatements_are_erased_and_unlinked_mentions_are_reported(app_factory, tmp_path):
    """A verbatim restatement of the value in another user turn is the same information and is
    erased; a turn that only mentions the subject is kept, counted and reported."""
    app = app_factory()
    app.ask("my qwvbnm dessert is zqxjvwk pudding 91357")                   # casual chat, not a command
    item = app.ask("remember that my qwvbnm dessert is zqxjvwk pudding 91357")["actions"][0]["result"]["item"]
    app.ask("I might bake something for the qwvbnm dessert party")           # mentions the subject only
    proposal = app.ask(f"forget #{item['id']}")
    done = app.ask(f"approve {proposal['actions'][0]['operation_id']}")
    report = done["actions"][0]["result"]
    assert report["residue"]["derived"] == 0 and report["residue"]["unlinked_user_mentions"] == 1
    assert report["residue"]["possible_user_mentions"] == 0 and len(report["mention_event_ids"]) == 1
    assert "1 other message(s) of yours still mention it or share distinctive words with it" in done["answer"]
    assert "Mentions in different words cannot be detected." in done["answer"]
    left = residue(tmp_path / "home", MARKERS)
    assert set(left.get("events", set())) == {"qwvbnm"}                    # only the kept, unlinked turn
    assert "zqxjvwk" not in json.dumps({k: sorted(v) for k, v in left.items()})


def test_pending_proposals_for_a_forgotten_memory_are_cancelled(app_factory):
    app = app_factory()
    app.ask("remember that the qwvbnm locker is number 91357")
    first = app.ask("forget #1")["actions"][0]["operation_id"]
    second = app.ask("forget #1")["actions"][0]["operation_id"]
    report = app.ask(f"approve {first}")["actions"][0]["result"]
    assert report["proposals_cancelled"] == [second]
    record = app.store.operations.get(second)
    assert record["status"] == "cancelled" and record["error_type"] == "TargetForgotten"
    assert app.tools.approve(second).executed is False


def test_forget_reports_a_wal_it_could_not_truncate(app_factory, tmp_path):
    """Honesty: an open reader blocks WAL truncation; the report and the reply must say so,
    and the forget must not stall for the full busy timeout waiting for that reader."""
    app = app_factory()
    item = app.ask("remember that my qwvbnm dessert is zqxjvwk pudding 91357")["actions"][0]["result"]["item"]
    op_id = app.ask(f"forget #{item['id']}")["actions"][0]["operation_id"]
    reader = sqlite3.connect(tmp_path / "home" / "jarvis.sqlite3", isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM events").fetchone()                 # holds a read snapshot
    try:
        started = time.monotonic()
        done = app.ask(f"approve {op_id}")
        elapsed = time.monotonic() - started
    finally:
        reader.execute("COMMIT")
        reader.close()
    assert elapsed < 5, f"forget stalled {elapsed:.1f}s waiting for a reader (busy timeout is 10 s)"
    action = done["actions"][0]
    assert action["result"]["storage"]["wal_truncated"] is False
    assert "write-ahead log could not be truncated" in action["verification_note"]
    assert "write-ahead log will only be cleared" in done["answer"]
    app.store.checkpoint()
    assert residue(tmp_path / "home", MARKERS) == {}                           # gone once the reader left


def test_concurrent_requests_with_one_idempotency_key_execute_once_and_both_get_an_outcome(app_factory):
    """Defect O1: the second of two concurrent callers got a raw sqlite3.IntegrityError."""
    app = app_factory()
    calls: list[int] = []
    app.tools.register(Tool("test.slow_write", "slow", "write",
                            lambda a: (time.sleep(0.3), calls.append(1), {"n": a["n"]})[-1], {"n": int}, timeout_s=5))
    barrier, results = threading.Barrier(2), {}

    def worker(i: int) -> None:
        barrier.wait()
        try:
            results[i] = app.tools.run(ActionRequest("test.slow_write", {"n": 1}, Origin.USER_COMMAND,
                                                     idempotency_key="same-key"))
        except Exception as exc:  # noqa: BLE001 - recorded so the assertion can show it
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert all(not isinstance(r, Exception) for r in results.values()), results
    assert sorted(bool(r.replayed) for r in results.values()) == [False, True]


def test_retention_purge_of_expired_appraisals_truncates_the_wal(app_factory):
    """P8: expired (sensitive) events were deleted at start-up without truncating the WAL, so
    their page images could linger in the -wal file."""
    app = app_factory(affect_retention_days=0)              # appraisals expire immediately
    app.ask("I'm really worried and anxious about the audit tomorrow")
    with app.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE kind='affect.appraised'").fetchone()[0] == 1
    app.close()
    restarted = app_factory(affect_retention_days=0)
    assert restarted.recovery["purged_expired_events"] >= 1
    assert restarted.recovery["wal_truncated"] is True
    with restarted.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE kind='affect.appraised'").fetchone()[0] == 0


def test_single_word_values_never_erase_unrelated_history(app_factory):
    """Over-deletion guard: forgetting a one-word value (here the verbosity preference
    "detailed") must not erase the person's other messages that merely contain the word."""
    app = app_factory()
    app.ask("please be more detailed")                                       # source turn of the preference
    app.ask("give me a detailed plan for the qwvbnm garden")                 # unrelated, contains "detailed"
    preference = app.store.memory.find_active_by_key("communication verbosity")[0]
    assert preference["value"] == "detailed"
    report = app.store.memory.forget(preference["id"])
    assert report["residue"]["derived"] == 0
    with app.store.read() as db:
        turns = [r[0] for r in db.execute("SELECT text FROM events WHERE kind='observation.received'")]
    assert "give me a detailed plan for the qwvbnm garden" in turns
    assert "please be more detailed" not in turns


def test_single_word_v02_key_is_scrubbed_from_both_v02_audit_formats(tmp_path):
    """A one-word v0.2 key (e.g. ``broker_mode``) survives in ``<category>:<key>`` targets and in
    ``"key": "<key>"`` audit details; both are matched structurally, never as a bare substring."""
    home = tmp_path / "home"
    home.mkdir()
    _v02_database(home / "jarvis.sqlite3", "AXIOM", "plovnik_mode", "paper")
    store = Store(home / "jarvis.sqlite3")
    report = store.memory.forget(store.memory.list()[0]["id"])
    assert report["residue"]["derived"] == 0
    assert residue(home, ["plovnik_mode"]) == {}


# ---------------------------------------------------------------- pre-release review (second pass)
def test_partial_restatements_are_kept_but_disclosed(app_factory):
    """Review R1: a later message that restates the memory partially (not verbatim) is the
    person's own words and is kept — but it can resurface through episodic recall, so the
    report and the reply must disclose it instead of claiming nothing remains."""
    app = app_factory()
    item = app.ask("remember that my quandrel recipe is vorpalix brumbletoast with honey")["actions"][0]["result"]["item"]
    kept = app.ask("I made vorpalix brumbletoast with honey again today")
    kept_id = next(e["id"] for e in app.store.events.for_cycle(kept["cycle_id"]) if e["kind"] == "observation.received")
    proposal = app.ask(f"forget #{item['id']}")
    done = app.ask(f"approve {proposal['actions'][0]['operation_id']}")
    report = done["actions"][0]["result"]
    assert report["residue"] == {"derived": 0, "unlinked_user_mentions": 0, "possible_user_mentions": 1}
    assert report["mention_event_ids"] == [kept_id]
    assert "1 other message(s) of yours still mention it or share distinctive words with it" in done["answer"]
    assert "can still come up in conversation" in done["answer"]
    assert app.store.events.get(kept_id)["text"] == "I made vorpalix brumbletoast with honey again today"


def test_reply_of_a_cycle_running_during_forget_is_withheld_and_never_stored(app_factory, tmp_path):
    """Review R2: a second runtime on the same database was mid-cycle (waiting for its model)
    with the memory in context when the forget committed; its reply then quoted the memory and
    stored it after the forget had reported success."""
    from jarvis.provider import ScriptedProvider

    owner = app_factory()
    mid = owner.ask("remember that the plovnik gate code hint is grumbleberry")["actions"][0]["result"]["item"]["id"]
    op = owner.ask(f"forget #{mid}")["actions"][0]["operation_id"]
    in_model, release, seen = threading.Event(), threading.Event(), {}

    def slow_model(request):
        with sqlite3.connect(tmp_path / "home" / "jarvis.sqlite3") as db:      # observed from outside, mid-cycle
            seen["consulted"] = [json.loads(r[0]) for r in db.execute(
                "SELECT memory_ids_json FROM cycles WHERE status='running'")]
        in_model.set()
        release.wait(10)
        return "Your plovnik gate code hint is grumbleberry."

    other = app_factory(model_provider=ScriptedProvider([slow_model]))
    result: dict = {}
    worker = threading.Thread(target=lambda: result.update(other.ask("chat: what was the plovnik gate thing again")))
    worker.start()
    assert in_model.wait(10)
    done = owner.tools.approve(op, approved_by="user")                       # the forget commits now
    release.set()
    worker.join(15)
    assert done.status.value == "succeeded" and done.verified is True
    assert [mid] in seen["consulted"]                  # the running cycle had recorded what it used
    assert "grumbleberry" not in result["answer"] and "just been forgotten" in result["answer"]
    assert residue(tmp_path / "home", ["grumbleberry"]) == {}


def test_forget_cost_does_not_grow_with_the_number_of_search_terms(app_factory, monkeypatch):
    """Review R3: the sweep ran one full-table SQL scan per search term (26 JSON-field patterns
    per single word, twice), so a forget on a 100k-event history took over 6 s against a 10 s
    tool timeout. Each table is now scanned once for the sweep and once for the check."""
    app = app_factory()
    item = app.store.memory.remember("gruntleberry", key="plovnikmode")      # two single-word terms
    statements: list[str] = []
    original = app.store._connect

    def traced():
        db = original()
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(app.store, "_connect", traced)
    app.store.memory.forget(item.item["id"])
    full_event_scans = [s for s in statements if "FROM events" in s and "WHERE id=" not in s
                        and "id IN" not in s and "memory_id" not in s and "operation_id" not in s
                        and "cycle_id IN" not in s]
    assert len(full_event_scans) <= 6, len(full_event_scans)
    assert app.tools.get("memory.forget").timeout_s >= 60
