# `eval/registry/` — capability registry (evaluator-owned)

> **This is a test-harness inventory, not product features.** No entry here is implemented in a product:
> each one is an in-memory simulator the evaluator can call. The product requirements are C-01…C-14 in the
> [capability specification](../../docs/product/capability-specification.md); a registry entry means "the
> harness can simulate this capability for a fixture", and nothing more. See
> [`docs/product/README.md`](../../docs/product/README.md) for the boundary between product, research and
> evaluation.

This directory is **owned by the evaluator, not by the system under test**. It is one of the first
artefacts of Stage 1 of the
[revised roadmap](../../docs/research/15-revised-roadmap-supersession-and-next-step.md#5-the-recommended-next-step-stage-1--discriminative-multi-family-evaluation--capability-registry):
breadth becomes an inventory instead of a claim.

## What is here

`capabilities.json` — one entry per capability the evaluation fixtures exercise (19 at registry v1.4.0):
tools (`calendar.create_event`, `mail.send`, `files.write/append/delete`, `memory.write_record`,
`memory.retrieve_current`), skills (`corpus.retrieve`, `document.extract_fields`, `source.reconcile`,
`schedule.assign`, `skill.apply`, `memory.consolidate`, `explain.limitation`), the verifier and the approval
policy. `memory.restore_backup` is the third M5-era entry: it simulates a backup restore that filters
deleted records, keeps unrelated ones, can be interrupted at a point the harness chooses — between two
records or inside one, leaving the record partially applied with the copies written so far — and refuses
or repairs a damaged backup. The call fails with `RestoreInterrupted`; a retry resumes on the same day,
either finishing the record it left half-written or removing it from every copy first, and the harness
scores the final state rather than the interruption; it backs the B4D deletion / restore slice. All of them still simulate nothing that exists in a product. Each entry declares:

| Field | Why |
|---|---|
| `contract` | preconditions, postconditions, argument schema — validation is code, not prose |
| `cost_profile` | synthetic in Stage 1 (no model calls); `usd_per_call` must be 0.0 and `synthetic` must be true |
| `failure_modes` | what the family's mutations and tests must cover |
| `idempotency` | `required_key` for side effects, `none` for pure skills |
| `tests` | a reference into `eval/tests/` that fails if the behaviour regresses |
| `evidence` | where the capability was established |
| `owner` | a human is accountable; `unassigned at Stage 1` is a tracked gap, not a pass |
| `families` | which task blocks exercise it |

## The boundary, and how it is enforced

1. **Nothing under `eval/registry/`, `eval/tasks/` or `eval/baselines/` is writable by the system.**
   A solver receives a task and a `ToolContext`; it never receives a registry handle. `python -m
   eval.harness selftest` hashes every evaluator-owned file before and after a full run and fails if
   anything changed (`evaluator_isolation`).
2. **A change here changes the run identity.** The registry sha256 and every task-set sha256 are part
   of the run id, so results recorded before and after a registry edit cannot be mistaken for each
   other.
3. **The registry cannot describe authority over the evaluator.** Capability ids beginning `eval.`,
   `harness.`, `gate.`, `baseline.`, `permission.` or `policy.admin` are rejected by
   `eval/harness/registry.py`.
4. **Fixture coverage is checked.** Every capability a task payload uses must exist in the registry,
   and every registry entry must be a valid, tested, owned record — `validate_registry()` reports
   violations and the self-test fails on any of them.

## Honest limits

This is an inventory, not evidence of capability. No model backs any entry; cost profiles are zeros;
the tests are unit tests over the simulator. A registry entry means "this capability exists in the
harness and someone must keep it honest", never "the system can do this at human level".
