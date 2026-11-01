# `eval/` — evaluation harness (Stage 0 core, Stage 1 breadth)

A small, standard-library-only harness that runs six task families reproducibly, records every attempt,
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

## What this harness does **not** measure

- **It does not measure a model.** Every solver in `harness/solvers/` is a deterministic hand-written
  program. The 100% scores in `results/scorecard*.md` are a *ceiling set by construction*, not a capability
  result. The `oracle_ceiling` rung is worse still: it reads the fixtures' gold fields to bound what the
  suite can award, and it ties the reference rung on all six families — the suite therefore shows no
  demonstrated headroom above the reference program.
- It does not evaluate B4–B7 (voice, memory stores, latency, cost sweep), long-horizon tasks, or real user
  data. Every fixture is synthetic and the loader refuses anything else.
- It does not call any model, network service or real calendar/mail/filesystem. There is no
  network-capable import anywhere under `eval/harness` and a self-check enforces that.
- It does not produce confidence intervals: the solvers are deterministic, so run-to-run variance is zero
  by construction.

## Layout

```
eval/
  harness/
    config.py        run identity, frozen thresholds, seeding, git provenance
    stats.py         Wilson interval, nearest-rank percentile, mean
    taskset.py       task-set loading + schema and synthetic-data validation (all six families)
    registry.py      capability registry loader/validator, coverage checks, evaluator digest
    planning.py      constraint checker shared by the planner solver and its scorer
    tools.py         in-memory calendar/mail/filesystem with validation, policy,
                     idempotency, fault injection and credential redaction
    solvers/
      base.py        Solver protocol, ToolContext, single idempotent retry
      stub.py        reference / naive / no-verify profiles (B1-B3, B8-B10)
      ladder.py      the X-13 rungs, including the gold-reading oracle probe
      mutations.py   the nine planted regressions used by X-10
    scorers.py       per-family scoring; pure functions of fixture + result + world
    runner.py        task x repeat execution, budgets, record hashing, per-subfamily summary
    ledger.py        JSONL result ledger, index, frozen baseline
    scorecard.py     Markdown + JSON scorecard
    gates.py         zero-tolerance invariants, regression thresholds, family_regression
    selftest.py      16 self-checks of the harness itself
    cli.py           command line entry point
  tasks/             synthetic fixtures: b1/{corpus,tasks}.json, b2/, b3/, b8/, b9/, b10/
  registry/          capabilities.json (evaluator-owned) + README
  baselines/         baseline-a-stub.json (Stage 0), baseline-b-stage1.json (six families)
  results/           committed evidence: *.jsonl runs, index.json, scorecards, x10/x13 reports
  tests/             76 unittest cases
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
