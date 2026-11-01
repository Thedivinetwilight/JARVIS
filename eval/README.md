# `eval/` — Stage 0 evaluation harness

A small, standard-library-only harness that runs task blocks **B1** (retrieval with
citations), **B2** (single-tool execution under policy) and **B3** (multi-step state
mutation with read-back verification) reproducibly, records every attempt, compares
runs against a frozen baseline, and demonstrates that a deliberately planted
regression is caught (experiment **X-10**).

Design background: [`docs/research/08-evaluation-and-improvement.md`](../docs/research/08-evaluation-and-improvement.md),
[`docs/research/10-proposed-architecture-and-experiments.md`](../docs/research/10-proposed-architecture-and-experiments.md),
and the plan in [`docs/research/stage-0-plan.md`](../docs/research/stage-0-plan.md).

## What this harness measures

- **Reproducibility.** A run is identified by `(git sha, config hash, task-set hash, seed)`.
  Each attempt carries an `outcome_hash` over the observed outcome only (status, success,
  metrics, detail, steps, cost) — not latency, not the run id — so two runs can be compared
  field by field, across processes.
- **Correctness against a specification**, not style: keyword answers plus citation precision
  (B1), final world state plus argument exactness (B2), independent read-back verification (B3).
- **Honesty**: a `false_success` is a run that claimed `done` while the world or the citations
  disagreed. Repeated blocks also report `pass^k` (all of the first k attempts correct).
- **Safety invariants** as zero-tolerance gates: approval violations, duplicate writes after an
  ambiguous timeout, unauthorised irreversible actions, credential-shaped strings in the log.

## What this harness does **not** measure

- **It does not measure a model.** The Stage 0 reference solver (`solvers/stub.py`) is a
  deterministic keyword-retrieval + templated-execution stub. It exists to exercise the harness.
  Its 100% score on the synthetic fixtures is a *ceiling set by construction*, not a capability
  result, and it must never be quoted as one.
- It does not evaluate B4–B7 (voice, memory, latency, cost sweep), long-horizon tasks, or
  real user data. Every fixture is synthetic and the loader refuses anything else.
- It does not call any model, network service or real calendar/mail/filesystem. There is no
  network-capable import anywhere under `eval/harness` and a self-check enforces that.

## Layout

```
eval/
  harness/
    config.py        run identity, frozen thresholds, seeding, git provenance
    stats.py         Wilson interval, nearest-rank percentile, mean
    taskset.py       task-set loading + schema and synthetic-data validation
    tools.py         in-memory calendar/mail/filesystem with validation, policy,
                     idempotency, fault injection and credential redaction
    solvers/
      base.py        Solver protocol, ToolContext, single idempotent retry
      stub.py        reference solver (B1/B2/B3) — deliberately simple
      mutations.py   the six planted regressions used by X-10
    scorers.py       per-block scoring; pure functions of fixture + result + world
    runner.py        task x repeat execution, budgets, record hashing
    ledger.py        JSONL result ledger, index, frozen baseline
    scorecard.py     Markdown + JSON scorecard
    gates.py         zero-tolerance invariants and regression thresholds
    selftest.py      12 self-checks of the harness itself
    cli.py           command line entry point
  tasks/             synthetic fixtures: b1/{corpus,tasks}.json, b2/, b3/
  baselines/         baseline-a-stub.json — frozen reference baseline
  results/           committed evidence: *.jsonl runs, index.json, scorecard, x10 report
  tests/             58 unittest cases
  scripts/           x10_demo.sh — end-to-end demonstration
```

## Commands

```bash
python3 -m eval.harness selftest        # harness self-checks (must be 12/12)
python3 -m eval.harness list            # task sets and mutations
python3 -m eval.harness run --block B1 --block B2 --block B3
python3 -m eval.harness baseline-freeze # freeze the clean stub as baseline A
python3 -m eval.harness x10             # planted-regression test: must fail the gates
python3 -m eval.harness scorecard --run <file.jsonl> [--run ...] --out scorecard.md
python3 -m eval.harness gates --run <file.jsonl>
python3 -m unittest discover -s eval/tests -t .
bash eval/scripts/x10_demo.sh           # everything above, in order
```

`x10` exits non-zero if any mutation escapes **its expected gate**. A gate that fires for an
unrelated reason does not count: X-10 asks whether the intended invariant is actually protected.

## Rules for changing this harness

1. Fixtures are frozen per version. Editing a task set changes its `sha256`, which changes the run
   identity, and requires a new version string — never an in-place edit of a published set.
2. A new check must come with a way to fail. If you add a metric, add the mutated behaviour that
   that metric is supposed to catch (see `solvers/mutations.py`).
3. Only synthetic data. Real names, addresses, credentials or user content are rejected by the
   loader and would break the Stage 0 constraints.
4. Results are evidence: `results/` is append-only in spirit. Re-running produces a new file
   rather than editing an old one.
