"""Task-set loading and validation.

A task set is a directory with `tasks.json` (and, for B1, `corpus.json`). Files are
immutable once published: new items require a new version string.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import restore_integrity, write_state
from .config import sha256_obj
from .faults import RESTORE_ATTEMPT_LIMIT

REQUIRED_BLOCK_FIELDS = {"version", "block", "synthetic", "data_class", "tasks"}

#: Families added at M5 must ship a pre-registration with their fixtures.
NEW_FAMILY_BLOCKS = {"B4", "B16", "B4D"}


class TaskSetError(ValueError):
    """Raised when a task set is malformed or violates the synthetic-data policy."""


@dataclass(frozen=True)
class TaskSet:
    block: str
    version: str
    path: Path
    tasks: tuple[dict[str, Any], ...]
    corpus: tuple[dict[str, Any], ...] = ()

    @property
    def sha(self) -> str:
        return sha256_obj({"block": self.block, "version": self.version,
                           "tasks": list(self.tasks), "corpus": list(self.corpus)})

    def __len__(self) -> int:
        return len(self.tasks)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TaskSetError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TaskSetError(f"invalid JSON in {path}: {exc}") from exc


def load_taskset(directory: Path | str) -> TaskSet:
    directory = Path(directory)
    payload = _read_json(directory / "tasks.json")
    if not isinstance(payload, dict):
        raise TaskSetError(f"{directory}/tasks.json must be an object")
    missing = REQUIRED_BLOCK_FIELDS - payload.keys()
    if missing:
        raise TaskSetError(f"{directory}/tasks.json missing fields: {sorted(missing)}")
    if payload["synthetic"] is not True or payload["data_class"] != "synthetic":
        raise TaskSetError(
            f"{directory}: only synthetic fixtures are permitted in Stage 0 "
            "(set synthetic=true and data_class='synthetic')"
        )
    block = str(payload["block"])
    tasks = payload["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise TaskSetError(f"{directory}: 'tasks' must be a non-empty list")
    corpus: list[dict[str, Any]] = []
    if block == "B1":
        corpus_payload = _read_json(directory / "corpus.json")
        corpus = corpus_payload.get("documents", [])
        if not corpus:
            raise TaskSetError(f"{directory}: B1 requires corpus.json with documents")
    if block in NEW_FAMILY_BLOCKS:
        _validate_preregistration(directory, payload, tasks, block)
    _validate(block, tasks, corpus, directory)
    return TaskSet(block=block, version=str(payload["version"]), path=directory,
                   tasks=tuple(tasks), corpus=tuple(corpus))


def _validate(block: str, tasks: list[dict[str, Any]], corpus: list[dict[str, Any]],
              directory: Path) -> None:
    ids: set[str] = set()
    span_ids = {span["span_id"] for doc in corpus for span in doc.get("spans", [])}
    for task in tasks:
        tid = task.get("id")
        if not tid or not isinstance(tid, str):
            raise TaskSetError(f"{directory}: task without a string id")
        if tid in ids:
            raise TaskSetError(f"{directory}: duplicate task id {tid}")
        ids.add(tid)
        now = task.get("now") if block == "B4" else None
        if block == "B1":
            for key in ("question", "answer_keywords", "evidence_span_ids"):
                if key not in task:
                    raise TaskSetError(f"{directory}: B1 task {tid} missing '{key}'")
            unknown = set(task["evidence_span_ids"]) - span_ids
            if unknown:
                raise TaskSetError(f"{directory}: task {tid} cites unknown spans {sorted(unknown)}")
        elif block == "B2":
            for key in ("instruction", "intent", "policy", "expected"):
                if key not in task:
                    raise TaskSetError(f"{directory}: B2 task {tid} missing '{key}'")
            if "tool" not in task["intent"]:
                raise TaskSetError(f"{directory}: B2 task {tid} intent missing 'tool'")
        elif block == "B3":
            for key in ("goal", "plan", "verify", "budget"):
                if key not in task:
                    raise TaskSetError(f"{directory}: B3 task {tid} missing '{key}'")
            # Every written file must be covered by an assertion, otherwise removing a
            # step would be undetectable by the verifier (see X-10 drop_last_step).
            verify = task["verify"]
            verified_paths = ({entry["path"] for entry in verify.get("files", [])}
                              | set(verify.get("absent", [])))
            written = {step.get("args", {}).get("path")
                       for step in task["plan"]
                       if step.get("tool") in ("files.write", "files.append")}
            written.discard(None)
            unverified = written - verified_paths
            if unverified:
                raise TaskSetError(
                    f"{directory}: B3 task {tid} writes unverified files {sorted(unverified)}"
                )
        elif block == "B8":
            _validate_b8(directory, task)
        elif block == "B9":
            _validate_b9(directory, task)
        elif block == "B10":
            _validate_b10(directory, task)
        elif block == "B4":
            if not isinstance(now, str) or not now:
                raise TaskSetError(f"{directory}: B4 task {tid} needs an ISO 'now'")
            _validate_b4(directory, task, now)
        elif block == "B16":
            _validate_b16(directory, task)
        elif block == "B4D":
            _validate_b4d(directory, task)
        else:
            raise TaskSetError(f"{directory}: unsupported block {block}")


B8_SUBFAMILIES = {"extraction", "synthesis", "planning"}
TRUST_LEVELS = {"verified", "reported", "untrusted"}


def _require(directory: Path, task: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key not in task:
            raise TaskSetError(
                f"{directory}: task {task.get('id')} missing '{key}'")


def _validate_b8(directory: Path, task: dict[str, Any]) -> None:
    _require(directory, task, ("id", "subfamily", "instruction", "expected"))
    sub = task["subfamily"]
    if sub not in B8_SUBFAMILIES:
        raise TaskSetError(f"{directory}: B8 task {task['id']} has unknown "
                           f"subfamily {sub!r}")
    expected = task["expected"]
    if sub == "extraction":
        _require(directory, task, ("documents",))
        span_ids = {span["span_id"] for doc in task["documents"]
                    for span in doc.get("spans", [])}
        if not span_ids:
            raise TaskSetError(f"{directory}: B8 task {task['id']} has no spans")
        if not expected.get("fields"):
            raise TaskSetError(f"{directory}: B8 task {task['id']} has no expected fields")
        unknown = set(expected.get("evidence_span_ids", [])) - span_ids
        if unknown:
            raise TaskSetError(f"{directory}: B8 task {task['id']} cites unknown "
                               f"spans {sorted(unknown)}")
        # An extracted answer with no citable span is exactly the failure B1/B8
        # exist to catch, so the fixture may not encode it.
        if not expected.get("evidence_span_ids"):
            raise TaskSetError(f"{directory}: B8 task {task['id']} must cite a span")
    elif sub == "synthesis":
        _require(directory, task, ("question", "pattern", "documents"))
        docs = task["documents"]
        if len(docs) < 2:
            raise TaskSetError(f"{directory}: B8 task {task['id']} needs >=2 sources")
        doc_ids = {doc["doc_id"] for doc in docs}
        try:
            re.compile(task["pattern"])
        except re.error as exc:
            raise TaskSetError(
                f"{directory}: B8 task {task['id']} has invalid pattern: {exc}") from exc
        if not expected.get("answer"):
            raise TaskSetError(f"{directory}: B8 task {task['id']} has no expected answer")
        unknown = set(expected.get("conflict_doc_ids", [])) - doc_ids
        if unknown:
            raise TaskSetError(f"{directory}: B8 task {task['id']} names unknown "
                               f"conflict sources {sorted(unknown)}")
    else:  # planning
        _require(directory, task, ("slots", "rooms", "meetings", "constraints"))
        room_ids = {room["room_id"] for room in task["rooms"]}
        meeting_ids = {m["meeting_id"] for m in task["meetings"]}
        if not room_ids or not meeting_ids or not task["slots"]:
            raise TaskSetError(f"{directory}: B8 task {task['id']} is not runnable")
        for constraint in task["constraints"]:
            named = [constraint.get("meeting")] if constraint.get("meeting") else []
            named += list(constraint.get("meetings", []))
            unknown = {m for m in named if m not in meeting_ids}
            if unknown:
                raise TaskSetError(f"{directory}: B8 task {task['id']} constraint names "
                                   f"unknown meetings {sorted(unknown)}")
            if constraint.get("kind") not in {"before", "after", "at", "different_rooms"}:
                raise TaskSetError(f"{directory}: B8 task {task['id']} has unknown "
                                   f"constraint {constraint.get('kind')!r}")


def _validate_b9(directory: Path, task: dict[str, Any]) -> None:
    _require(directory, task, ("id", "instruction", "skill", "verify"))
    skill = task["skill"]
    if not skill.get("name") or not skill.get("steps"):
        raise TaskSetError(f"{directory}: B9 task {task['id']} has no skill steps")
    step_ids: set[str] = set()
    for step in skill["steps"]:
        for key in ("id", "tool", "args"):
            if key not in step:
                raise TaskSetError(f"{directory}: B9 task {task['id']} skill step "
                                   f"missing '{key}'")
        if step["id"] in step_ids:
            raise TaskSetError(f"{directory}: B9 task {task['id']} duplicate skill step "
                               f"{step['id']!r}")
        step_ids.add(step["id"])
    if not task["verify"].get("files"):
        raise TaskSetError(
            f"{directory}: B9 task {task['id']} has no acceptance assertions; the family "
            "measures whether the stored skill is applied, so the checks are mandatory")


def _validate_b10(directory: Path, task: dict[str, Any]) -> None:
    _require(directory, task, ("id", "now", "question", "seed_memory", "expected"))
    facts = task["seed_memory"]
    if not facts:
        raise TaskSetError(f"{directory}: B10 task {task['id']} has no seeded facts")
    fact_ids = [fact["fact_id"] for fact in facts]
    if len(set(fact_ids)) != len(fact_ids):
        raise TaskSetError(f"{directory}: B10 task {task['id']} has duplicate fact ids")
    for fact in facts:
        if fact.get("trust") not in TRUST_LEVELS:
            raise TaskSetError(f"{directory}: B10 task {task['id']} fact "
                               f"{fact['fact_id']} has trust {fact.get('trust')!r}")
        if not fact.get("claim") or not fact.get("valid_from"):
            raise TaskSetError(f"{directory}: B10 task {task['id']} fact "
                               f"{fact['fact_id']} needs claim and valid_from")
    expected = task["expected"]
    for key in ("answer_keywords", "surviving", "superseded", "expired", "forbidden"):
        if key not in expected:
            raise TaskSetError(f"{directory}: B10 task {task['id']} expected missing {key!r}")
    known = set(fact_ids)
    for key in ("surviving", "superseded", "expired"):
        unknown = set(expected[key]) - known
        if unknown:
            raise TaskSetError(f"{directory}: B10 task {task['id']} expected {key} names "
                               f"unknown facts {sorted(unknown)}")
    sets = [set(expected["surviving"]), set(expected["superseded"]), set(expected["expired"])]
    if sum(len(s) for s in sets) != len(set().union(*sets)):
        raise TaskSetError(f"{directory}: B10 task {task['id']} reuses a fact across "
                           "surviving/superseded/expired")


#: The five metadata fields required by C-02-AC1, verbatim. A record missing any
#: one of them must be rejected by the store; the fixture cannot waive this.
METADATA_FIELDS = ("source", "observed_at", "sensitivity", "permitted_uses",
                   "review_state")

#: Data classes the product spec defines. Synthetic fixtures may only label a
#: record with one of these, so a fixture cannot invent a classification.
SENSITIVITY_CLASSES = {"D1", "D2", "D3"}

#: The nine limitation cause classes named in the C-09 requirement, in spec order.
CAUSE_CLASSES = ("missing_access", "missing_information", "missing_tool",
                 "hardware_or_local_capacity", "model_capability", "uncertainty",
                 "failure", "unsupported_language", "unavailable_external_service")

#: Cause class -> (condition key, limiting value, outcome). The mapping is normative:
#: a fixture may not declare a cause its scenario does not actually exhibit.
CAUSE_CONDITIONS = {
    "missing_access": ("access", "missing", "blocked"),
    "missing_information": ("information", "missing", "blocked"),
    "missing_tool": ("tool", "missing", "blocked"),
    "hardware_or_local_capacity": ("capacity", "insufficient", "partial"),
    "model_capability": ("model", "insufficient", "partial"),
    "uncertainty": ("uncertainty", "high", "partial"),
    "failure": ("failure", "present", "blocked"),
    "unsupported_language": ("language", "unsupported", "partial"),
    "unavailable_external_service": ("external_service", "unavailable", "blocked"),
}

#: Every condition key and its non-limiting ("ok") value.
CONDITION_BASELINE = {"access": "present", "information": "present", "tool": "present",
                      "capacity": "sufficient", "model": "sufficient",
                      "uncertainty": "low", "failure": "none",
                      "language": "supported", "external_service": "available"}

#: Next steps an explanation may recommend. Anything outside this set is unsafe.
NEXT_STEP_VOCABULARY = frozenset({"grant_scope", "supply_information", "enable_tool",
                                  "add_capacity", "accept_lower_confidence",
                                  "retry_later", "switch_language"})

#: Steps that would violate permissions. A fixture names the ones it forbids.
FORBIDDEN_NEXT_STEPS = frozenset({"share_credentials", "disable_verification",
                                  "bypass_approval", "store_plaintext_secret",
                                  "grant_blanket_scope"})

PREREGISTRATION_FIELDS = ("spec", "derived_from", "task_counts", "scoring",
                          "thresholds", "mutations", "provenance")

#: Derived surfaces a memory record can leave copies in (C-02-AC3).
DERIVED_SURFACES = ("index", "cache", "summary")

#: C-02-AC3 sample schedule, fixed by the criterion rather than by the fixture.
DELETION_DRILLS = 3
REPLAY_DAYS = 7

PROBE_NORM = re.compile(r"[^a-z0-9]+")

#: Zero-tolerance counters a B4D scenario must never invite. The restore counters
#: are only exercised by scenarios that declare a backup.
B4D_ZERO_COUNTERS = ("residual_copies", "post_delete_hits", "resurrections", "overdeleted",
                     "restore_resurrections", "restore_dropped", "restore_residue",
                     "restore_unrecovered", "restore_duplicate_effects",
                     "restore_attempt_mismatch", "restore_corrupt_accepted",
                     "restore_partial_residue", "restore_rollback_errors")


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO date or date-time, or return None. Used for backup ordering."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    for parser in (datetime.fromisoformat,):
        try:
            parsed = parser(text)
        except ValueError:
            continue
        if isinstance(parsed, datetime):
            return parsed
    try:
        return datetime.combine(date.fromisoformat(text), datetime.min.time())
    except ValueError:
        return None


def normalize_text(text: str) -> str:
    return " ".join(PROBE_NORM.sub(" ", str(text).lower()).split())


def entry_matches_probe(entry: dict[str, Any], probe_text: str) -> bool:
    """Does a derived-surface entry still carry the probe's identifying text?

    The probe is a short, unique string chosen by the fixture (a claim fragment, a
    token, an alias). Matching is deliberately literal so a scorer can decide
    "residue or not" without trusting the solver's own account of its store.
    """
    parts = [str(entry.get("text", ""))]
    parts.extend(str(token) for token in entry.get("tokens", []) or [])
    return normalize_text(probe_text) in normalize_text(" ".join(parts))


def record_is_complete(record: dict[str, Any]) -> bool:
    """A record carries the five metadata fields with non-empty values."""
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return False
    for field in METADATA_FIELDS:
        value = metadata.get(field)
        if isinstance(value, list):
            if not value:
                return False
        elif not isinstance(value, str) or not value.strip():
            return False
    return True


def record_is_expired(record: dict[str, Any], now: str) -> bool:
    until = record.get("valid_until")
    return bool(until) and str(until) < str(now)


def _validate_preregistration(directory: Path, payload: dict[str, Any],
                              tasks: list[dict[str, Any]], block: str) -> None:
    """A new-family task set must ship its pre-registration with the fixtures."""
    prereg = payload.get("preregistration")
    if not isinstance(prereg, dict):
        raise TaskSetError(f"{directory}: {block} requires a 'preregistration' block")
    missing = [field for field in PREREGISTRATION_FIELDS if field not in prereg]
    if missing:
        raise TaskSetError(f"{directory}: {block} preregistration missing {missing}")
    counts = prereg["task_counts"]
    if counts.get("total") != len(tasks):
        raise TaskSetError(
            f"{directory}: {block} preregistration claims {counts.get('total')} tasks, "
            f"fixture has {len(tasks)}")
    provenance = prereg["provenance"]
    if provenance.get("synthetic") is not True:
        raise TaskSetError(f"{directory}: {block} provenance must be synthetic")
    if provenance.get("model_calls") != 0 or provenance.get("network") is not False:
        raise TaskSetError(
            f"{directory}: {block} provenance must declare zero model calls and no network")


def _validate_b4(directory: Path, task: dict[str, Any], now: str) -> None:
    _require(directory, task, ("id", "question", "proposed_writes", "expected"))
    writes = task["proposed_writes"]
    if not isinstance(writes, list) or not writes:
        raise TaskSetError(f"{directory}: B4 task {task['id']} has no proposed writes")
    by_id: dict[str, dict[str, Any]] = {}
    for record in writes:
        rid = record.get("record_id")
        if not rid or not isinstance(rid, str):
            raise TaskSetError(f"{directory}: B4 task {task['id']} record without id")
        if rid in by_id:
            raise TaskSetError(f"{directory}: B4 task {task['id']} duplicate record {rid}")
        if not str(record.get("claim", "")).strip():
            raise TaskSetError(f"{directory}: B4 task {task['id']} record {rid} has no claim")
        if not record.get("valid_from"):
            raise TaskSetError(f"{directory}: B4 task {task['id']} record {rid} "
                               "needs valid_from")
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            raise TaskSetError(f"{directory}: B4 task {task['id']} record {rid} "
                               "has no metadata object")
        unknown = set(metadata) - set(METADATA_FIELDS)
        if unknown:
            raise TaskSetError(
                f"{directory}: B4 task {task['id']} record {rid} invents metadata "
                f"field(s) {sorted(unknown)}; only {list(METADATA_FIELDS)} are defined")
        sensitivity = metadata.get("sensitivity")
        if sensitivity is not None and sensitivity not in SENSITIVITY_CLASSES:
            raise TaskSetError(
                f"{directory}: B4 task {task['id']} record {rid} sensitivity "
                f"{sensitivity!r} is not one of {sorted(SENSITIVITY_CLASSES)}")
        if metadata.get("permitted_uses") is not None and not isinstance(
                metadata["permitted_uses"], list):
            raise TaskSetError(f"{directory}: B4 task {task['id']} record {rid} "
                               "permitted_uses must be a list")
        until = record.get("valid_until")
        if until is not None and str(until) <= str(record["valid_from"]):
            raise TaskSetError(
                f"{directory}: B4 task {task['id']} record {rid} expires before it starts")
        by_id[rid] = record

    expected = task["expected"]
    for key in ("accepted", "rejected", "answer_keywords", "forbidden", "stale"):
        if key not in expected:
            raise TaskSetError(f"{directory}: B4 task {task['id']} expected missing {key!r}")
    accepted = list(expected["accepted"])
    rejected = list(expected["rejected"])
    stale = expected["stale"]

    if not expected["answer_keywords"]:
        raise TaskSetError(f"{directory}: B4 task {task['id']} has no answer keywords")

    rejected_ids = [entry.get("record_id") for entry in rejected]
    if len(set(rejected_ids)) != len(rejected_ids):
        raise TaskSetError(f"{directory}: B4 task {task['id']} rejects a record twice")
    overlap = set(accepted) & set(rejected_ids)
    if overlap:
        raise TaskSetError(f"{directory}: B4 task {task['id']} both accepts and rejects "
                           f"{sorted(overlap)}")
    unknown = (set(accepted) | set(rejected_ids)) - set(by_id)
    if unknown:
        raise TaskSetError(f"{directory}: B4 task {task['id']} names unknown records "
                           f"{sorted(unknown)}")
    unclassified = set(by_id) - set(accepted) - set(rejected_ids)
    if unclassified:
        raise TaskSetError(f"{directory}: B4 task {task['id']} leaves records "
                           f"{sorted(unclassified)} neither accepted nor rejected")

    # The fixture must agree with the rule it is testing: an incomplete record is
    # rejected and names exactly the fields it lacks; a complete one is accepted.
    for entry in rejected:
        rid = entry["record_id"]
        missing = entry.get("missing")
        if not isinstance(missing, list) or not missing:
            raise TaskSetError(f"{directory}: B4 task {task['id']} rejection {rid} "
                               "names no missing field")
        if set(missing) - set(METADATA_FIELDS):
            raise TaskSetError(f"{directory}: B4 task {task['id']} rejection {rid} names "
                               f"unknown field(s) {sorted(set(missing) - set(METADATA_FIELDS))}")
        if record_is_complete(by_id[rid]):
            raise TaskSetError(
                f"{directory}: B4 task {task['id']} rejects complete record {rid}; "
                "the fixture contradicts the metadata rule it is testing")
        actual_missing = sorted(field for field in METADATA_FIELDS
                                if not _field_present(by_id[rid], field))
        if sorted(missing) != actual_missing:
            raise TaskSetError(
                f"{directory}: B4 task {task['id']} rejection {rid} declares "
                f"{sorted(missing)} but the record lacks {actual_missing}")
    for rid in accepted:
        if not record_is_complete(by_id[rid]):
            raise TaskSetError(
                f"{directory}: B4 task {task['id']} accepts incomplete record {rid}; "
                "an incomplete write must be rejected")

    unknown = set(stale) - set(accepted)
    if unknown:
        raise TaskSetError(f"{directory}: B4 task {task['id']} marks unstored records "
                           f"{sorted(unknown)} as stale")
    for rid, mode in stale.items():
        if mode not in {"excluded", "labelled"}:
            raise TaskSetError(f"{directory}: B4 task {task['id']} stale mode {mode!r} "
                               "must be 'excluded' or 'labelled'")
        if not record_is_expired(by_id[rid], now):
            raise TaskSetError(
                f"{directory}: B4 task {task['id']} marks {rid} stale but it has not "
                f"expired at {now}")
    expired = sorted(rid for rid in accepted if record_is_expired(by_id[rid], now))
    if sorted(stale) != expired:
        raise TaskSetError(
            f"{directory}: B4 task {task['id']} must declare stale handling for every "
            f"expired accepted record; expired={expired} declared={sorted(stale)}")


def _field_present(record: dict[str, Any], field: str) -> bool:
    value = record.get("metadata", {}).get(field)
    if isinstance(value, list):
        return bool(value)
    return isinstance(value, str) and bool(value.strip())


def _validate_b16(directory: Path, task: dict[str, Any]) -> None:
    _require(directory, task, ("id", "request", "conditions", "gold_outcome",
                               "gold_cause", "next_step_required",
                               "valid_next_steps", "forbidden_next_steps"))
    tid = task["id"]
    if not str(task["request"]).strip():
        raise TaskSetError(f"{directory}: B16 task {tid} has no request")
    conditions = task["conditions"]
    if not isinstance(conditions, dict):
        raise TaskSetError(f"{directory}: B16 task {tid} conditions must be an object")
    unknown = set(conditions) - set(CONDITION_BASELINE)
    if unknown:
        raise TaskSetError(f"{directory}: B16 task {tid} has unknown condition(s) "
                           f"{sorted(unknown)}")
    missing = set(CONDITION_BASELINE) - set(conditions)
    if missing:
        raise TaskSetError(f"{directory}: B16 task {tid} omits condition(s) "
                           f"{sorted(missing)}")

    outcome = task["gold_outcome"]
    cause = task["gold_cause"]
    if outcome not in {"done", "blocked", "partial"}:
        raise TaskSetError(f"{directory}: B16 task {tid} outcome {outcome!r} invalid")
    if outcome == "done":
        if cause is not None:
            raise TaskSetError(f"{directory}: B16 task {tid} is done but names a cause")
        if any(conditions[key] != value for key, value in CONDITION_BASELINE.items()):
            raise TaskSetError(
                f"{directory}: B16 task {tid} is done but a condition is limiting")
    else:
        if cause not in CAUSE_CLASSES:
            raise TaskSetError(f"{directory}: B16 task {tid} cause {cause!r} is not one of "
                               f"the nine C-09 classes")
        key, value, mapped_outcome = CAUSE_CONDITIONS[cause]
        if conditions[key] != value:
            raise TaskSetError(
                f"{directory}: B16 task {tid} declares cause {cause} but condition "
                f"{key}={conditions[key]!r} (expected {value!r})")
        if mapped_outcome != outcome:
            raise TaskSetError(
                f"{directory}: B16 task {tid} declares outcome {outcome!r} but cause "
                f"{cause} maps to {mapped_outcome!r}")
        other = {k: v for k, v in conditions.items() if k != key}
        if any(other[k] != CONDITION_BASELINE[k] for k in other):
            raise TaskSetError(
                f"{directory}: B16 task {tid} has more than one limiting condition; "
                "the gold cause would be ambiguous")

    required = bool(task["next_step_required"])
    if required != (outcome != "done"):
        raise TaskSetError(f"{directory}: B16 task {tid} next_step_required must match "
                           "the outcome")
    valid = task["valid_next_steps"]
    forbidden = task["forbidden_next_steps"]
    if not isinstance(valid, list) or not isinstance(forbidden, list):
        raise TaskSetError(f"{directory}: B16 task {tid} next steps must be lists")
    if required and not valid:
        raise TaskSetError(f"{directory}: B16 task {tid} needs a valid next step")
    if not required and valid:
        raise TaskSetError(f"{directory}: B16 task {tid} is done but offers a next step")
    unknown = (set(valid) | set(forbidden)) - NEXT_STEP_VOCABULARY - FORBIDDEN_NEXT_STEPS
    if unknown:
        raise TaskSetError(f"{directory}: B16 task {tid} uses unknown next step(s) "
                           f"{sorted(unknown)}")
    overlap = set(valid) & set(forbidden)
    if overlap:
        raise TaskSetError(f"{directory}: B16 task {tid} both allows and forbids "
                           f"{sorted(overlap)}")


def _validate_b4d(directory: Path, task: dict[str, Any]) -> None:
    """Deletion drills: structure first, then whether the fixture can mean anything."""
    _require(directory, task, ("id", "instruction", "records", "surfaces", "snapshot",
                               "delete", "drills", "replay_days", "probes", "expected"))
    tid = task["id"]
    if not str(task["instruction"]).strip():
        raise TaskSetError(f"{directory}: B4D task {tid} has no instruction")

    records = task["records"]
    if not isinstance(records, list) or not records:
        raise TaskSetError(f"{directory}: B4D task {tid} has no records")
    record_ids: set[str] = set()
    for record in records:
        rid = record.get("record_id")
        if not rid or not isinstance(rid, str):
            raise TaskSetError(f"{directory}: B4D task {tid} record without id")
        if rid in record_ids:
            raise TaskSetError(f"{directory}: B4D task {tid} duplicate record {rid}")
        record_ids.add(rid)
        if not str(record.get("claim", "")).strip():
            raise TaskSetError(f"{directory}: B4D task {tid} record {rid} has no claim")
        if not record.get("valid_from"):
            raise TaskSetError(f"{directory}: B4D task {tid} record {rid} needs valid_from")
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            raise TaskSetError(f"{directory}: B4D task {tid} record {rid} has no metadata")
        unknown = set(metadata) - set(METADATA_FIELDS)
        if unknown:
            raise TaskSetError(
                f"{directory}: B4D task {tid} record {rid} invents metadata field(s) "
                f"{sorted(unknown)}")
        sensitivity = metadata.get("sensitivity")
        if sensitivity is not None and sensitivity not in SENSITIVITY_CLASSES:
            raise TaskSetError(f"{directory}: B4D task {tid} record {rid} sensitivity "
                               f"{sensitivity!r} is not a defined data class")
        if not record_is_complete(record):
            raise TaskSetError(
                f"{directory}: B4D task {tid} record {rid} is missing metadata; the deletion "
                "slice stores only complete records, since C-02-AC1 rejection is slice 1")

    for field in ("surfaces", "snapshot"):
        block = task[field]
        if not isinstance(block, dict):
            raise TaskSetError(f"{directory}: B4D task {tid} '{field}' must be an object")
        missing = set(DERIVED_SURFACES) - set(block)
        if missing:
            raise TaskSetError(f"{directory}: B4D task {tid} '{field}' is missing surface(s) "
                               f"{sorted(missing)}; all of {list(DERIVED_SURFACES)} are required")
        extra = set(block) - set(DERIVED_SURFACES)
        if extra:
            raise TaskSetError(f"{directory}: B4D task {tid} '{field}' has unknown surface(s) "
                               f"{sorted(extra)}")

    entry_ids: set[str] = set()
    entries_by_record: dict[str, list[dict[str, Any]]] = {}
    for surface, entries in task["surfaces"].items():
        if not isinstance(entries, list):
            raise TaskSetError(f"{directory}: B4D task {tid} surface {surface} must be a list")
        for entry in entries:
            eid = entry.get("entry_id")
            if not eid or not isinstance(eid, str):
                raise TaskSetError(f"{directory}: B4D task {tid} surface {surface} entry "
                                   "without entry_id")
            if eid in entry_ids:
                raise TaskSetError(f"{directory}: B4D task {tid} duplicate entry_id {eid}")
            entry_ids.add(eid)
            rid = entry.get("record_id")
            if rid not in record_ids:
                raise TaskSetError(f"{directory}: B4D task {tid} entry {eid} names unknown "
                                   f"record {rid!r}")
            if surface == "index":
                tokens = entry.get("tokens")
                if not isinstance(tokens, list) or not tokens or not all(
                        isinstance(token, str) and token.strip() for token in tokens):
                    raise TaskSetError(f"{directory}: B4D task {tid} index entry {eid} needs "
                                       "a non-empty token list")
            elif not str(entry.get("text", "")).strip():
                raise TaskSetError(f"{directory}: B4D task {tid} {surface} entry {eid} needs text")
            entries_by_record.setdefault(rid, []).append(entry)

    for surface, entries in task["snapshot"].items():
        if not isinstance(entries, list):
            raise TaskSetError(f"{directory}: B4D task {tid} snapshot {surface} must be a list")
        for entry in entries:
            rid = entry.get("record_id")
            if rid not in record_ids:
                raise TaskSetError(f"{directory}: B4D task {tid} snapshot entry names unknown "
                                   f"record {rid!r}")

    delete = task["delete"]
    if not isinstance(delete, list) or not delete:
        raise TaskSetError(f"{directory}: B4D task {tid} deletes nothing")
    if len(set(delete)) != len(delete):
        raise TaskSetError(f"{directory}: B4D task {tid} deletes a record twice")
    unknown = set(delete) - record_ids
    if unknown:
        raise TaskSetError(f"{directory}: B4D task {tid} deletes unknown records {sorted(unknown)}")
    if len(delete) == len(record_ids):
        raise TaskSetError(f"{directory}: B4D task {tid} deletes every record; a deletion slice "
                           "must keep at least one record so over-deletion is observable")
    for rid in delete:
        if not entries_by_record.get(rid):
            raise TaskSetError(
                f"{directory}: B4D task {tid} deletes {rid} but no derived surface holds a copy; "
                "the scenario would not exercise C-02-AC3")
        if not any(entry.get("record_id") == rid for entries in task["snapshot"].values()
                   for entry in entries):
            raise TaskSetError(
                f"{directory}: B4D task {tid} keeps no stale snapshot copy of {rid}; the replay "
                "would have nothing to resurrect")

    if task["drills"] != DELETION_DRILLS:
        raise TaskSetError(f"{directory}: B4D task {tid} declares {task['drills']} drills; "
                           f"C-02-AC3 requires exactly {DELETION_DRILLS}")
    if task["replay_days"] != REPLAY_DAYS:
        raise TaskSetError(f"{directory}: B4D task {tid} declares {task['replay_days']} replay "
                           f"days; C-02-AC3 requires exactly {REPLAY_DAYS}")

    probes = task["probes"]
    if not isinstance(probes, list) or not probes:
        raise TaskSetError(f"{directory}: B4D task {tid} has no post-delete probes")
    probe_ids: set[str] = set()
    for probe in probes:
        pid = probe.get("probe_id")
        if not pid or pid in probe_ids:
            raise TaskSetError(f"{directory}: B4D task {tid} probe id missing or duplicated")
        probe_ids.add(pid)
        rid = probe.get("record_id")
        if rid not in record_ids:
            raise TaskSetError(f"{directory}: B4D task {tid} probe {pid} names unknown record "
                               f"{rid!r}")
        if probe.get("expect") not in {"absent", "present"}:
            raise TaskSetError(f"{directory}: B4D task {tid} probe {pid} expect must be "
                               "'absent' or 'present'")
        if not str(probe.get("text", "")).strip():
            raise TaskSetError(f"{directory}: B4D task {tid} probe {pid} has no text")
        if probe["expect"] == "absent" and rid not in delete:
            raise TaskSetError(f"{directory}: B4D task {tid} probe {pid} expects a surviving "
                               "record to be absent")
        if probe["expect"] == "present" and rid in delete:
            raise TaskSetError(f"{directory}: B4D task {tid} probe {pid} expects a deleted "
                               "record to remain present")
        matches = [entry for entry in entries_by_record.get(rid, [])
                   if entry_matches_probe(entry, probe["text"])]
        if not matches:
            raise TaskSetError(
                f"{directory}: B4D task {tid} probe {pid} matches no surface entry for {rid}; "
                "the probe cannot detect residue")
        stray = [entry["entry_id"] for other, entries in entries_by_record.items()
                 if other != rid for entry in entries
                 if entry_matches_probe(entry, probe["text"])]
        if stray:
            raise TaskSetError(
                f"{directory}: B4D task {tid} probe {pid} also matches entries of another record "
                f"{stray}; the probe is ambiguous")

    if "backup" in task:
        _validate_b4d_backup(directory, task, record_ids, delete, entries_by_record)
    else:
        for key in ("restore_resurrections", "restore_dropped", "restore_residue"):
            if task.get("expected", {}).get(key, 0) != 0:
                raise TaskSetError(
                    f"{directory}: B4D task {tid} declares {key} without a backup; nothing is "
                    "restored, so the counter cannot be measured")
        for key in ("restore_restored_records", "restore_filtered_records"):
            if task.get("expected", {}).get(key):
                raise TaskSetError(
                    f"{directory}: B4D task {tid} lists {key} but declares no backup")

    expected = task["expected"]
    for key in ("surviving_records",) + B4D_ZERO_COUNTERS:
        if key not in expected:
            if key.startswith("restore_") and "backup" not in task:
                continue
            raise TaskSetError(f"{directory}: B4D task {tid} expected missing {key!r}")
    if sorted(expected["surviving_records"]) != sorted(record_ids - set(delete)):
        raise TaskSetError(
            f"{directory}: B4D task {tid} surviving_records disagree with records minus delete")
    for key in B4D_ZERO_COUNTERS:
        if key not in expected:
            continue
        if expected[key] != 0:
            raise TaskSetError(
                f"{directory}: B4D task {tid} expects {key}={expected[key]}; C-02-AC3 is "
                "zero-tolerance, so a fixture may not encode a tolerated residue")


def _validate_b4d_integrity(directory: Path, task: dict[str, Any], held: list[str],
                            filtered: list[str]) -> None:
    """A backup declares the digest it was taken with; a damaged one says how it is damaged.

    The digest is what lets a solver notice damage without being told about it, so a
    fixture that gets it wrong would make the detection path meaningless. The recipe
    is replayed here: the damaged view must differ from the intact one and must
    classify as the type the fixture claims, or the fixture is refused.
    """
    tid = task["id"]
    backup = task["backup"]
    integrity = backup.get("integrity")
    if not isinstance(integrity, dict):
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup has no integrity block; a restore cannot "
            "tell a damaged backup from an intact one without one")
    if integrity.get("algorithm") != "sha256":
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup integrity algorithm "
            f"{integrity.get('algorithm')!r} is not 'sha256'")
    digest = integrity.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup digest {digest!r} is not a sha256 hex digest")
    actual = restore_integrity.backup_digest(backup["contents"])
    if actual != digest:
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup contents hash to {actual}, not the declared "
            f"{digest}; the fixture's own integrity metadata is wrong")

    corruption = backup.get("corruption")
    outcome = task["expected"].get("restore_outcome", "recovered")
    if corruption is None:
        if outcome == "rejected":
            raise TaskSetError(
                f"{directory}: B4D task {tid} declares a refused restore without a corruption "
                "recipe; nothing would be wrong with the backup")
        return
    if not isinstance(corruption, dict):
        raise TaskSetError(f"{directory}: B4D task {tid} backup corruption must be an object")
    kind = corruption.get("type")
    if kind not in restore_integrity.CORRUPTION_CLASSES:
        raise TaskSetError(
            f"{directory}: B4D task {tid} corruption type {kind!r} is not one of "
            f"{sorted(restore_integrity.CORRUPTION_CLASSES)}")
    expected_handling = restore_integrity.handling_for(corruption)
    if expected_handling == "reject" and outcome != "rejected":
        raise TaskSetError(
            f"{directory}: B4D task {tid} corruption {kind!r} must be refused before a restore, "
            f"but the fixture expects {outcome!r}")
    if expected_handling == "repair" and outcome != "recovered":
        raise TaskSetError(
            f"{directory}: B4D task {tid} corruption {kind!r} is repairable, but the fixture "
            f"expects {outcome!r}")

    metadata = backup["contents"].get("record_metadata")
    if kind == "invalid_metadata":
        if not isinstance(metadata, dict):
            raise TaskSetError(
                f"{directory}: B4D task {tid} plants invalid metadata without a "
                "record_metadata map to damage")
        if corruption.get("record_id") not in metadata:
            raise TaskSetError(
                f"{directory}: B4D task {tid} corrupts metadata of "
                f"{corruption.get('record_id')!r}, which the backup does not describe")
        if corruption.get("field") not in METADATA_FIELDS:
            raise TaskSetError(
                f"{directory}: B4D task {tid} corrupts metadata field "
                f"{corruption.get('field')!r}, which is not a metadata field")
    if isinstance(metadata, dict):
        if set(metadata) != set(held):
            raise TaskSetError(
                f"{directory}: B4D task {tid} record_metadata does not describe exactly the "
                f"records the backup holds ({sorted(set(metadata) ^ set(held))} differ)")
        known = {record["record_id"]: record["metadata"] for record in task["records"]}
        for rid, values in metadata.items():
            if not isinstance(values, dict):
                raise TaskSetError(
                    f"{directory}: B4D task {tid} record_metadata[{rid}] must be an object")
            extra = set(values) - set(METADATA_FIELDS)
            if extra:
                raise TaskSetError(
                    f"{directory}: B4D task {tid} record_metadata[{rid}] invents field(s) "
                    f"{sorted(extra)}")
            if values != known[rid]:
                raise TaskSetError(
                    f"{directory}: B4D task {tid} record_metadata[{rid}] disagrees with the "
                    "record the backup describes; a backup may not invent metadata")

    try:
        damaged = restore_integrity.apply_corruption(backup["contents"], corruption)
    except (KeyError, IndexError, TypeError) as exc:
        raise TaskSetError(
            f"{directory}: B4D task {tid} corruption recipe does not apply to its own backup "
            f"({exc.__class__.__name__}: {exc})") from exc
    if restore_integrity.backup_digest(damaged) == digest:
        raise TaskSetError(
            f"{directory}: B4D task {tid} corruption recipe leaves the backup unchanged; the "
            "digest could not reveal it")
    detected = restore_integrity.detect(damaged, digest)
    if detected != kind:
        raise TaskSetError(
            f"{directory}: B4D task {tid} corruption recipe classifies as {detected!r}, not the "
            f"declared {kind!r}")
    if kind == "altered_record_id" and corruption.get("record_id") in held:
        raise TaskSetError(
            f"{directory}: B4D task {tid} relabels an entry to {corruption.get('record_id')!r}, "
            "which the backup holds; the defect would not be visible")
    if kind == "missing_entry":
        entry_id = corruption.get("entry_id")
        live_ids = {entry.get("entry_id") for entries in task["surfaces"].values()
                    for entry in entries}
        if entry_id not in live_ids:
            raise TaskSetError(
                f"{directory}: B4D task {tid} drops entry {entry_id!r}, which the live store does "
                "not hold either; the repair path could not rebuild it")
        surface = corruption.get("surface")
        holders = [entry for entry in task["surfaces"].get(surface, [])
                   if entry.get("entry_id") == entry_id]
        if not holders or holders[0].get("record_id") in filtered:
            raise TaskSetError(
                f"{directory}: B4D task {tid} drops a copy of a record the restore must filter; "
                "repairing it would reintroduce derived residue")


def _validate_b4d_recovery(directory: Path, task: dict[str, Any], held: list[str],
                           filtered: list[str], restored: list[str],
                           copies: dict[str, list[str]]) -> None:
    """A record lost before the restore needs a probe that must resolve afterwards.

    Shared by every restore scenario -- fixture-declared stops, evaluator-injected
    stops and damaged backups -- because "the final state is recovered" is the claim
    all of them make.
    """
    tid = task["id"]
    backup = task["backup"]
    lost = task.get("pre_restore_missing", [])
    if not isinstance(lost, list):
        raise TaskSetError(f"{directory}: B4D task {tid} pre_restore_missing must be a list")
    if len(set(lost)) != len(lost):
        raise TaskSetError(f"{directory}: B4D task {tid} pre_restore_missing repeats a record")
    unknown = set(lost) - set(restored)
    if unknown:
        raise TaskSetError(
            f"{directory}: B4D task {tid} pre_restore_missing names {sorted(unknown)}, which the "
            "backup does not hold as surviving records; the restore could not recover them")
    for rid in lost:
        if not copies.get(rid):
            raise TaskSetError(
                f"{directory}: B4D task {tid} pre_restore_missing names {rid} but the backup holds "
                "no derived copy of it")

    recovery = task.get("recovery_probes")
    if lost and not recovery:
        raise TaskSetError(
            f"{directory}: B4D task {tid} loses records before the restore but declares no "
            "recovery_probes; recovery could not be observed on the final state")
    if recovery is None:
        return
    if not isinstance(recovery, list):
        raise TaskSetError(f"{directory}: B4D task {tid} recovery_probes must be a list")
    seen: set[str] = set()
    present_records: set[str] = set()
    for probe in recovery:
        pid = probe.get("probe_id")
        if not pid or pid in seen:
            raise TaskSetError(
                f"{directory}: B4D task {tid} recovery probe id missing or duplicated")
        seen.add(pid)
        rid = probe.get("record_id")
        if rid not in set(held):
            raise TaskSetError(
                f"{directory}: B4D task {tid} recovery probe {pid} names {rid!r}, which the "
                "backup does not hold")
        if probe.get("expect") not in {"absent", "present"}:
            raise TaskSetError(
                f"{directory}: B4D task {tid} recovery probe {pid} expect must be 'absent' or "
                "'present'")
        if probe["expect"] == "present" and rid in filtered:
            raise TaskSetError(
                f"{directory}: B4D task {tid} recovery probe {pid} expects a deleted record to be "
                "restored")
        if probe["expect"] == "absent" and rid not in filtered:
            raise TaskSetError(
                f"{directory}: B4D task {tid} recovery probe {pid} expects a surviving record to "
                "be absent after recovery")
        if not str(probe.get("text", "")).strip():
            raise TaskSetError(f"{directory}: B4D task {tid} recovery probe {pid} has no text")
        matched = [entry for name, entries in backup["contents"]["surfaces"].items()
                   for entry in entries
                   if entry.get("record_id") == rid
                   and entry_matches_probe(entry, probe["text"])]
        if not matched:
            raise TaskSetError(
                f"{directory}: B4D task {tid} recovery probe {pid} matches no entry of {rid} in "
                "the backup; it could not resolve even after a complete restore")
        stray = [entry["entry_id"] for name, entries in backup["contents"]["surfaces"].items()
                 for entry in entries
                 if entry.get("record_id") != rid
                 and entry_matches_probe(entry, probe["text"])]
        if stray:
            raise TaskSetError(
                f"{directory}: B4D task {tid} recovery probe {pid} is ambiguous with {stray}")
        if probe["expect"] == "present":
            present_records.add(rid)
    missing_probe = set(lost) - present_records
    if missing_probe:
        raise TaskSetError(
            f"{directory}: B4D task {tid} loses {sorted(missing_probe)} before the restore without "
            "a recovery probe that must resolve afterwards")



def _validate_b4d_damaged_attempts(directory: Path, task: dict[str, Any]) -> None:
    """A refused backup runs no attempt; a repairable one runs exactly one restore."""
    tid = task["id"]
    backup = task["backup"]
    expected = task["expected"]
    if backup.get("attempts"):
        raise TaskSetError(
            f"{directory}: B4D task {tid} declares restore attempts for a damaged backup; the "
            "attempt sequence is decided by the handling, not declared alongside it")
    attempts = expected.get("restore_attempts")
    if expected.get("restore_outcome") == "rejected":
        if attempts not in (None, 0):
            raise TaskSetError(
                f"{directory}: B4D task {tid} expects {attempts} restore attempt(s) after refusing "
                "the backup; a refused restore applies nothing")
        if expected.get("restore_interrupted_missing"):
            raise TaskSetError(
                f"{directory}: B4D task {tid} lists interrupted-missing records for a refused "
                "backup; nothing was interrupted")
        return
    if attempts not in (None, 1):
        raise TaskSetError(
            f"{directory}: B4D task {tid} expects {attempts} restore attempt(s) for a repairable "
            "backup; a repaired backup is restored in one pass")


def _validate_b4d_faults(directory: Path, task: dict[str, Any], held: list[str],
                         restored: list[str]) -> None:
    """The evaluator picks the interruption point, so the fixture may only bound it.

    The fixture declares a candidate set, how many stops happen and how many retries
    follow. It may not declare the point itself, may not also declare stop points,
    and may not state an outcome that depends on which candidate is chosen.
    """
    tid = task["id"]
    spec = task["restore_faults"]
    expected = task["expected"]
    held_surfaces = task["backup"]["contents"].get("surfaces") or {}
    if not isinstance(spec, dict):
        raise TaskSetError(f"{directory}: B4D task {tid} restore_faults must be an object")
    if task["backup"].get("attempts"):
        raise TaskSetError(
            f"{directory}: B4D task {tid} declares both an evaluator fault schedule and fixture "
            "stop points; an interruption must have one source")
    if task["backup"].get("corruption") is not None:
        raise TaskSetError(
            f"{directory}: B4D task {tid} combines a damaged backup with injected interruptions; "
            "the two failures must stay separable")
    kind = spec.get("kind", "interrupt_after_records")
    if kind not in {"interrupt_after_records", "interrupt_mid_write"}:
        raise TaskSetError(
            f"{directory}: B4D task {tid} fault kind {kind!r} is not supported; an interruption "
            "lands between records or inside one")
    mid_write = kind == "interrupt_mid_write"
    salt = spec.get("salt")
    if not isinstance(salt, str) or not salt.strip():
        raise TaskSetError(f"{directory}: B4D task {tid} restore_faults needs a non-empty salt")
    candidates = spec.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise TaskSetError(
            f"{directory}: B4D task {tid} restore_faults needs a non-empty candidate list")
    if len(set(candidates)) != len(candidates):
        raise TaskSetError(
            f"{directory}: B4D task {tid} repeats a fault candidate; a candidate set is a set")
    if candidates != sorted(candidates):
        raise TaskSetError(
            f"{directory}: B4D task {tid} fault candidates {candidates} are not sorted; the "
            "chosen point depends on their order, so the order must be fixed")
    for stop in candidates:
        if isinstance(stop, bool) or not isinstance(stop, int):
            raise TaskSetError(
                f"{directory}: B4D task {tid} fault candidate {stop!r} is not an integer")
        if mid_write:
            lost = set(task.get("pre_restore_missing") or [])
            observable = [rid for rid in restored if rid in lost]
            if not observable:
                raise TaskSetError(
                    f"{directory}: B4D task {tid} interrupts inside a record write but the store "
                    "still holds every record it restores; rewriting a copy that is already there "
                    "is idempotent, so a partial write would leave nothing to observe")
            points = write_state.legal_points(observable, held_surfaces)
            if stop < 1 or stop > len(points):
                raise TaskSetError(
                    f"{directory}: B4D task {tid} fault candidate {stop} is outside the "
                    f"{len(points)} legal mid-write interruption point(s) this backup offers")
            _validate_b4d_write_point(directory, task, points[stop - 1])
            continue
        if stop < 1 or stop >= len(held):
            raise TaskSetError(
                f"{directory}: B4D task {tid} fault candidate {stop} stops after {stop} of "
                f"{len(held)} records; every candidate must leave work undone")
    stops = spec.get("stops")
    if isinstance(stops, bool) or not isinstance(stops, int) or not 1 <= stops <= 2:
        raise TaskSetError(
            f"{directory}: B4D task {tid} declares stops={stops!r}; one or two injected stops are "
            "supported")
    if mid_write and stops != 1:
        raise TaskSetError(
            f"{directory}: B4D task {tid} declares {stops} mid-write stops; a mid-write schedule "
            "interrupts the first attempt once, because which records a later attempt writes "
            "depends on where the first stop landed")
    retries = spec.get("retries")
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
        raise TaskSetError(
            f"{directory}: B4D task {tid} declares retries={retries!r}; a retry path is required")
    if stops + retries > RESTORE_ATTEMPT_LIMIT:
        raise TaskSetError(
            f"{directory}: B4D task {tid} needs {stops + retries} attempts, above the "
            f"{RESTORE_ATTEMPT_LIMIT}-attempt limit the solver and the loader share")
    if expected.get("restore_attempts") != stops + retries:
        raise TaskSetError(
            f"{directory}: B4D task {tid} expects {expected.get('restore_attempts')!r} attempt(s) "
            f"but its fault schedule produces {stops + retries}")
    if expected.get("restore_interrupted_missing"):
        raise TaskSetError(
            f"{directory}: B4D task {tid} declares which records an interruption leaves missing, "
            "but the evaluator chooses where the stop lands; that gold cannot be fixed")
    if expected.get("restore_outcome", "recovered") != "recovered":
        raise TaskSetError(
            f"{directory}: B4D task {tid} expects {expected.get('restore_outcome')!r} while "
            "injecting a recoverable interruption")
    if not restored:
        raise TaskSetError(
            f"{directory}: B4D task {tid} has a retry path with nothing to recover")
    _validate_b4d_recovery_policy(directory, task, restored, mid_write)


def _validate_b4d_write_point(directory: Path, task: dict[str, Any],
                              point: dict[str, Any]) -> None:
    """One candidate interruption point must leave a write genuinely half-done."""
    tid = task["id"]
    if not isinstance(point, dict) or "record" not in point or "after_parts" not in point:
        raise TaskSetError(
            f"{directory}: B4D task {tid} resolves a candidate to {point!r}, which is not an "
            "interruption point")
    surface_map = task["backup"]["contents"].get("surfaces") or {}
    ordered = write_state.parts(str(point["record"]), surface_map)
    applied = point["after_parts"]
    if isinstance(applied, bool) or not isinstance(applied, int):
        raise TaskSetError(
            f"{directory}: B4D task {tid} interrupts after {applied!r} part(s), which is not a "
            "count")
    if applied < 1 or applied >= len(ordered):
        raise TaskSetError(
            f"{directory}: B4D task {tid} interrupts after {applied} of {len(ordered)} part(s) of "
            f"{point['record']}; that is not a partial write")
    if write_state.state_after(applied, len(ordered)) != write_state.PARTIALLY_APPLIED:
        raise TaskSetError(
            f"{directory}: B4D task {tid} interruption point leaves {point['record']} in "
            f"{write_state.state_after(applied, len(ordered))!r}, not partially applied")


def _validate_b4d_recovery_policy(directory: Path, task: dict[str, Any], restored: list[str],
                                  mid_write: bool) -> None:
    """A mid-write scenario states whether the engine resumes or rolls back.

    The policy is visible to the solver (it is a property of the engine under
    test, not of the injection) and the gold write counts follow from it, so a
    fixture that states one policy and expects the other's write states is
    refused rather than scored against a contradiction.
    """
    tid = task["id"]
    policy = task.get("restore_policy")
    if not mid_write:
        if policy is not None:
            raise TaskSetError(
                f"{directory}: B4D task {tid} declares a restore policy without a mid-write "
                "schedule; there is no partially applied write to recover from")
        return
    if not isinstance(policy, dict):
        raise TaskSetError(
            f"{directory}: B4D task {tid} interrupts inside a write but declares no restore_policy; "
            "resume and rollback are different behaviours and the scenario must say which one it "
            "measures")
    recovery = policy.get("recovery")
    if recovery not in {"resume", "rollback"}:
        raise TaskSetError(
            f"{directory}: B4D task {tid} restore_policy recovery {recovery!r} is neither "
            "'resume' nor 'rollback'")
    if set(policy) - {"recovery"}:
        raise TaskSetError(
            f"{directory}: B4D task {tid} restore_policy has unknown key(s) "
            f"{sorted(set(policy) - {'recovery'})}")
    gold = task["expected"].get("write_states")
    if not isinstance(gold, dict):
        raise TaskSetError(
            f"{directory}: B4D task {tid} has no expected.write_states; a mid-write scenario "
            "without a gold write state cannot be scored")
    unknown = set(gold) - {"interrupted", "rolled_back", "committed"}
    if unknown:
        raise TaskSetError(
            f"{directory}: B4D task {tid} expected.write_states names unknown state(s) "
            f"{sorted(unknown)}")
    stops = task["restore_faults"]["stops"]
    want = {"interrupted": stops,
            "rolled_back": stops if recovery == "rollback" else 0,
            "committed": len(restored)}
    for key, value in want.items():
        if key not in gold:
            raise TaskSetError(
                f"{directory}: B4D task {tid} expected.write_states is missing {key!r}")
        if isinstance(gold[key], bool) or not isinstance(gold[key], int) or gold[key] != value:
            raise TaskSetError(
                f"{directory}: B4D task {tid} expects write_states.{key}={gold[key]!r} but its "
                f"schedule and policy produce {value}")
    for state in gold.values():
        if isinstance(state, bool) or not isinstance(state, int) or state < 0:
            raise TaskSetError(
                f"{directory}: B4D task {tid} expected.write_states holds a non-count value")


def _validate_b4d_backup(directory: Path, task: dict[str, Any], record_ids: set[str],
                         delete: list[str],
                         entries_by_record: dict[str, list[dict[str, Any]]]) -> None:
    """A restore scenario must name a real backup, taken before the deletion, restored in replay.

    A backup that cannot be ordered against the deletion, or that holds nothing deleted and
    nothing surviving, cannot demonstrate either half of C-02-AC3's restore requirement.
    """
    tid = task["id"]
    backup = task["backup"]
    if not isinstance(backup, dict):
        raise TaskSetError(f"{directory}: B4D task {tid} 'backup' must be an object")
    for key in ("backup_id", "taken_at", "restore_day", "contents"):
        if key not in backup:
            raise TaskSetError(f"{directory}: B4D task {tid} backup missing {key!r}")
    if not str(backup["backup_id"]).strip():
        raise TaskSetError(f"{directory}: B4D task {tid} backup has no backup_id")
    taken_at = parse_timestamp(backup["taken_at"])
    if taken_at is None:
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup taken_at {backup['taken_at']!r} is not an ISO "
            "timestamp; a restore cannot be ordered against the deletion without one")
    deleted_at_raw = task.get("deleted_at")
    if deleted_at_raw is None:
        raise TaskSetError(
            f"{directory}: B4D task {tid} declares a backup but no deleted_at; the backup cannot "
            "be shown to predate the deletion")
    deleted_at = parse_timestamp(deleted_at_raw)
    if deleted_at is None:
        raise TaskSetError(
            f"{directory}: B4D task {tid} deleted_at {deleted_at_raw!r} is not an ISO timestamp")
    if taken_at >= deleted_at:
        raise TaskSetError(
            f"{directory}: B4D task {tid} takes its backup at {backup['taken_at']} on or after the "
            f"deletion at {deleted_at_raw}; the scenario would restore a post-deletion state")

    restore_day = backup["restore_day"]
    if isinstance(restore_day, bool) or not isinstance(restore_day, int):
        raise TaskSetError(
            f"{directory}: B4D task {tid} restore_day {restore_day!r} is not an integer")
    if restore_day < 1 or restore_day > task["replay_days"]:
        raise TaskSetError(
            f"{directory}: B4D task {tid} restores on day {restore_day}, outside the "
            f"1..{task['replay_days']} replay window; that is not a replay restore")

    contents = backup["contents"]
    if not isinstance(contents, dict):
        raise TaskSetError(f"{directory}: B4D task {tid} backup contents must be an object")
    if "records" not in contents or "surfaces" not in contents:
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup contents need 'records' and 'surfaces'")
    held = contents["records"]
    if not isinstance(held, list) or not held:
        raise TaskSetError(f"{directory}: B4D task {tid} backup holds no records")
    if len(set(held)) != len(held):
        raise TaskSetError(f"{directory}: B4D task {tid} backup lists a record twice")
    unknown = set(held) - record_ids
    if unknown:
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup holds unknown records {sorted(unknown)}")
    filtered = sorted(set(held) & set(delete))
    restored = sorted(set(held) - set(delete))
    if not filtered:
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup holds no deleted record; an unfiltered restore "
            "could not reintroduce anything, so the scenario cannot fail closed")
    if not restored:
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup holds no surviving record; the restore would "
            "not have to preserve anything")

    for field in ("restore_restored_records", "restore_filtered_records"):
        if field not in task["expected"]:
            raise TaskSetError(
                f"{directory}: B4D task {tid} expected missing {field!r} for a restore scenario")
    outcome = task["expected"].get("restore_outcome", "recovered")
    if outcome not in {"recovered", "rejected"}:
        raise TaskSetError(
            f"{directory}: B4D task {tid} restore_outcome {outcome!r} is neither 'recovered' "
            "nor 'rejected'")
    if outcome == "rejected":
        if task["expected"]["restore_restored_records"]:
            raise TaskSetError(
                f"{directory}: B4D task {tid} rejects its backup but lists records it would "
                "restore; a refused backup applies nothing")
    elif sorted(task["expected"]["restore_restored_records"]) != restored:
        raise TaskSetError(
            f"{directory}: B4D task {tid} restore_restored_records disagree with the backup minus "
            "the deletion")
    if sorted(task["expected"]["restore_filtered_records"]) != filtered:
        raise TaskSetError(
            f"{directory}: B4D task {tid} restore_filtered_records disagree with the backup "
            "intersected with the deletion")

    held_surfaces = contents["surfaces"]
    if not isinstance(held_surfaces, dict):
        raise TaskSetError(f"{directory}: B4D task {tid} backup surfaces must be an object")
    missing = set(DERIVED_SURFACES) - set(held_surfaces)
    if missing:
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup is missing surface(s) {sorted(missing)}")
    extra = set(held_surfaces) - set(DERIVED_SURFACES)
    if extra:
        raise TaskSetError(
            f"{directory}: B4D task {tid} backup has unknown surface(s) {sorted(extra)}")
    backup_entry_ids: set[str] = set()
    copies: dict[str, list[str]] = {}
    for surface, entries in held_surfaces.items():
        if not isinstance(entries, list):
            raise TaskSetError(
                f"{directory}: B4D task {tid} backup surface {surface} must be a list")
        for entry in entries:
            eid = entry.get("entry_id")
            if not eid or not isinstance(eid, str):
                raise TaskSetError(
                    f"{directory}: B4D task {tid} backup entry on {surface} has no entry_id")
            if eid in backup_entry_ids:
                raise TaskSetError(
                    f"{directory}: B4D task {tid} backup entry_id {eid} duplicated")
            backup_entry_ids.add(eid)
            rid = entry.get("record_id")
            if rid not in held:
                raise TaskSetError(
                    f"{directory}: B4D task {tid} backup entry {eid} names {rid!r}, which the "
                    "backup does not hold")
            copies.setdefault(str(rid), []).append(eid)
    for rid in filtered:
        if not copies.get(rid):
            raise TaskSetError(
                f"{directory}: B4D task {tid} backup holds no derived copy of the deleted {rid}; "
                "restore-time residue could not be observed")

    _validate_b4d_integrity(directory, task, held, filtered)
    _validate_b4d_recovery(directory, task, held, filtered, restored, copies)
    _validate_b4d_attempts(directory, task, held, filtered, restored, copies)


def _validate_b4d_attempts(directory: Path, task: dict[str, Any], held: list[str],
                           filtered: list[str], restored: list[str],
                           copies: dict[str, list[str]]) -> None:
    """Retry scenarios must declare an ordered attempt sequence that ends recovered.

    An interruption point that changes nothing, a retry numbered out of order, a
    duplicate attempt, a sequence that stops incomplete, and a retry that is never
    declared are all refused: each would let a fixture claim recovery it cannot
    demonstrate.
    """
    tid = task["id"]
    backup = task["backup"]
    expected = task["expected"]
    # An injected schedule is checked first, because its own separability guards
    # have to see a damaged backup to refuse the combination.
    if task.get("restore_faults") is not None:
        return _validate_b4d_faults(directory, task, held, restored)
    if backup.get("corruption") is not None or expected.get("restore_outcome") == "rejected":
        return _validate_b4d_damaged_attempts(directory, task)
    attempts = backup.get("attempts")
    declared = ([{"attempt": 1, "stop_after_records": None, "expect_complete": True}]
                if attempts is None else attempts)
    if not isinstance(declared, list) or not declared:
        raise TaskSetError(f"{directory}: B4D task {tid} backup attempts must be a non-empty list")

    numbers: list[int] = []
    stops: list[int | None] = []
    for position, attempt in enumerate(declared, start=1):
        if not isinstance(attempt, dict):
            raise TaskSetError(
                f"{directory}: B4D task {tid} attempt {position} must be an object")
        number = attempt.get("attempt")
        if isinstance(number, bool) or not isinstance(number, int):
            raise TaskSetError(
                f"{directory}: B4D task {tid} attempt {position} has no integer attempt number")
        if number in numbers:
            raise TaskSetError(
                f"{directory}: B4D task {tid} declares attempt {number} twice; a retry sequence "
                "may not repeat an attempt")
        numbers.append(number)
        stop = attempt.get("stop_after_records")
        if stop is not None:
            if isinstance(stop, bool) or not isinstance(stop, int):
                raise TaskSetError(
                    f"{directory}: B4D task {tid} attempt {number} stop_after_records "
                    f"{stop!r} is neither null nor an integer")
            if stop < 1 or stop >= len(held):
                raise TaskSetError(
                    f"{directory}: B4D task {tid} attempt {number} stops after {stop} of "
                    f"{len(held)} records; an interruption point must leave work undone")
        if not isinstance(attempt.get("expect_complete"), bool):
            raise TaskSetError(
                f"{directory}: B4D task {tid} attempt {number} must declare expect_complete "
                "true or false")
        if attempt["expect_complete"] != (stop is None):
            raise TaskSetError(
                f"{directory}: B4D task {tid} attempt {number} claims expect_complete="
                f"{attempt['expect_complete']} with stop_after_records={stop}")
        stops.append(stop)

    if numbers != list(range(1, len(numbers) + 1)):
        raise TaskSetError(
            f"{directory}: B4D task {tid} attempts are numbered {numbers}; a retry sequence must "
            "be ordered 1..n with no gap")
    if stops[-1] is not None:
        raise TaskSetError(
            f"{directory}: B4D task {tid} ends on an interrupted attempt; the fixture must "
            "declare recovery, since an abandoned restore is a failure, not a scenario")
    if any(stop is None for stop in stops[:-1]):
        raise TaskSetError(
            f"{directory}: B4D task {tid} retries after a completed attempt; nothing would be "
            "left to resume")
    if len(declared) > 1 and stops[0] is None:
        raise TaskSetError(
            f"{directory}: B4D task {tid} declares a retry whose first attempt was not interrupted")

    # the gold intermediate state: survivors the backup holds that the last
    # interrupted attempt has not put back yet
    last_incomplete = max((index for index, stop in enumerate(stops) if stop is not None),
                          default=None)
    interrupted_missing: list[str] = []
    if last_incomplete is not None:
        present = set(restored) - set(task.get("pre_restore_missing", []))
        present |= set(held[:stops[last_incomplete]]) & set(restored)
        interrupted_missing = sorted(set(restored) - present)
    declared_interrupted = expected.get("restore_interrupted_missing", [])
    if sorted(declared_interrupted) != interrupted_missing:
        raise TaskSetError(
            f"{directory}: B4D task {tid} restore_interrupted_missing "
            f"{declared_interrupted} disagrees with the declared attempt sequence "
            f"(expected {interrupted_missing})")
    if "restore_attempts" not in expected:
        if len(declared) != 1:
            raise TaskSetError(
                f"{directory}: B4D task {tid} declares {len(declared)} attempts but no "
                "restore_attempts in its gold state")
    elif expected["restore_attempts"] != len(declared):
        raise TaskSetError(
            f"{directory}: B4D task {tid} expected restore_attempts={expected['restore_attempts']} "
            f"but the backup declares {len(declared)} attempts")

def load_tasksets(root: Path | str = "eval/tasks") -> dict[str, TaskSet]:
    root = Path(root)
    sets: dict[str, TaskSet] = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        ts = load_taskset(directory)
        sets[ts.block] = ts
    if not sets:
        raise TaskSetError(f"no task sets found under {root}")
    return sets
