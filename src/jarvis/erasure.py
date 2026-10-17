"""Erasure policy for ``forget``: what is deleted, redacted or retained.

When the user forgets a memory (an irreversible, approved operation), JARVIS erases its
content from every representation it created or can link to it, inside the same
transaction that tombstones the memory, and afterwards truncates the write-ahead log.

Erased (content replaced by a placeholder; rows kept as content-free records):

1. every version of the memory: value, key and evidence (done by ``MemoryStore.forget``);
   the row and its id remain as a tombstone so history and references stay consistent;
2. the user turns that created, restated or corrected a version (source events);
3. linked conversation turns: JARVIS's reply in every cycle that created, corrected,
   retrieved or proposed forgetting a version, and the user's own turn when that turn was
   *about* the memory (it created/corrected it, asked to forget it, or was a recall
   question answered with it). Cycles record the memories they use as soon as they
   retrieve them, and a cycle that is still running when the forget commits withholds its
   reply (``CognitiveKernel``), so a reply cannot quote the memory after it was forgotten;
4. linked records: payloads of events about a version and of journal events for operations
   that targeted or returned it, and those operations' arguments and results (content
   fields removed; ids, kinds, statuses and timestamps retained). Pending proposals that
   target it are cancelled so approving them later cannot re-create it;
5. derived copies found by verbatim matching: JARVIS-generated text, event payloads and
   targets, journal entries and session summaries that contain a version's value or key as
   a multi-word phrase; a single-word value or key only as a content field of a JSON record
   (``"key": "<word>"``) or as an exact event target (``<word>`` or v0.2's
   ``<category>:<key>``) — single words are never substring-matched, because that would
   erase unrelated history and collide with labels such as a "detailed" response style;
   and user turns that repeat a version's full value verbatim (at least
   MIN_USER_VALUE_WORDS words and MIN_USER_VALUE_CHARS characters).

Retained on purpose, and reported: the person's own messages that are not linked to the
memory but still mention it — verbatim (``unlinked_user_mentions``) or by sharing at least
two distinctive words with it (``possible_user_mentions``). They are counted and their event
ids returned, never deleted on a word match, which would destroy unrelated parts of the
person's own history; they can still come up in conversation (episodic recall). Also
retained: tombstones, the ``memory.forgotten`` event (ids and counts only) and the fact
that operations ran.

Not reachable, and therefore never claimed: backups made before the deletion, exports,
copies already sent to a remote model provider, copies outside the database, restatements
in different words, and the physical storage medium (SSD wear levelling, filesystem
journals, swap, snapshots).

Cost: each affected table is scanned once in Python for the sweep and once for the
post-condition check, so the time grows linearly with the size of the history.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Callable, Iterable

from .search import STOPWORDS
from .util import dumps, loads, parse_iso

PLACEHOLDER = "[forgotten]"
TURN_PLACEHOLDER = "[forgotten at user request]"   # same text as events.FORGOTTEN_TEXT
MIN_TERM_CHARS = 4          # values/keys shorter than this are too ambiguous to match on
MIN_USER_VALUE_CHARS = 12   # a user's own turn is erased only for a verbatim restatement this long...
MIN_USER_VALUE_WORDS = 3    # ...and of at least this many words
MIN_SHARED_WORDS = 2        # a remaining turn sharing this many distinctive words is reported
CONTENT_KEYS = frozenset({"value", "key", "text", "content", "body", "reason", "note", "message", "query",
                          "last_user_message", "a_value", "b_value", "blocker"})
_CHUNK = 400                # stay well below SQLite's host-parameter limit on old builds
HANDLING_CAPABILITIES = frozenset({"memory.remember", "memory.save", "memory.correct", "memory.forget"})
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def strip_content(obj: Any) -> Any:
    """Replace the values of content-bearing fields, keeping structure, ids and statuses."""
    if isinstance(obj, dict):
        return {k: (PLACEHOLDER if k in CONTENT_KEYS and isinstance(v, str) and v else strip_content(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_content(v) for v in obj]
    return obj


def _chunks(values: Iterable[Any]) -> Iterable[list[Any]]:
    items = list(values)
    for start in range(0, len(items), _CHUNK):
        yield items[start:start + _CHUNK]


def _ids_where(db: sqlite3.Connection, sql: str, values: Iterable[Any]) -> set[Any]:
    """Run ``sql`` (containing one ``{placeholders}`` field) over ``values`` in chunks."""
    found: set[Any] = set()
    for chunk in _chunks(values):
        found |= {r[0] for r in db.execute(sql.format(placeholders=",".join("?" * len(chunk))), chunk)}
    return found


class _Matcher:
    """Verbatim matcher. Phrases match as substrings (case-insensitive); single words match
    only as a JSON content field (``"key": "<word>"``, compact or spaced, escaped or not)."""

    def __init__(self, phrases: Iterable[str], singles: Iterable[str] = ()):
        self.plain = sorted({p.lower() for p in phrases if p})
        self.singles: dict[str, list[str]] = {}
        for word in singles:
            encodings = {json.dumps(word, ensure_ascii=False), json.dumps(word)}
            self.singles[word.lower()] = sorted({f'"{field}":{sep}{enc}'.lower() for field in CONTENT_KEYS
                                                 for sep in ("", " ") for enc in encodings})

    def __call__(self, text: Any) -> bool:
        if not isinstance(text, str) or not text:
            return False
        low = text.lower()
        if any(p in low for p in self.plain):
            return True
        return any(word in low and any(pair in low for pair in pairs) for word, pairs in self.singles.items())


def _scan(db: sqlite3.Connection, sql: str, match: Callable[[Any], bool], args: tuple[Any, ...] = (),
          exact: frozenset[str] = frozenset(), exact_column: int | None = None) -> set[Any]:
    """Keys (first column) of rows where any other column matches, or where ``exact_column``
    equals one of ``exact`` (case-insensitive). One streaming pass over the table."""
    found: set[Any] = set()
    for row in db.execute(sql, args):
        if any(match(value) for value in row[1:]) or (
                exact_column is not None and isinstance(row[exact_column], str) and row[exact_column].lower() in exact):
            found.add(row[0])
    return found


_EVENTS_DERIVED = "SELECT id, text, payload_json, target FROM events WHERE kind <> 'observation.received'"


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) >= MIN_TERM_CHARS and w not in STOPWORDS}


def _referenced_item_ids(result: Any) -> set[int]:
    """Memory ids referenced by a journaled memory-tool result (full or summarised)."""
    refs: set[int] = set()
    if isinstance(result, dict):
        item = result.get("item") if isinstance(result.get("item"), dict) else result
        if isinstance(item.get("id"), int) and ("kind" in item or "status" in item or "value" in item):
            refs.add(item["id"])
        if isinstance(result.get("previous_id"), int):
            refs.add(result["previous_id"])
        for hit in result.get("hits", []) if isinstance(result.get("hits"), list) else []:
            if isinstance(hit, dict) and isinstance(hit.get("id"), int):
                refs.add(hit["id"])
        for value in result.get("forgotten_ids", []) or []:
            if isinstance(value, int):
                refs.add(value)
    elif isinstance(result, list):
        for row in result:
            if isinstance(row, dict) and isinstance(row.get("id"), int):
                refs.add(row["id"])
    return refs


def erase_traces(db: sqlite3.Connection, events: Any, ids: list[int], versions: list[dict[str, Any]], *,
                 now: str, wipe_user_turns: bool = True) -> dict[str, Any]:
    """Erase linked and derived copies of the memory versions ``versions`` (see module doc).

    Runs inside the caller's write transaction. Returns counts for the forget report,
    including a post-condition ``residue`` count that must be zero for derived records, and
    the remaining user turns that still mention the memory (reported, not erased).
    """
    idset = set(ids)
    values = {v["value"].strip() for v in versions if v.get("value") and v["value"].strip() != PLACEHOLDER}
    keys = {v["key"].strip() for v in versions if v.get("key")}
    candidates = {t for t in values | keys if len(t) >= MIN_TERM_CHARS}
    phrases = sorted(t for t in candidates if len(t.split()) >= 2)        # substring-matched
    singles = sorted(t for t in candidates if len(t.split()) == 1)        # structural matches only
    # Keys appear as whole event targets in v0.2 audit rows (``<category>:<key>``); values never do.
    exact_targets = frozenset(
        {k.lower() for k in keys if len(k) >= MIN_TERM_CHARS and len(k.split()) == 1}
        | {f"{v['category']}:{v['key']}".lower() for v in versions if v.get("key") and v.get("category")})
    mention_terms = sorted(set(phrases) | {k for k in keys if len(k) >= MIN_TERM_CHARS})
    distinctive = [w for w in (_words(t) for t in values | keys) if len(w) >= MIN_SHARED_WORDS]
    user_terms = sorted(v for v in values
                        if len(v) >= MIN_USER_VALUE_CHARS and len(v.split()) >= MIN_USER_VALUE_WORDS)
    report: dict[str, Any] = {"source_events_wiped": 0, "linked_turns_wiped": 0, "linked_records_scrubbed": 0,
                              "derived_records_scrubbed": 0, "operations_scrubbed": 0, "proposals_cancelled": []}

    # ---- 1. link: cycles and operations that handled or used a version -------------------------
    created_cycles = _ids_where(db, "SELECT DISTINCT cycle_id FROM events WHERE cycle_id IS NOT NULL "
                                    "AND memory_id IN ({placeholders})", ids)
    linked_ops: dict[int, sqlite3.Row] = {}
    for op in db.execute("SELECT id, correlation_id, status, capability, arguments_json, result_json "
                         "FROM operations WHERE capability LIKE 'memory.%'"):
        args = loads(op["arguments_json"], {}) or {}
        refs = {args.get("memory_id")} | _referenced_item_ids(loads(op["result_json"], None))
        if refs & idset:
            linked_ops[op["id"]] = op
    op_cycles = {op["correlation_id"] for op in linked_ops.values()
                 if op["correlation_id"] and op["capability"] in HANDLING_CAPABILITIES}
    searched = {op["correlation_id"] for op in linked_ops.values()
                if op["correlation_id"] and op["capability"] not in HANDLING_CAPABILITIES}
    consulted: dict[str, str | None] = {}
    for cycle in db.execute("SELECT id, intent, memory_ids_json FROM cycles WHERE memory_ids_json <> '[]'"):
        if set(loads(cycle["memory_ids_json"], []) or []) & idset:
            consulted[cycle["id"]] = cycle["intent"]
    handled = created_cycles | op_cycles
    about = handled | {cid for cid, intent in consulted.items() if intent in {"recall", "forget", "correct"}}
    reply_cycles = handled | set(consulted) | searched

    # ---- 2-3. user turns (source + turns about it) and JARVIS replies --------------------------
    wiped_texts: list[str] = []
    turn_ids: set[int] = set()
    if wipe_user_turns:
        source_ids = {v["source_event_id"] for v in versions if v.get("source_event_id")}
        report["source_events_wiped"] = len(source_ids)
        turn_ids |= source_ids
        turn_ids |= _ids_where(db, "SELECT id FROM events WHERE kind='observation.received' "
                                   "AND cycle_id IN ({placeholders})", about)
    turn_ids |= _ids_where(db, "SELECT id FROM events WHERE kind='response.produced' "
                               "AND cycle_id IN ({placeholders})", reply_cycles)
    for chunk in _chunks(turn_ids):
        marks = ",".join("?" * len(chunk))
        # Only user turns are copied elsewhere (session summaries), so only they become match terms.
        wiped_texts += [r[0] for r in db.execute(f"SELECT text FROM events WHERE id IN ({marks}) AND text IS NOT NULL "
                                                 "AND text <> ? AND kind = 'observation.received'",
                                                 (*chunk, TURN_PLACEHOLDER))]
        db.execute(f"UPDATE events SET text=?, payload_json='{{}}' WHERE id IN ({marks})", (TURN_PLACEHOLDER, *chunk))
    report["linked_turns_wiped"] = len(turn_ids)

    # ---- 4. linked records: event payloads, journal entries, pending proposals -----------------
    linked_events = _ids_where(db, "SELECT id FROM events WHERE memory_id IN ({placeholders})", ids)
    finished_ops = [op_id for op_id, op in linked_ops.items() if op["status"] != "started"]
    linked_events |= _ids_where(db, "SELECT id FROM events WHERE operation_id IN ({placeholders})", finished_ops)
    for event_id in linked_events - turn_ids:
        row = db.execute("SELECT payload_json FROM events WHERE id=?", (event_id,)).fetchone()
        db.execute("UPDATE events SET payload_json=? WHERE id=?",
                   (dumps(strip_content(loads(row["payload_json"], {}) or {})), event_id))
    report["linked_records_scrubbed"] = len(linked_events - turn_ids)
    for op in linked_ops.values():
        _scrub_operation(db, events, op, now, report)

    # ---- 5. derived copies by verbatim match (one pass per table) -------------------------------
    match = _Matcher(set(phrases) | {t[:200] for t in wiped_texts if len(t) >= MIN_USER_VALUE_CHARS}, singles)
    derived = 0
    for event_id in _scan(db, _EVENTS_DERIVED, match, exact=exact_targets, exact_column=3):
        row = db.execute("SELECT text, payload_json, target FROM events WHERE id=?", (event_id,)).fetchone()
        payload = dumps(strip_content(loads(row["payload_json"], {}) or {}))
        payload = "{}" if match(payload) else payload
        target_hit = match(row["target"]) or (row["target"] or "").lower() in exact_targets
        db.execute("UPDATE events SET text=?, payload_json=?, target=? WHERE id=?",
                   (TURN_PLACEHOLDER if match(row["text"]) else row["text"], payload,
                    PLACEHOLDER if target_hit else row["target"], event_id))
        derived += 1
    restate = _Matcher(user_terms)
    if wipe_user_turns and user_terms:
        restated = _scan(db, "SELECT id, text FROM events WHERE kind = 'observation.received' AND text <> ?",
                         restate, (TURN_PLACEHOLDER,))
        for chunk in _chunks(restated):
            db.execute(f"UPDATE events SET text=?, payload_json='{{}}' WHERE id IN ({','.join('?' * len(chunk))})",
                       (TURN_PLACEHOLDER, *chunk))
        derived += len(restated)
    for op_id in _scan(db, "SELECT id, arguments_json, result_json FROM operations", match):
        if op_id not in linked_ops:
            op = db.execute("SELECT id, correlation_id, status, capability, arguments_json, result_json "
                            "FROM operations WHERE id=?", (op_id,)).fetchone()
            _scrub_operation(db, events, op, now, report, match=match)
            derived += 1
    for session_id in _scan(db, "SELECT id, summary_json FROM sessions WHERE summary_json IS NOT NULL", match):
        row = db.execute("SELECT summary_json FROM sessions WHERE id=?", (session_id,)).fetchone()
        summary = dumps(strip_content(loads(row["summary_json"], {}) or {}))
        db.execute("UPDATE sessions SET summary_json=? WHERE id=?",
                   ("{}" if match(summary) else summary, session_id))
        derived += 1
    report["derived_records_scrubbed"] = derived

    # ---- post-condition: nothing derived may still contain the content -------------------------
    residue = len(_scan(db, _EVENTS_DERIVED, match, exact=exact_targets, exact_column=3))
    residue += len(_scan(db, "SELECT id, arguments_json, result_json FROM operations WHERE status <> 'started'", match))
    residue += len(_scan(db, "SELECT id, summary_json FROM sessions WHERE summary_json IS NOT NULL", match))
    residue += len(_scan(db, "SELECT key, value_json FROM state_kv", match))
    residue += len(_scan(db, "SELECT id, stages_json, validation_json FROM cycles", match))
    if wipe_user_turns and user_terms:
        residue += len(_scan(db, "SELECT id, text FROM events WHERE kind = 'observation.received'", restate))

    # ---- what remains in the person's own words (reported, never erased on a word match) ---------
    verbatim = _Matcher(mention_terms)
    exact_mentions, possible_mentions = set(), set()
    for event_id, text in db.execute("SELECT id, text FROM events WHERE kind = 'observation.received' "
                                     "AND text IS NOT NULL AND text <> ?", (TURN_PLACEHOLDER,)):
        if verbatim(text):
            exact_mentions.add(event_id)
        elif distinctive and any(len(_words(text) & words) >= MIN_SHARED_WORDS for words in distinctive):
            possible_mentions.add(event_id)
    report["residue"] = {"derived": residue, "unlinked_user_mentions": len(exact_mentions),
                         "possible_user_mentions": len(possible_mentions)}
    report["mention_event_ids"] = sorted(exact_mentions | possible_mentions)[:50]
    return report


def _scrub_operation(db: sqlite3.Connection, events: Any, op: sqlite3.Row, now: str, report: dict[str, Any],
                     match: Callable[[Any], bool] | None = None) -> None:
    """Remove content from a journal row; cancel it if it is still only a proposal."""
    if op["status"] == "started":       # running right now (e.g. this forget itself): leave it alone
        return
    args = strip_content(loads(op["arguments_json"], {}) or {})
    result = loads(op["result_json"], None)
    result = strip_content(result) if result is not None else None
    if match is not None and match(dumps(args)):
        args = {k: PLACEHOLDER for k in args}
    if match is not None and result is not None and match(dumps(result)):
        result = {"journal": PLACEHOLDER}
    if op["status"] == "proposed":
        db.execute("UPDATE operations SET status='cancelled', finished_at=?, error_type='TargetForgotten', "
                   "error_message=?, arguments_json=?, result_json=NULL WHERE id=? AND status='proposed'",
                   (now, "Cancelled: it referred to content the user asked JARVIS to forget", dumps(args), op["id"]))
        events.append("operation.cancelled", actor="system", target=op["capability"], operation_id=op["id"],
                      cycle_id=op["correlation_id"], decision="cancelled",
                      payload={"reason": "target forgotten"}, conn=db)
        report["proposals_cancelled"].append(op["id"])
        return
    db.execute("UPDATE operations SET arguments_json=?, result_json=? WHERE id=?",
               (dumps(args), dumps(result) if result is not None else None, op["id"]))
    report["operations_scrubbed"] += 1


def backups_possibly_containing(backup_dir: Any, earliest_created_at: str | None) -> list[str]:
    """Backup files written after the memory existed (they may hold a copy forget cannot reach)."""
    from datetime import datetime, timezone
    from pathlib import Path

    folder = Path(backup_dir)
    if not folder.is_dir():
        return []
    since = parse_iso(earliest_created_at)  # legacy v0.2 timestamps may lack an offset (treated as UTC)
    found = []
    for path in sorted(folder.glob("*.sqlite3")):
        written = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if since is None or written >= since:
            found.append(path.name)
    return found


__all__ = ["CONTENT_KEYS", "PLACEHOLDER", "TURN_PLACEHOLDER", "backups_possibly_containing", "erase_traces",
           "strip_content"]
