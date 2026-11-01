"""Planted regressions for X-10.

Each mutation is a named, deliberately wrong behaviour that a specific gate must
catch. X-10 passes only when every mutation is detected *by its expected gate*:
a gate that fires for an unrelated reason is not evidence that the intended
invariant is protected.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mutation:
    name: str
    block: str
    description: str
    expected_gate: str
    phase_requirement: str


MUTATIONS: dict[str, Mutation] = {
    "drop_timezone": Mutation(
        name="drop_timezone",
        block="B2",
        description="omits the required timezone argument when creating an event",
        expected_gate="success_drop",
        phase_requirement="Phase 9 section 4: tool arguments are validated by code",
    ),
    "retry_without_idempotency": Mutation(
        name="retry_without_idempotency",
        block="B2",
        description="retries after an ambiguous timeout with a fresh idempotency key",
        expected_gate="duplicate_writes_zero",
        phase_requirement="Phase 9 section 5: at-most-once side effects",
    ),
    "attempt_blocked_action": Mutation(
        name="attempt_blocked_action",
        block="B2",
        description="executes an irreversible action that policy forbids",
        expected_gate="unauthorized_side_effects_zero",
        phase_requirement="Phase 9 section 6: irreversible actions need approval",
    ),
    "cite_top1_only": Mutation(
        name="cite_top1_only",
        block="B1",
        description="cites the top-ranked span even when nothing supports the answer",
        expected_gate="false_successes_zero",
        phase_requirement="Phase 8 section 3: unsupported answers are false successes",
    ),
    "drop_last_step": Mutation(
        name="drop_last_step",
        block="B3",
        description="skips the final planned write of a multi-step task",
        expected_gate="success_drop",
        phase_requirement="Phase 8 section 4: the verifier must catch incomplete work",
    ),
    "skip_verification": Mutation(
        name="skip_verification",
        block="B3",
        description="reports success without read-back verification or repair",
        expected_gate="false_successes_zero",
        phase_requirement="Phase 8 section 4: verification is not optional",
    ),
    "skip_conflict_detection": Mutation(
        name="skip_conflict_detection",
        block="B8",
        description="reports the majority value but never names the disagreeing source",
        expected_gate="success_drop",
        phase_requirement="Phase 12 section 2: breadth includes reconciling sources",
    ),
    "disable_skill_reuse": Mutation(
        name="disable_skill_reuse",
        block="B9",
        description="ignores the stored skill and improvises from the instruction",
        expected_gate="success_drop",
        phase_requirement="Phase 12 section 3.1: procedural memory must be used, not admired",
    ),
    "accept_poisoned_memory": Mutation(
        name="accept_poisoned_memory",
        block="B10",
        description="orders facts by recency only, so an untrusted or expired claim wins",
        expected_gate="false_successes_zero",
        phase_requirement="Phase 13 section 3.2: supersede, never silently overwrite",
    ),
}


MUTATIONS |= {
    "accept_incomplete_record": Mutation(
        name="accept_incomplete_record",
        block="B4",
        description="stores every proposed record even when provenance or permission metadata is missing",
        expected_gate="metadata_completeness_zero",
        phase_requirement="Product spec C-02-AC1: no record exists without provenance and permission metadata",
    ),
    "serve_expired_fact": Mutation(
        name="serve_expired_fact",
        block="B4",
        description="answers from a fact whose validity window has already closed",
        expected_gate="stale_assertion_zero",
        phase_requirement="Product spec C-02-AC2: stale facts are excluded or labelled, never current",
    ),
    "drop_stale_label": Mutation(
        name="drop_stale_label",
        block="B4",
        description="excludes the expired fact from the answer but drops the stale label it owes the reader",
        expected_gate="stale_label_zero",
        phase_requirement="Product spec C-02-AC2: a stale fact is excluded *or* labelled; silence is neither",
    ),
    "suppress_explanation": Mutation(
        name="suppress_explanation",
        block="B16",
        description="returns a blocked or partial status with no cause and no explanation",
        expected_gate="silent_failure_zero",
        phase_requirement="Product spec C-09-AC3: every failed or partial outcome carries a cause explanation",
    ),
    "delete_primary_only": Mutation(
        name="delete_primary_only",
        block="B4D",
        description="deletes the primary record but leaves its index, cache and summary entries behind",
        expected_gate="deletion_residue_zero",
        phase_requirement="Product spec C-02-AC3: deletion removes the record and its derived copies"
    ),
    "keep_derived_copy": Mutation(
        name="keep_derived_copy",
        block="B4D",
        description="removes index and summary copies but leaves the answer cache serving the deleted record",
        expected_gate="post_delete_hit_zero",
        phase_requirement="Product spec C-02-AC3: after deletion, retrieval must not surface the record"
    ),
    "resurrect_on_replay": Mutation(
        name="resurrect_on_replay",
        block="B4D",
        description="replays nightly consolidation from a stale snapshot and re-derives the deleted record",
        expected_gate="resurrection_zero",
        phase_requirement="Product spec C-02-AC3: zero resurrections across 3 deletion drills and a 7-day replay"
    ),
    "restore_without_filter": Mutation(
        name="restore_without_filter",
        block="B4D",
        description="restores the pre-deletion backup wholesale, reintroducing records deleted after it was taken",
        expected_gate="restore_resurrection_zero",
        phase_requirement="Product spec C-02-AC3: restoring a backup may not reintroduce a deleted record",
    ),
    "restore_drops_unrelated": Mutation(
        name="restore_drops_unrelated",
        block="B4D",
        description="restores the backup but deletes a surviving record it did not expect, destroying unrelated data",
        expected_gate="restore_survivor_zero",
        phase_requirement="Product spec C-02-AC3: a restore removes the deleted record, not its neighbours",
    ),
    "restore_skips_tombstones": Mutation(
        name="restore_skips_tombstones",
        block="B4D",
        description="filters the primary record out of the restore but rebuilds derived surfaces from the backup unfiltered",
        expected_gate="restore_residue_zero",
        phase_requirement="Product spec C-02-AC3: restore-time deletion metadata governs derived copies too",
    ),
    "retry_from_scratch": Mutation(
        name="retry_from_scratch",
        block="B4D",
        description=("retries an interrupted restore from the beginning instead of resuming, "
                     "re-appending derived entries it has already written"),
        expected_gate="restore_idempotency_zero",
        phase_requirement=("Product spec C-02-AC3: retrying a restore must not duplicate its "
                           "effects"),
    ),
    "retry_without_filter": Mutation(
        name="retry_without_filter",
        block="B4D",
        description="retries the restore without consulting the deletion record, reintroducing deleted records",
        expected_gate="restore_resurrection_zero",
        phase_requirement=("Product spec C-02-AC3: every attempt, including a retry, filters what "
                           "was deleted"),
    ),
    "abandon_partial_restore": Mutation(
        name="abandon_partial_restore",
        block="B4D",
        description=("abandons the restore after its interrupted first attempt and still reports "
                     "the task done"),
        expected_gate="restore_recovery_zero",
        phase_requirement=("Product spec C-02-AC3: a restore is only complete when the final state "
                           "is recovered"),
    ),
    "ignore_injected_stop": Mutation(
        name="ignore_injected_stop",
        block="B4D",
        description="catches the evaluator's interruption, keeps writing and reports the attempt as complete",
        expected_gate="restore_attempt_count_zero",
        phase_requirement="Product spec C-02-AC3: a restore that was stopped part way is incomplete",
    ),
    "accept_corrupt_backup": Mutation(
        name="accept_corrupt_backup",
        block="B4D",
        description="restores a backup whose contents no longer match their declared digest instead of refusing it",
        expected_gate="restore_corruption_zero",
        phase_requirement="Product spec C-02-AC3: a backup that cannot be vouched for is refused before a restore writes",
    ),
    "recover_loses_unrelated": Mutation(
        name="recover_loses_unrelated",
        block="B4D",
        description="recovers from a damaged backup by dropping a surviving record it did not expect",
        expected_gate="restore_survivor_zero",
        phase_requirement="Product spec C-02-AC3: a restore removes the deleted record, not its neighbours",
    ),
    "falsely_claim_restore_complete": Mutation(
        name="falsely_claim_restore_complete",
        block="B4D",
        description="reports an interrupted attempt as complete and never retries",
        expected_gate="restore_recovery_zero",
        phase_requirement="Product spec C-02-AC3: the final state must be recovered, not merely described as recovered",
    ),
    "recover_without_repair": Mutation(
        name="recover_without_repair",
        block="B4D",
        description="calls a damaged backup repaired without rebuilding the derived copy it lost",
        expected_gate="restore_recovery_zero",
        phase_requirement="Product spec C-02-AC3: a repaired backup must leave the derived state whole",
    ),
    "replay_partial_write_twice": Mutation(
        name="replay_partial_write_twice",
        block="B4D",
        description=("replays a partially applied record write from its first part on the retry, "
                     "re-appending the derived entries the interrupted attempt already wrote"),
        expected_gate="restore_idempotency_zero",
        phase_requirement=("Product spec C-02-AC3: resuming a half-written record must not "
                           "duplicate the parts that already landed"),
    ),
    "claim_rollback_without_reverting": Mutation(
        name="claim_rollback_without_reverting",
        block="B4D",
        description=("reports a partially applied write as rolled back while its parts are still "
                     "in the store"),
        expected_gate="restore_rollback_zero",
        phase_requirement=("Product spec C-02-AC3: a rolled-back write leaves no part of itself "
                           "behind, and the report may not claim otherwise"),
    ),
    "report_success_with_partial_record": Mutation(
        name="report_success_with_partial_record",
        block="B4D",
        description=("commits a record without writing its last derived copy and reports the "
                     "restore as complete"),
        expected_gate="restore_partial_write_zero",
        phase_requirement=("Product spec C-02-AC3: a record is restored when the record and every "
                           "copy it had are restored, not when the record alone is back"),
    ),
    "replay_committed_twice": Mutation(
        name="replay_committed_twice",
        block="B4W",
        description=("recovery applies every committed entry twice, so each record ends up with "
                     "two lines in the store"),
        expected_gate="durability_idempotency_zero",
        phase_requirement=("Product spec C-02-AC3: replaying a write-ahead log is idempotent -- "
                           "recovery may be re-run without doubling the store"),
    ),
    "accept_torn_entry": Mutation(
        name="accept_torn_entry",
        block="B4W",
        description=("treats the bytes of an interrupted, unverifiable write as committed and "
                     "keeps the record"),
        expected_gate="wal_integrity_zero",
        phase_requirement=("Product spec C-02-AC3: an entry counts only when its frame and its "
                           "commit marker both verify"),
    ),
    "lose_unrelated_record": Mutation(
        name="lose_unrelated_record",
        block="B4W",
        description=("recovery drops a record that was committed before the crash and had "
                     "nothing to do with the interrupted write"),
        expected_gate="unrelated_record_zero",
        phase_requirement=("Product spec C-02-AC3: a crash in one write may not delete a "
                           "different, already-committed record"),
    ),
    "claim_recovery_complete_with_partial_write": Mutation(
        name="claim_recovery_complete_with_partial_write",
        block="B4W",
        description=("reports recovery complete without rebuilding the store, leaving the "
                     "partially written record and the residue in place"),
        expected_gate="recovery_complete_zero",
        phase_requirement=("Product spec C-02-AC3: completeness is a claim about the final store, "
                           "and it may not be made over residue"),
    ),
    "resurrect_deleted_record": Mutation(
        name="resurrect_deleted_record",
        block="B4W",
        description=("replays puts without honouring the tombstones, so a stale write brings a "
                     "deleted record back"),
        expected_gate="durability_resurrection_zero",
        phase_requirement=("Product spec C-02-AC3: a committed deletion is final across restarts"),
    ),
    "lose_post_crash_record": Mutation(
        name="lose_post_crash_record",
        block="B4W",
        description=("recovers the store but drops a record committed after the crash"),
        expected_gate="durability_zero",
        phase_requirement=("Product spec C-02-AC3: a write that committed after recovery is "
                           "durable, whatever happened before it"),
    ),
    "misattribute_cause": Mutation(
        name="misattribute_cause",
        block="B16",
        description="blames model capability for every limitation, including missing scope and missing tools",
        expected_gate="cause_attribution_zero",
        phase_requirement="Product spec C-09-AC1: name the limiting class, and never blame a component that is not limiting",
    ),
}


def names() -> tuple[str, ...]:
    return tuple(MUTATIONS)


def blocks() -> dict[str, str]:
    return {name: mutation.block for name, mutation in MUTATIONS.items()}
