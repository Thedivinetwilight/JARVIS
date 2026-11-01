# Stage 0 — M2: Frozen Reference Baseline and Reproducibility

- **Status:** published (implementation milestone; evidence recorded 2026-11-01)
- **Published:** 2026-11-01
- **Milestone:** M2 of [the Stage 0 plan](stage-0-plan.md)
- **Depends on:** [M1 harness core](stage-0-m1-harness-core.md)
- **Baseline file:** `eval/baselines/baseline-a-stub.json` (`baseline_id` `ade894af6b158805`)
- **Evidence:** `eval/results/2026-11-01-B1-d157c95b15ba6c3d.jsonl`,
  `eval/results/2026-11-01-B2-95851d67af0a53c7.jsonl`,
  `eval/results/2026-11-01-B3-a134e5d7f9f7fec6.jsonl`, `eval/results/scorecard.md`
- **Code commit:** `2f2b41b` (baseline and evidence are committed with this note)

---

## 1. Exit criteria

Plan: *"Reference solver run → `baselines/baseline-a-stub.json`; rerun produces identical hash;
compare command reports no regression."* All three met. **[E]**

## 2. What is frozen

`python3 -m eval.harness baseline-freeze` runs all three blocks with the clean stub solver, writes
the ledger files, and then freezes this header plus, per block, the run id, task-set version and
sha256, config hash, repeats, the full summary and the sorted `outcome_hash` of every attempt.

| Field | Value |
|---|---|
| `baseline_id` | `ade894af6b158805` |
| created | `2026-11-01T11:56:55+00:00` |
| harness version | `0.1.0` |
| git revision | `2f2b41bc556f`, tracked files modified: `false` |
| config hash | `03b409597b1fa18828f5b258c02448fb3eee7a69ce931e766a8ae24ab2fbbb9c` |
| seed | `1729` |
| price sheet | none — Stage 0 makes no model calls |

| Block | Run id | Task set | Task-set sha256 | Repeats | Attempts | Success | False success | Citation precision | `pass^4` |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| B1 | `d157c95b15ba6c3d` | 2026-Q4.1 | `e10f2476c1fcfaca` | 1 | 8 | 8/8 | 0 | 1.00 (6 citations, 2 abstentions) | — |
| B2 | `95851d67af0a53c7` | 2026-Q4.1 | `42663f224f72e271` | 4 | 28 | 28/28 | 0 | n/a | 100.0% |
| B3 | `a134e5d7f9f7fec6` | 2026-Q4.1 | `e0a7f82f9aac30f3` | 1 | 7 | 7/7 | 0 | n/a | — |

Zero-tolerance counters are all zero in every block: false successes 0, approval violations 0,
duplicate writes 0, unauthorised side effects 0, credential-shaped strings in the ledger 0. Recorded
cost is `0.00 USD` on every attempt with `cost_is_synthetic: true`. **[E]**

The B1 numbers are worth reading carefully rather than celebrating: 6 of 8 tasks produced a cited
answer and all 6 citations hit a gold span; the other 2 tasks are unanswerable by construction and
the stub declined (`abstain_expected`). `citation_precision` is averaged over attempts that actually
cited, so abstaining cannot inflate it — which is exactly the loophole the `cite_top1_only` mutation
exists to punish (see [M3](stage-0-m3-planted-regression.md)). **[E]**

## 3. Reproducibility, measured not asserted

Three independent checks, all recorded:

1. **In-process rerun.** `deterministic_rerun` in the self-test: two runs of each block produce
   identical `outcome_hash` lists (8, 28 and 7 records). **[E]**
2. **Cross-process rerun.** A second `python3 -m eval.harness run --block B1 --block B2 --block B3`
   in a fresh process, writing to a different directory, reproduced the same run ids
   (`d157c95b15ba6c3d`, `95851d67af0a53c7`, `a134e5d7f9f7fec6`) and byte-equal hash lists for all 43
   attempts. *(Compared with a one-off script that read both ledgers through `read_run`; the same
   property is asserted continuously by the `cross_process_reproducibility` self-check.)* **[E]**
3. **Seed handling.** Seeds 1729 and 7 yield identical outcomes and identical `outcome_hash` values
   for the stub, while the run id differs: the seed is part of the run's identity but not of the
   observed outcome. This is the property that lets a later stochastic solver change behaviour
   without silently invalidating comparisons. **[E]**

## 4. What the gates say about the baseline against itself

`python3 -m eval.harness scorecard --run …B1 --run …B2 --run …B3` → `verdict: pass`, ledger digest
`1fef01cace883987`. Of the 24 gate rows, 15 pass and 9 skip; the skips are accounted for explicitly
(3 × `cost_ratio` because a zero-cost baseline has no ratio, 3 × `citation_precision_drop` outside
B1, 3 × `citation_precision_drop` where the block produced no citations). A skip is printed as a
skip, never as a pass. **[E]**

## 5. Why "Baseline A" is a harness baseline, not a model baseline

The frozen numbers are a 100% ceiling *by construction*: a deterministic stub answering 21 synthetic
tasks whose gold answers it can retrieve by keyword. Its uses are (a) proving the harness is
deterministic, (b) giving the gates something to regress *from*, and (c) making X-10 meaningful.
Its non-uses: comparing models, estimating J.A.R.V.I.S. capability, or setting expectations for a
real system. When a workhorse model is finally selected (Open decision O1), a **separate, clearly
named** model baseline must be frozen the same way — this file must not be overwritten in place,
because thresholds calibrated against a stub are not thresholds for a model. **[P]** that this
distinction holds up when real variance appears; **[U]** what a model's variance will do to the
`max_success_drop_pp = 0.0` threshold, which is only defensible for a deterministic solver.

## 6. Known gaps in the baseline

- 21 tasks, 43 attempts: no statistical claim survives at this size, and no confidence interval is
  published with the baseline. Phase 8's ±18 pp power note applies to a 48–80 task suite. **[U]**
- Only three of seven evaluation blocks are covered; B4 (voice), B5 (memory), B6 (latency/cost sweep)
  and B7 (long-horizon) are out of Stage 0 scope. **[E]**
- Latency numbers in the ledger (~0.1 ms median) measure the stub, not a system. **[E]**
- The baseline is stamped `git_dirty: false` with 2 untracked files (the results and baselines
  directories being written at the time); untracked files are counted separately so that writing
  evidence does not masquerade as an unreviewed source change. **[E]**

**What was learned, what remains uncertain, and the next research step.** *Learned:* reproducibility
is cheap to *claim* and not that hard to *verify* — but only if the record excludes everything that
legitimately varies (latency, run id, seed) while the run identity includes everything that must not
(git revision, config hash, task-set hash). The self-check that mattered was cross-process equality,
not same-process determinism. *Uncertain:* whether the same identity scheme survives a solver that
calls out to a remote, nondeterministic model — sampling and provider-side changes will need a
recorded decode configuration and probably a tolerance in the gates. *Next research step:* M3 — plant
the six known regressions and require each to be caught by its expected gate
([note](stage-0-m3-planted-regression.md)).
