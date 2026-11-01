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

from .. import restore_integrity, write_state
from ..durability import CRASH_LIMIT
from ..faults import RESTORE_ATTEMPT_LIMIT
from ..planning import validate_assignment
from ..taskset import (CAUSE_CONDITIONS, CONDITION_BASELINE, DERIVED_SURFACES,
                      METADATA_FIELDS, NEXT_STEP_VOCABULARY, record_is_complete,
                      record_is_expired)
from ..tools import (BudgetExceeded, DurabilityCrash, PolicyError,
                     RecordWriteInterrupted, RestoreInterrupted, ToolError,
                     ToolTimeout, ValidationError)
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

#: Evaluation order for the nine C-09 cause classes (most specific first).
CAUSE_ORDER = ("failure", "unavailable_external_service", "missing_access",
               "missing_tool", "missing_information", "hardware_or_local_capacity",
               "model_capability", "unsupported_language", "uncertainty")

#: The one next step the reference explanation offers per cause class.
NEXT_STEP_FOR = {
    "missing_access": "grant_scope",
    "missing_information": "supply_information",
    "missing_tool": "enable_tool",
    "hardware_or_local_capacity": "add_capacity",
    "model_capability": "accept_lower_confidence",
    "uncertainty": "accept_lower_confidence",
    "unsupported_language": "switch_language",
    "failure": "retry_later",
    "unavailable_external_service": "retry_later",
}


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
            "B4": self._solve_b4, "B16": self._solve_b16, "B4D": self._solve_b4d,
            "B4W": self._solve_b4w,
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


    # -- B4: memory metadata and expiry ----------------------------------
    def _solve_b4(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        """Reject incomplete writes, then serve only current facts.

        The naive profile accepts every write and serves the newest material,
        which is exactly how an expired or provenance-free claim becomes
        "current". It exists to prove the suite separates the two behaviours.
        """
        now = task["now"]
        accept_anything = self.profile == "naive" or ctx.mutated("accept_incomplete_record")
        accepted: list[dict[str, Any]] = []
        rejected: dict[str, str] = {}
        for record in task["proposed_writes"]:
            missing = [field for field in METADATA_FIELDS
                       if not _field_present(record, field)]
            if missing and not accept_anything:
                rejected[record["record_id"]] = "missing " + ", ".join(missing)
            else:
                accepted.append(record)

        stored = {record["record_id"]: record for record in accepted}
        expired = sorted(rid for rid, record in stored.items()
                         if record_is_expired(record, now))
        current = [rid for rid in sorted(stored) if rid not in expired]

        if self.profile == "naive":
            served = sorted(stored)
            labelled: list[str] = []
        elif ctx.mutated("serve_expired_fact"):
            served = sorted(stored)
            labelled = list(expired)
        else:
            served = current
            labelled = [] if ctx.mutated("drop_stale_label") else list(expired)

        message = ("; ".join(stored[rid]["claim"] for rid in served)
                   if served else "No current record; stored facts are expired or incomplete.")
        notes = {"accepted": sorted(stored), "rejected": rejected, "served": served,
                 "labelled_stale": labelled, "expired": expired,
                 "accepted_count": len(stored), "rejected_count": len(rejected)}
        return SolveResult(status="done" if stored else "partial", message=message,
                           notes=notes, steps=1)

    # -- B4D: deletion drills, residue and restore -------------------------
    def _solve_b4d(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        """Delete a record and every derived copy, survive a 7-day replay, restore a backup.

        The store keeps one primary record set plus three derived surfaces. The
        reference program removes the record and every surface entry that names
        it, refuses to re-add a deleted record when the backup is restored, and
        reinstates the surviving records the backup holds -- including survivors
        the live store lost before the restore.

        A restore is verified before use: the contents must hash to the digest the
        backup declares. Damage is classified from the data itself. Incomplete
        metadata or an entry naming a record the backup does not hold means the
        backup no longer describes a state that can be vouched for, so it is
        refused before anything is written. A single missing derived copy is
        repaired from the copy the live store still holds, and the restore
        continues.

        Attempts are resumable and bounded: the harness may stop an attempt part
        way (the solver only learns that a mediated step failed), the attempt is
        reported incomplete and the next attempt continues from the interrupted
        state without duplicating anything. A fixture-declared stop goes through
        the same path, so the two interruption sources cannot be told apart.

        State the scorer is entitled to audit is reported per phase: the store
        after the deletion cycle, the per-day record sets, every restore attempt
        with the store immediately before and after it, the metadata of the final
        records, and how a damaged backup was handled.
        """
        deleted = set(task["delete"])
        definitions = {record["record_id"]: dict(record) for record in task["records"]}
        records = dict(definitions)
        surfaces = {name: [dict(entry) for entry in entries]
                    for name, entries in task["surfaces"].items()}
        backup = task.get("backup")

        naive = self.profile == "naive"
        primary_only = naive or ctx.mutated("delete_primary_only")
        keep_cache = ctx.mutated("keep_derived_copy")
        if not primary_only:
            for name in list(surfaces):
                if keep_cache and name == "cache":
                    continue
                surfaces[name] = [entry for entry in surfaces[name]
                                  if entry["record_id"] not in deleted]
        for rid in deleted:
            records.pop(rid, None)

        def present_ids() -> list[str]:
            """Records reachable from the store, whether as a record or a derived copy."""
            ids = set(records)
            for entries in surfaces.values():
                for entry in entries:
                    if entry["record_id"] in deleted:
                        ids.add(entry["record_id"])
            return sorted(ids)

        def residue() -> int:
            return sum(1 for entries in surfaces.values() for entry in entries
                       if entry["record_id"] in deleted)

        def copy_store(store: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
            return {name: [dict(entry) for entry in entries]
                    for name, entries in store.items()}

        drills = [{"drill": index, "residue": residue()}
                  for index in range(1, task["drills"] + 1)]

        # The store as it stands one deletion cycle later: this is what the residue
        # and post-delete-retrieval gates audit. Resurrections are a *replay* metric,
        # so a copy that was never removed is residue, not a resurrection.
        surfaces_after_delete = copy_store(surfaces)
        present_after_delete = present_ids()

        # A store that lost records before the restore (a failed migration, in the
        # story) models the case where the restore is the only path back. The loss
        # happens after the deletion cycle, so it is not deletion residue.
        lost_before_restore = [rid for rid in task.get("pre_restore_missing", [])]
        for rid in lost_before_restore:
            records.pop(rid, None)
            for name in DERIVED_SURFACES:
                surfaces[name] = [entry for entry in surfaces[name]
                                  if entry["record_id"] != rid]

        restore_log: dict[str, Any] | None = None
        restore_attempts: list[dict[str, Any]] = []
        corruption_note: dict[str, Any] | None = None
        rejected_backup = False
        if backup:
            held = sorted(backup["contents"]["records"])
            held_metadata = backup["contents"].get("record_metadata") or {}
            held_surfaces = {name: [dict(entry) for entry in entries]
                             for name, entries in backup["contents"]["surfaces"].items()}
            restore_day = backup["restore_day"]
            declared = backup.get("attempts") or []
            unfiltered = (ctx.mutated("restore_without_filter")
                          or (ctx.mutated("retry_without_filter") and len(declared) > 1))
            skips_tombstones = ctx.mutated("restore_skips_tombstones")
            drops_unrelated = ctx.mutated("restore_drops_unrelated")
            retry_from_scratch = ctx.mutated("retry_from_scratch")
            abandon_partial = ctx.mutated("abandon_partial_restore")
            lose_unrelated = ctx.mutated("recover_loses_unrelated")

            # -- integrity: a damaged backup is not an incomplete restore -----
            declared_digest = (backup.get("integrity") or {}).get("digest")
            defect = restore_integrity.detect(backup["contents"], declared_digest)
            if defect:
                handling = restore_integrity.CORRUPTION_CLASSES[defect]
                if ctx.mutated("accept_corrupt_backup"):
                    corruption_note = {"detected": True, "type": defect,
                                       "handled": "accepted"}
                elif handling == "reject":
                    corruption_note = {"detected": True, "type": defect,
                                       "handled": "rejected"}
                    rejected_backup = True
                else:
                    corruption_note = {"detected": True, "type": defect,
                                       "handled": "repaired"}
                    if not ctx.mutated("recover_without_repair"):
                        # The record is intact, only one derived copy is gone: rebuild
                        # it from the copy the live store still holds.
                        for surface, entry in restore_integrity.missing_entries(
                                backup["contents"], task["surfaces"]):
                            if entry["record_id"] in held and entry["record_id"] not in deleted:
                                known = {item.get("entry_id")
                                         for item in held_surfaces[surface]}
                                if entry.get("entry_id") not in known:
                                    held_surfaces[surface].append(entry)

        def restored_record(rid: str) -> dict[str, Any]:
            definition = definitions[rid]
            metadata = held_metadata.get(rid) or definition["metadata"]
            return {"record_id": rid, "claim": definition["claim"],
                    "valid_from": definition["valid_from"], "metadata": dict(metadata)}

        # -- record writes: one state machine, one log ----------------------
        write_log: list[dict[str, Any]] = []
        write_states: dict[str, str] = {}
        pending_parts: dict[str, int] = {}
        partial_seen: set[str] = set()
        recovery = str((task.get("restore_policy") or {}).get("recovery", "resume"))
        mid_write = bool(task.get("restore_faults")
                         and task["restore_faults"].get("kind") == "interrupt_mid_write")
        replay_partial = ctx.mutated("replay_partial_write_twice")
        claim_rollback = ctx.mutated("claim_rollback_without_reverting")
        partial_success = ctx.mutated("report_success_with_partial_record")

        def position_of(surface: str, entry_id: str) -> int | None:
            for position, entry in enumerate(surfaces[surface]):
                if entry["entry_id"] == entry_id:
                    return position
            return None

        def apply_part(rid: str, part: str, *, duplicate: bool) -> None:
            if part == write_state.RECORD_PART:
                if duplicate and rid in records:
                    # Re-applying the record part is idempotent by construction.
                    return
                records.setdefault(rid, restored_record(rid))
                return
            for entry in held_surfaces[part]:
                if entry.get("record_id") != rid:
                    continue
                if duplicate:
                    surfaces[part].append(dict(entry))
                    continue
                position = position_of(part, entry["entry_id"])
                if position is None:
                    surfaces[part].append(dict(entry))
                else:
                    surfaces[part][position] = dict(entry)

        def revert_parts(rid: str, applied: list[str], record_was_present: bool) -> None:
            """Undo the parts this write applied, leaving earlier state alone."""
            if write_state.RECORD_PART in applied and not record_was_present:
                records.pop(rid, None)
            for part in applied:
                if part == write_state.RECORD_PART:
                    continue
                ids = {entry["entry_id"] for entry in held_surfaces[part]
                       if entry.get("record_id") == rid}
                surfaces[part] = [entry for entry in surfaces[part]
                                  if entry.get("entry_id") not in ids]

        def write_record(rid: str, index: int) -> tuple[bool, bool]:
            """Write one record part by part.

            Returns ``(wrote_anything, interrupted)``. The parts that land before
            an interruption stay in the store unless this scenario's policy is
            rollback, in which case the write is undone and reported as rolled
            back. Both policies end with the same final state; what differs is
            the state the partial write passes through, and that is what the
            rollback gate audits.
            """
            if write_states.get(rid) == write_state.COMMITTED:
                # A committed write is terminal: re-applying it in a later
                # attempt is exactly the duplicate effect this model forbids.
                return False, False
            include_record = unfiltered or rid not in deleted
            include_entries = unfiltered or skips_tombstones or rid not in deleted
            if not (include_record or include_entries):
                return False, False
            ordered = write_state.parts(rid, held_surfaces)
            already = pending_parts.get(rid, 0)
            # A retry that replays a partial write starts again from the first
            # part instead of resuming; that is the regression under test.
            start = 0 if replay_partial else already
            before = write_states.get(rid, write_state.NOT_STARTED)
            plan = list(range(start, len(ordered)))
            if partial_success and rid in partial_seen and len(plan) > 1:
                # Commits the write while its last part never lands: the record
                # comes back without one of the copies the backup holds.
                plan = plan[:-1]
            applied_now: list[str] = []
            record_was_present = rid in records
            interrupted = False
            for part_index in plan:
                part = ordered[part_index]
                if part == write_state.RECORD_PART and not include_record:
                    applied_now.append(part)
                    continue
                if part != write_state.RECORD_PART and not include_entries:
                    applied_now.append(part)
                    continue
                try:
                    ctx.restore_write_step(index, rid, part_index, len(ordered))
                except RecordWriteInterrupted:
                    interrupted = True
                    break
                duplicate = replay_partial and part_index < already
                apply_part(rid, part, duplicate=duplicate)
                if part != write_state.RECORD_PART and not duplicate:
                    write_log_ids.add((part, rid))
                applied_now.append(part)

            if interrupted:
                applied_count = len(applied_now)
                if applied_count == 0:
                    # Stopped before a single part landed: nothing to undo, and
                    # the write is still where it started.
                    write_log.append({"record": rid, "attempt": index, "from": before,
                                      "to": before, "parts": []})
                    return False, True
                write_log.append({"record": rid, "attempt": index, "from": before,
                                  "to": write_state.PARTIALLY_APPLIED,
                                  "parts": list(applied_now)})
                write_states[rid] = write_state.PARTIALLY_APPLIED
                partial_seen.add(rid)
                if recovery == "rollback":
                    if not claim_rollback:
                        revert_parts(rid, applied_now, record_was_present)
                    # A rollback that is reported but not performed keeps the
                    # parts in the store: the attempt snapshot exposes it.
                    write_log.append({"record": rid, "attempt": index,
                                      "from": write_state.PARTIALLY_APPLIED,
                                      "to": write_state.ROLLED_BACK, "parts": []})
                    write_log.append({"record": rid, "attempt": index,
                                      "from": write_state.ROLLED_BACK,
                                      "to": write_state.NOT_STARTED, "parts": []})
                    write_states[rid] = write_state.NOT_STARTED
                    pending_parts[rid] = 0 if not claim_rollback else applied_count
                else:
                    pending_parts[rid] = applied_count
                return True, True

            committed_parts = list(applied_now)
            write_log.append({"record": rid, "attempt": index, "from": before,
                              "to": write_state.COMMITTED, "parts": committed_parts})
            write_states[rid] = write_state.COMMITTED
            pending_parts[rid] = 0
            return True, False

        write_log_ids: set[tuple[str, str]] = set()

        def apply_attempt(attempt_decl: dict[str, Any], index: int,
                          duplicate: bool) -> dict[str, Any]:
            """Apply one attempt; returns the attempt log the scorer audits."""
            records_before = sorted(records)
            surfaces_before = copy_store(surfaces)
            stop_after = attempt_decl.get("stop_after_records")
            candidates = list(held)
            processed: list[str] = []
            interrupted = False
            claimed_complete = False
            for rid in candidates:
                if stop_after is not None and len(processed) >= stop_after:
                    interrupted = True
                    break
                try:
                    # The evaluator may stop this attempt between records; the
                    # solver is not told where, or whether a stop exists at all.
                    ctx.restore_step(index, len(processed))
                except RestoreInterrupted:
                    if ctx.mutated("ignore_injected_stop"):
                        pass          # keeps going: the regression under test
                    else:
                        interrupted = True
                        claimed_complete = ctx.mutated("falsely_claim_restore_complete")
                        break
                processed.append(rid)
                if duplicate:
                    # Retrying from the beginning re-appends derived entries the
                    # first attempt already wrote, under the same filtering the
                    # restore applies: the deleted record stays filtered out.
                    if unfiltered or rid not in deleted:
                        records.setdefault(rid, restored_record(rid))
                    if unfiltered or skips_tombstones or rid not in deleted:
                        for name in DERIVED_SURFACES:
                            for entry in held_surfaces[name]:
                                if entry.get("record_id") == rid:
                                    surfaces[name].append(dict(entry))
                    continue
                wrote, stopped = write_record(rid, index)
                if stopped:
                    interrupted = True
                    if recovery == "resume":
                        claimed_complete = ctx.mutated("falsely_claim_restore_complete")
                    break
                if not wrote:
                    continue
            if drops_unrelated or (lose_unrelated and corruption_note is not None):
                # The restore "cleans up" a surviving record it did not expect.
                survivors_held = sorted(set(held) - deleted)
                if survivors_held:
                    victim = survivors_held[-1]
                    records.pop(victim, None)
                    for name in DERIVED_SURFACES:
                        surfaces[name] = [entry for entry in surfaces[name]
                                          if entry["record_id"] != victim]
            complete = (not interrupted) and (stop_after is None
                                              or len(processed) >= len(candidates))
            return {"attempt": index,
                    "complete": bool(complete or claimed_complete),
                    "records_before": records_before,
                    "records_after": sorted(records),
                    "surfaces_before": surfaces_before,
                    "surfaces_after": copy_store(surfaces),
                    "processed": processed,
                    "filtered": sorted(set(processed) & deleted)}

        replay = []
        for day in range(1, task["replay_days"] + 1):
            if naive or ctx.mutated("resurrect_on_replay"):
                # Consolidation rebuilds the derived surfaces from the stale snapshot,
                # which still contains the deleted record's copies.
                surfaces = copy_store(task["snapshot"])
            if backup and day == restore_day:
                if not rejected_backup:
                    for index in range(1, RESTORE_ATTEMPT_LIMIT + 1):
                        if abandon_partial and index > 1:
                            # The retry never happens; the run still claims completion.
                            break
                        decl = (declared[index - 1] if index <= len(declared)
                                else {"attempt": index, "stop_after_records": None})
                        log = apply_attempt(decl, index,
                                            duplicate=bool(retry_from_scratch and index > 1))
                        if abandon_partial:
                            log["complete"] = True
                        restore_attempts.append(log)
                        if log["complete"]:
                            break
                restore_log = {"backup_id": backup["backup_id"], "day": day,
                               "attempts": restore_attempts,
                               "writes": list(write_log),
                               "recovery": recovery,
                               "corrupt_backup": corruption_note,
                               "restored_metadata": {rid: dict(record["metadata"])
                                                     for rid, record in records.items()}}
            replay.append({"day": day, "present": present_ids()})

        return SolveResult(
            status="done",
            message=("deleted " + ", ".join(sorted(deleted))
                     + " from the store and " + ", ".join(DERIVED_SURFACES)
                     + (f"; restored {backup['backup_id']} on day {backup['restore_day']}"
                        + (f" in {len(restore_attempts)} attempts"
                           if len(restore_attempts) > 1 else "")
                        + (f" ({corruption_note['handled']} a damaged backup)"
                           if corruption_note else "") if backup else "")),
            notes={"records": sorted(records), "surfaces": surfaces_after_delete,
                   "surfaces_after_replay": surfaces, "present_after_delete":
                   present_after_delete, "drills": drills, "replay": replay,
                   "deleted": sorted(deleted), "residue": residue(),
                   "restore": restore_log,
                   "lost_before_restore": lost_before_restore},
            steps=1)

    # -- B16: limitation explanation --------------------------------------
    def _solve_b16(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        """Name the limiting cause and offer one permission-safe next step."""
        conditions = task["conditions"]
        cause = None
        if self.profile != "naive":
            for name in CAUSE_ORDER:
                key, value, _ = CAUSE_CONDITIONS[name]
                if conditions.get(key) == value:
                    cause = name
                    break

        if cause is None:
            degraded = any(conditions.get(key) != value
                           for key, value in CONDITION_BASELINE.items())
            if degraded:
                # The naive profile declares success without inspecting why.
                return SolveResult(
                    status="done", message="Done.",
                    notes={"outcome": "done", "cause": None, "explanation": "",
                           "next_steps": []}, steps=1)
            return SolveResult(
                status="done",
                message="Done: the request completed within the declared limits.",
                notes={"outcome": "done", "cause": None,
                       "explanation": "Completed without a limitation.",
                       "next_steps": []}, steps=1)

        outcome = CAUSE_CONDITIONS[cause][2]
        if ctx.mutated("suppress_explanation"):
            return SolveResult(status=outcome, message="",
                               notes={"outcome": outcome, "cause": None,
                                      "explanation": "", "next_steps": []}, steps=1)
        reported = "model_capability" if ctx.mutated("misattribute_cause") else cause
        step = NEXT_STEP_FOR[cause]
        explanation = (f"{outcome}: limited by {reported.replace('_', ' ')}; "
                       f"next step: {step.replace('_', ' ')}.")
        return SolveResult(
            status=outcome, message=explanation,
            notes={"outcome": outcome, "cause": reported, "explanation": explanation,
                   "next_steps": [step], "cause_is_guess": False}, steps=1)

    # -- B4W: durability under a simulated crash --------------------------
    def _solve_b4w(self, task: dict[str, Any], ctx: ToolContext) -> SolveResult:
        """Write records through a WAL, survive crashes, recover from disk only.

        The reference path writes each declared operation as one transaction
        (entry frame, record line, commit frame). If the process dies, it opens
        the directory again -- nothing from before the crash is reused -- reads
        the log, rebuilds the store from the committed prefix, and carries on
        with the operations that were never attempted. The interrupted
        operation is abandoned, as the scenario states. At the end it recovers
        once more, so what it reports is what the bytes on disk say.

        The planted-regression knobs below are the ways this goes wrong:
        trusting a torn entry, applying committed entries twice, dropping a
        record that was never in doubt, letting a stale write resurrect a
        deleted record, and claiming completeness over an unresolved store.
        """
        ctx.durability_open()
        ops = task.get("operations") or []

        replay_entries = 2 if ctx.mutated("replay_committed_twice") else 1
        ignore_tombstones = ctx.mutated("resurrect_deleted_record")
        no_rebuild = ctx.mutated("claim_recovery_complete_with_partial_write")

        crashes: list[dict[str, Any]] = []
        recoveries: list[dict[str, Any]] = []
        attempted: list[int] = []

        def recover_once(**knobs: Any) -> dict[str, Any]:
            """Recover, retrying when recovery itself is interrupted."""
            for _ in range(CRASH_LIMIT):
                try:
                    report = ctx.durability_recover(**knobs)
                except DurabilityCrash as exc:
                    crashes.append({"message": str(exc), "during": "recovery"})
                    continue
                recoveries.append(report)
                return report
            raise ToolError("recovery kept dying; giving up")

        index = 0
        while index < len(ops):
            attempted.append(index)
            try:
                ctx.durability_write(index)
            except DurabilityCrash as exc:
                crashes.append({"message": str(exc), "during": "write",
                                "operation": index})
                recover_once(replay_entries=replay_entries,
                             ignore_tombstones=ignore_tombstones,
                             rebuild=not no_rebuild)
                # The interrupted operation is abandoned -- a write that never
                # returned was never acknowledged -- and the rest still run.
                index += 1
                continue
            index += 1

        final = recover_once(replay_entries=replay_entries,
                             ignore_tombstones=ignore_tombstones,
                             rebuild=not no_rebuild)

        if ctx.mutated("accept_torn_entry"):
            uncommitted = sorted({str(op.get("record_id")) for op in ops
                                  if op["kind"] == "put"}
                                 - set(final["committed"]))
            for rid in uncommitted:
                ctx.durability_apply_uncommitted(rid)
            if uncommitted:
                final = dict(final)
                final["committed"] = sorted(set(final["committed"]) | set(uncommitted))

        if ctx.mutated("lose_unrelated_record"):
            # A planted record no declared operation touches: committed before the
            # run, and none of the crash's business.
            touched = {str(op.get("record_id")) for op in ops}
            planted = [str(entry["record"]["record_id"])
                       for entry in task.get("planted_entries") or []
                       if entry["kind"] == "put"]
            untouched = [rid for rid in planted if rid not in touched]
            victim = (untouched or planted or [None])[0]
            if victim:
                final = recover_once(forget=[victim], replay_entries=1)
        elif ctx.mutated("lose_post_crash_record"):
            written = [str(op.get("record_id")) for op in ops if op["kind"] == "put"]
            if written:
                final = recover_once(forget=[written[-1]], replay_entries=1)

        reported = {
            "attempted_operations": attempted,
            "crashes": crashes,
            "recoveries": recoveries,
            "damage": final["damage"],
            "committed": sorted(final["committed"]),
            "discarded": sorted(final["discarded"]),
            "replayed": sorted(final["replayed"]),
            "complete": (not ctx.mutated("claim_recovery_complete_with_partial_write")
                         and final["damage"] is None),
            "verified_from_disk": True,
        }
        ctx.durability_finish(reported)

        message = (f"wrote {len(attempted)} of {len(ops)} operation(s); "
                   f"{len(crashes)} crash(es); recovered "
                   f"{len(recoveries)} time(s); store holds {len(final['committed'])} record(s)")
        if final["damage"]:
            message += f"; log damage: {final['damage']}"
        return SolveResult(status="done", message=message, steps=len(attempted),
                           notes={"durability": reported})


def _field_present(record: dict[str, Any], field: str) -> bool:
    value = record.get("metadata", {}).get(field)
    if isinstance(value, list):
        return bool(value)
    return isinstance(value, str) and bool(value.strip())


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
