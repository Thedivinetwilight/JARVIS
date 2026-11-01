# `eval/` — evaluation harness (Stage 0 core, Stage 1 breadth)

A small, standard-library-only harness that runs eight task families reproducibly, records every attempt,
compares runs against a frozen baseline, and checks that a deliberately planted regression is caught by
the gate that protects the invariant it breaks:

| Family | What it exercises | Tasks | Stage |
|---|---|---:|---|
| **B1** | retrieval with citations | 8 | 0 |
| **B2** | single-tool execution under policy | 7 | 0 |
| **B3** | multi-step state mutation with read-back verification | 7 | 0 |
| **B8** | breadth: field extraction, source reconciliation, constraint planning | 18 | 1 |
| **B9** | skill reuse (applying a stored, under-specified skill) | 6 | 1 |
| **B10** | memory evolution (trust, supersession, expiry) | 6 | 1 |
| **B4** | memory metadata and expiry: required fields, rejection, stale exclusion/labelling | 12 | M5 slice |
| **B4D** | deletion drills + backup restore: record and derived copies removed, no post-delete hit, no resurrection over a 7-day replay, a pre-deletion backup restored without reintroducing or destroying anything, an evaluator-chosen interruption retried to completion, and a damaged backup refused or repaired | 22 | M5 slice |
| **B16** | limitation explanation: nine cause classes, no silent failure, safe next step | 12 | M5 slice |

## Where this sits (revision R1, 2026-11-01)

Stage 0 is complete and its evidence is frozen (M1–M3 notes in `docs/research/`); Stage 1 added the three
breadth families, the capability registry and the model-free solver ladder (M4 note). Revision R1 changed
the project direction to a brain-inspired, broadly capable, evolving system; it does **not** change the
harness or any recorded Stage 0 result. Under the
[revised roadmap](../docs/research/15-revised-roadmap-supersession-and-next-step.md#2-revised-roadmap)
this harness is the evaluator for Stage 2 (skills) and Stage 4 (self-proposed updates). Its limits are
listed in
[Phase 15 §7](../docs/research/15-revised-roadmap-supersession-and-next-step.md#7-what-the-current-harness-cannot-measure)
and [Stage 1 M4 §4](../docs/research/stage-1-m4-breadth-and-ladder.md), and should be read before quoting
any number from `results/`.

Design background: [`docs/research/08-evaluation-and-improvement.md`](../docs/research/08-evaluation-and-improvement.md),
[`docs/research/10-proposed-architecture-and-experiments.md`](../docs/research/10-proposed-architecture-and-experiments.md),
[`docs/research/12-capability-integration-architecture.md`](../docs/research/12-capability-integration-architecture.md),
and the plans in [`docs/research/stage-0-plan.md`](../docs/research/stage-0-plan.md) /
[`docs/research/stage-1-m4-breadth-and-ladder.md`](../docs/research/stage-1-m4-breadth-and-ladder.md).

## What this harness measures

- **Reproducibility.** A run is identified by `(git sha, config hash, task-set hashes, registry sha, seed)`.
  Each attempt carries an `outcome_hash` over the observed outcome only (status, success, metrics, detail,
  steps, cost) — not latency, not the run id — so two runs can be compared field by field, across
  processes.
- **Correctness against a specification**, not style: keyword answers plus citation precision (B1), final
  world state plus argument exactness (B2), independent read-back verification (B3), field exactness plus
  a gold citation / majority value plus the exact conflict set / independently re-checked assignments
  (B8), acceptance checks plus whether the stored skill was used (B9), surviving/superseded/expired sets
  plus zero poisoned values (B10).
- **Honesty**: a `false_success` is a run that claimed `done` while the world, the citations or the
  memory state disagreed. Repeated blocks also report `pass^k`.
- **Safety invariants** as zero-tolerance gates: approval violations, duplicate writes after an ambiguous
  timeout, unauthorised irreversible actions, credential-shaped strings in the log.
- **Discrimination**: the solver ladder (`x13`) checks that weaker hand-written behaviour scores lower
  than stronger behaviour, and the `family_regression` gate fails when one family collapses while the
  aggregate improves.

## M5 slices (B4, B4D, B16)

Two product families now have deliberately small evaluator suites: three **slices**, not the families and
not product features. `B4` (memory metadata and expiry), `B4D` (deletion drills, evaluator-chosen interruptions
and damaged backups: the second B4 slice) and `B16` (limitation explanation), 12 / 22 / 12 synthetic tasks,
each pre-registered inside its own `tasks.json`.

| Slice | Gates that must pass | Planted regressions (each caught by its own gate) |
|---|---|---|
| B4 `eval/tasks/b4` | `metadata_completeness_zero`, `stale_assertion_zero`, `stale_label_zero`, `stale_handling_threshold` (>=0.95, C-02-AC2) | `accept_incomplete_record`, `serve_expired_fact`, `drop_stale_label` |
| B4D `eval/tasks/b4d` | `deletion_residue_zero`, `post_delete_hit_zero`, `resurrection_zero`, `overdeletion_zero`, `deletion_report_zero`, `deletion_replay_completeness` (1.0, C-02-AC3), `restore_resurrection_zero`, `restore_survivor_zero`, `restore_residue_zero`, `restore_recovery_zero`, `restore_idempotency_zero`, `restore_attempt_count_zero`, `restore_corruption_zero` | `delete_primary_only`, `keep_derived_copy`, `resurrect_on_replay`, `restore_without_filter`, `restore_drops_unrelated`, `restore_skips_tombstones`, `retry_from_scratch`, `retry_without_filter`, `abandon_partial_restore`, `ignore_injected_stop`, `accept_corrupt_backup`, `recover_loses_unrelated`, `falsely_claim_restore_complete`, `recover_without_repair` |
| B16 `eval/tasks/b16` | `silent_failure_zero`, `cause_attribution_zero`, `unsafe_advice_zero`, `cause_accuracy_threshold` (>=0.95, C-09-AC1) | `suppress_explanation`, `misattribute_cause` |

The deletion slice reports its schedule instead of implying a rate: 22 scenarios, 3 deletion drills each
(66 executions), 7 replay days each (154 day evaluations), 14 backups and 14 restore events (each on a
declared replay day, 21 restore attempts in total: 4 retried after a stop the fixture declares, 4 interrupted
at a point the *evaluator* chooses with 5 stops in total, 3 run against a deliberately damaged backup of which
2 are refused before any attempt; 6 records lost before a restore), 38 recovery probes, 67 post-delete probes,
26 deleted records, 137 derived-surface entries and 94 entries held inside the backups. Those are deterministic re-executions, not independent samples, and
no confidence interval follows from them; the scripted interruptions say nothing about how a real engine
would fail. Four phases are scored separately: residue and post-delete hits on the store *after* the deletion
cycle; resurrections only on what returns during the replay (so a copy that was never removed is residue, not
a resurrection); each restore attempt on the store immediately before and after it (so a record already
present when an attempt runs is residue, not a restore resurrection); and the final recovered state after the
last attempt, where an interrupted first attempt is incomplete rather than failed, a retry must not duplicate
entries, and abandoning the restore fails recovery. A damaged backup is a fifth case with its own gate: it is
refused before any write or repaired from the live store, and a corrupt backup presented as an ordinary
incomplete restore counts as accepted corruption, not as an interrupted attempt.

Three properties are worth knowing before quoting a number:

- **The bound is not rounded down.** With 12 tasks, a >=95% bound is only satisfiable at 12/12. The gate applies
  the published number, so a single failure fails the slice; a larger suite is what would make 95% meaningful.
  The deletion slice has no rate at all: every gate is zero-tolerance, and replay completeness must be 1.0
  because C-02-AC3 fixes the schedule at 3 drills and 7 days.
- **The fixtures police themselves.** The loader refuses a B4 record that invents a metadata field, a fixture
  that accepts an incomplete record, an expired record with no declared stale handling, a B4D scenario with a
  missing or unknown surface, a deleted record with no derived or snapshot copy, a probe that matches nothing
  or two records, a schedule other than 3 drills / 7 days, a surviving-record list that contradicts the
  deletion, a backup with no timestamp or one taken on or after the deletion, a restore scheduled outside the
  replay window, a backup that holds nothing deleted or nothing surviving, gold restore lists that contradict
  the backup, a retry sequence with a duplicate, a gap or an out-of-order attempt number, an interruption
  point that leaves nothing undone or a sequence that ends interrupted, a completion flag that contradicts
  the declared interruption, a fault schedule with no salt, an unsorted or repeated candidate set, a stop at
  zero or after every record, more than two stops, no retry path, an attempt count the schedule does not
  produce, a fixture that also declares the stop points or a gold outcome that depends on which point is
  chosen, an injected schedule combined with a damaged backup, a backup with no digest or one that does not
  match its contents, an unknown corruption class, a recipe that changes nothing or classifies as another
  class, a refused class with a recovered outcome or a repairable class with a refused one, backup metadata
  that invents a field or omits a held record, a refused restore that still lists records or attempts, a pre-restore loss with no recovery probe, a recovery probe that expects a
  deleted record back or matches nothing, and a B16 task whose gold cause contradicts its own conditions or
  whose "valid" next step would violate permissions.
- **A slice is not a capability.** The solver is a deterministic hand-written program, the fixtures are
  invented, and no model, server, personal record or network call is involved.

Evidence: `results/m5-slices-report.json` plus the `*-B4-*.jsonl` and `*-B16-*.jsonl` ledgers;
`baselines/baseline-m5-slices.json` freezes the clean B4/B16 summaries. The deletion slice has its own
evidence — `results/m5-b4d-deletion-report.json` and the `*-B4D-*.jsonl` ledgers, then
`results/m5-b4d-restore-report.json` after the suite was extended with backup/restore, then
`results/m5-b4d-retry-report.json` and `results/x10-report-m5-b4d-retry.json` once interrupted restores and
retries were scored, then `results/m5-b4d-faults-report.json` and `results/x10-report-m5-b4d-faults.json`
once the evaluator chose the interruption points and damaged backups were added — and its own baselines,
`baselines/baseline-m5-b4d-deletion.json` (the 8-task slice, kept as historical evidence),
`baselines/baseline-m5-b4d-restore.json` (the 11-task suite), `baselines/baseline-m5-b4d-retry.json`
(the 15-task suite) and `baselines/baseline-m5-b4d-faults.json` (the 22-task suite).

## What this harness does **not** measure

- **It does not measure a model.** Every solver in `harness/solvers/` is a deterministic hand-written
  program. The 100% scores in `results/scorecard*.md` are a *ceiling set by construction*, not a capability
  result. The `oracle_ceiling` rung is worse still: it reads the fixtures' gold fields to bound what the
  suite can award, and it ties the reference rung on all six families — the suite therefore shows no
  demonstrated headroom above the reference program.
- It does not evaluate the unfinished families B5–B7, B11–B15 or B17–B20 (multimodal, voice, adversarial,
  speaker verification, telephony, preferences, self-evolution, invention, canon parity, smart home,
  connectors), long-horizon tasks, or real user data. B4, B4D and B16 exist only as the slices described
  above; the deletion slice restores scripted backups from an invented store — the evaluator picks where an
  attempt is interrupted, but it is still a clean abort between records, and its corruption classes are three
  synthetic cases under a sha256 digest — and it has no retention policy, off-site replication, legal-hold
  case or multi-day restore ordering.
  Every fixture is synthetic and the loader refuses anything else.
- It does not call any model, network service or real calendar/mail/filesystem. There is no
  network-capable import anywhere under `eval/harness` and a self-check enforces that.
- It does not produce confidence intervals: the solvers are deterministic, so run-to-run variance is zero
  by construction.
- **It does not measure the product requirements.** The twelve product capabilities (C-01…C-12:
  preferences and personal context, modular stores and a people database, voice-first interaction with no
  chat boxes and measured multilingual coverage, speaker verification, PA call handling, brain-inspired
  operation without a consciousness goal, bounded self-evolution, invention support, limitation
  explanation, MCU/Rescue parity, smart-home control, and scoped connectors) are specified in
  [`docs/product/capability-specification.md`](../docs/product/capability-specification.md). None is
  implemented, and the product runs everything locally on a dedicated server — this harness is a synthetic
  simulator and does not exercise that boundary. The registry in `registry/` is a harness inventory of
  simulators, not evidence for any of them.

## Layout

```
eval/
  harness/
    config.py        run identity, frozen thresholds, seeding, git provenance
    stats.py         Wilson interval, nearest-rank percentile, mean
    taskset.py       task-set loading + schema, synthetic-data and pre-registration validation
    registry.py      capability registry loader/validator, coverage checks, evaluator digest
    planning.py      constraint checker shared by the planner solver and its scorer
    tools.py         in-memory calendar/mail/filesystem with validation, policy,
                     idempotency, fault injection and credential redaction
    solvers/
      base.py        Solver protocol, ToolContext, single idempotent retry
      stub.py        reference / naive / no-verify profiles (B1-B3, B8-B10, B4, B4D, B16)
      ladder.py      the X-13 rungs, including the gold-reading oracle probe
      mutations.py   the planted regressions used by X-10 (23 at M5.4)
    scorers.py       per-family scoring; pure functions of fixture + result + world
    runner.py        task x repeat execution, budgets, record hashing, per-subfamily summary
    ledger.py        JSONL result ledger, index, frozen baseline
    scorecard.py     Markdown + JSON scorecard
    gates.py         zero-tolerance invariants, regression thresholds, family_regression
    selftest.py      17 self-checks of the harness itself
    cli.py           command line entry point
  tasks/             synthetic fixtures: b1/{corpus,tasks}.json, b2/, b3/, b4/, b4d/, b8/, b9/,
                     b10/, b16/
  registry/          capabilities.json (evaluator-owned) + README
  baselines/         baseline-a-stub.json (Stage 0), baseline-b-stage1.json (six families),
                     baseline-m5-slices.json (B4/B16)
  results/           committed evidence: *.jsonl runs, index.json, scorecards, x10/x13 reports
  tests/             161 unittest cases
  scripts/           x10_demo.sh — end-to-end Stage 0 demonstration
```

## Commands

```bash
python3 -m eval.harness selftest        # harness self-checks (must be 16/16)
python3 -m eval.harness list            # task sets, mutations and their expected gates
python3 -m eval.harness run             # all six families with the reference solver
python3 -m eval.harness baseline-freeze # freeze the clean reference run as baseline-b
python3 -m eval.harness x10 --baseline eval/baselines/baseline-b-stage1.json \
        --report eval/results/x10-report-stage1.json   # nine planted regressions
python3 -m eval.harness x13 --out eval/results/x13-ladder.md   # solver ladder (X-13)
python3 -m eval.harness scorecard --run <file.jsonl> [--run ...] --out scorecard.md
python3 -m eval.harness gates --run <file.jsonl>
python3 -m unittest discover -s eval/tests -t .
bash eval/scripts/x10_demo.sh           # Stage 0 demonstration, in order
```

`x10` exits non-zero if any mutation escapes **its expected gate**. A gate that fires for an unrelated
reason does not count: X-10 asks whether the intended invariant is actually protected. `x13` exits
non-zero if no adjacent pair of rungs separates by 10 pp on any family — the pre-registered kill
criterion for Stage 1, which means the family difficulty has to be fixed before any model is scored.

## Rules for changing this harness

1. Fixtures are frozen per version. Editing a task set changes its `sha256`, which changes the run
   identity, and requires a new version string — never an in-place edit of a published set.
2. A new check must come with a way to fail. If you add a metric, add the mutated behaviour that that
   metric is supposed to catch (see `solvers/mutations.py`).
3. Only synthetic data. Real names, addresses, credentials or user content are rejected by the loader.
4. Results are evidence: `results/` is append-only in spirit. Re-running produces a new file rather than
   editing an old one.
5. `registry/`, `tasks/` and `baselines/` are **evaluator-owned**. Nothing the system under test does may
   write to them; `evaluator_isolation` verifies this with a digest, and the registry sha is part of every
   run id so that edits invalidate comparability instead of silently changing it.
