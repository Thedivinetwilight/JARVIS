# Stage 1 — M4: Capability Families (B8–B10), Registry and the Solver Ladder (X-13)

- **Status:** published (implementation milestone; evidence recorded 2026-11-01)
- **Published:** 2026-11-01
- **Milestone:** M4 of [the Stage 1 recommendation](15-revised-roadmap-supersession-and-next-step.md#5-the-recommended-next-step-stage-1--discriminative-multi-family-evaluation--capability-registry)
- **Experiment:** [X-13](15-revised-roadmap-supersession-and-next-step.md#5-the-recommended-next-step-stage-1--discriminative-multi-family-evaluation--capability-registry)
  (solver ladder), plus a nine-mutation re-run of X-10
- **Depends on:** [Stage 0 M1](stage-0-m1-harness-core.md), [M2](stage-0-m2-frozen-baseline.md),
  [M3](stage-0-m3-planted-regression.md); [Phase 12](12-capability-integration-architecture.md)
  (capability registry), [Phase 13](13-evolving-intelligence-update-pipeline.md) (memory evolution)
- **Code commits:** `e8a2611` (families, registry, ladder, family gate), `1fbf17e` (ladder report)
- **Evidence commits:** `8620e70` (baseline-b), `37681c6` (X-10), `69c260a` (X-13, scorecard)
- **Evidence:** `eval/baselines/baseline-b-stage1.json`; 39 new `eval/results/*.jsonl` runs;
  `eval/results/x10-report-stage1.json`; `eval/results/x13-ladder.md`/`.json`;
  `eval/results/scorecard-stage1.md`/`.json`
- **Code:** `eval/harness/{registry,planning,scorers,runner,gates,taskset,cli}.py`,
  `eval/harness/solvers/{stub,ladder,mutations,__init__}.py`, `eval/tasks/b8|b9|b10/tasks.json`,
  `eval/registry/capabilities.json`, `eval/tests/test_stage1.py`
- **Classification of the claims below:** **[E]** measured in this repository · **[P]** plausible but
  not measured here · **[U]** unresolved

---

## 1. What Stage 1 was for

Stage 0 answered "can the harness be trusted?" and left a harder question open: it measured three
narrow blocks with a stub that scored 100%, so any comparison would have saturated and any suite would
have looked discriminative for the wrong reason. Stage 1 therefore added **breadth as an inventory**
(not as a claim), a **model-free ladder** that must separate weak from strong behaviour before a model
is allowed to define the numbers, and — because every family can now regress independently — a gate
that fails when one family collapses behind an aggregate gain.

Nothing here builds the assistant, connects an account, calls a model, or measures J.A.R.V.I.S. The
only systems under test are hand-written deterministic programs in `eval/harness/solvers/`. **[E]**

## 2. What was added

### 2.1 Three capability families (30 new synthetic tasks, 52 in total)

| Family | Tasks | What a task requires | Scored on |
|---|---:|---|---|
| **B8** breadth | 18 (6 extraction, 6 synthesis, 6 planning) | pull six fields out of an invoice; reconcile four short policy documents and *name* the disagreeing source; assign meetings to rooms and slots under capacity, busy-slot, `at`/`before`/`after` and room-separation constraints | field exactness **and** a citation that is one of the gold spans; majority value **and** the exact conflict set; every constraint satisfied by an independently re-checked assignment |
| **B9** skill reuse | 6 | apply a stored weekly bundle skill (`weekly_bundle_v2`) whose instruction is under-specified, with a documented fallback when it cannot be applied | independent acceptance checks over the resulting files **plus** a `used_skill` flag in the outcome |
| **B10** memory evolution | 6 | consolidate seeded facts carrying trust levels (`verified`/`reported`/`untrusted`) and validity windows | the surviving/superseded/expired sets, the answer keywords, and zero occurrences of the forbidden (poisoned or stale) value |

Schema validation was extended with the families, not after them: a B8 extraction task with no citable
span is rejected at load time, a B9 task with no acceptance checks is rejected (the family measures
whether a stored skill is applied, so checks are mandatory), and a B10 task that reuses a fact across
two outcome sets, or carries an unknown trust label, is rejected (`eval/tests/test_stage1.py`). **[E]**

### 2.2 Capability registry (`eval/registry/capabilities.json`, v1.0.0)

Thirteen entries — 5 tools, 6 skills, 1 verifier, 1 policy — each with a contract (preconditions,
postconditions, argument schema), a **synthetic** cost profile (`usd_per_call = 0.0`; the loader refuses
non-synthetic profiles), idempotency class, failure modes, a test reference, an evidence anchor, a
family list and an owner (`project-owner (unassigned at Stage 1)`).

The registry is **evaluator-owned**. Three properties are enforced rather than asserted:

1. **Coverage is checked against the fixtures.** `tools_used_by_fixtures()` walks the task payloads and
   `validate_registry()` reports any capability a fixture uses but the registry does not define. Dropping
   `schedule.assign` from the registry makes the self-test fail (`test_unregistered_tool_used_by_a_fixture_is_reported`). **[E]**
2. **No capability may claim authority over the evaluator or policy.** Ids beginning `eval.`, `harness.`,
   `gate.`, `baseline.`, `permission.` or `policy.admin` are rejected. **[E]**
3. **The system under test cannot write it.** A solver receives a task and a `ToolContext`; it never
   receives a registry handle. `evaluator_digest()` hashes every file under `eval/registry/`,
   `eval/tasks/*/` and `eval/baselines/`, and the self-check `evaluator_isolation` confirms the digest is
   byte-identical after full-family runs, including a mutated one. **[E]**

Registry edits are also visible in provenance: the registry sha256 is part of every run id, so results
recorded before and after a registry change cannot be silently pooled. **[E]**

### 2.3 Solver ladder (X-13) and one new gate

Four deterministic rungs, ordered by how much of the intended capability they implement:

| Rung | Implementation |
|---|---|
| `oracle_ceiling` | reads the fixture's gold fields; exists only to bound what the suite can award — never a system, never a promotion candidate |
| `stub` | the Stage 0 reference profile, extended to B8–B10: IDF retrieval with citations, policy-aware execution, read-back verification with repair, schema extraction, majority reconciliation, backtracking search, trust-ordered consolidation |
| `stub_naive` | a plausible shallow program: last-span retrieval, no conflict detection, greedy first-fit planning (no backtracking), recency-only memory, stored skills ignored |
| `stub_no_verify` | reference logic with read-back verification and citations removed — it produces work and declares success without checking it |

`family_regression` is the new gate: for each family it computes the drop against the frozen baseline,
fails if **any** family drops beyond the threshold, and reports when the aggregate would have masked
that drop ("masked by other families"). Its negative control is a synthetic case where B1 improves by
50 pp with 100 attempts while B8 collapses by 80 pp with 10 attempts — the aggregate improves by 38 pp
and the gate still fails (`family_regression_masking`). **[E]**

## 3. Results

### 3.1 X-13: the ladder passes, and it passes for a specific reason

`python3 -m eval.harness x13` → exit **0**; artifact `eval/results/x13-ladder.md` (+ `.json`), 24
recorded runs. **[E]**

| Family | `oracle_ceiling` | `stub` | `stub_naive` | `stub_no_verify` |
|---|---:|---:|---:|---:|
| B1 retrieval | 8/8 (100%) | 8/8 (100%) | 0/8 (0%) | 0/8 (0%) |
| B2 tools | 28/28 (100%) | 28/28 (100%) | 28/28 (100%) | 28/28 (100%) |
| B3 multi-step | 7/7 (100%) | 7/7 (100%) | 6/7 (86%) | 6/7 (86%) |
| B8 breadth | 18/18 (100%) | 18/18 (100%) | 4/18 (22%) | 12/18 (67%) |
| B9 skill reuse | 6/6 (100%) | 6/6 (100%) | 0/6 (0%) | 6/6 (100%) |
| B10 memory | 6/6 (100%) | 6/6 (100%) | 0/6 (0%) | 6/6 (100%) |

Adjacent spreads of at least 10 pp: B1 +100.0 pp, B9 +100.0 pp, B10 +100.0 pp, B8 +77.8 pp, B3 +14.3 pp;
B2 +0.0 pp. The threshold (≥10 pp on ≥1 family) is met by five families, so the exit criterion passes. **[E]**

**Three readings matter more than the pass.**

1. **The oracle ties the reference everywhere.** Once a hand-written program that already implements the
   intended behaviour is scored, the ceiling probe can only confirm it: there is no headroom left above
   the reference rung in this fixture set. X-13 therefore proves the suite can rank *programs that differ
   in behaviour*; it does not show the suite has room for a better system. **[E]**
2. **B2 has no ladder signal at all.** All four rungs score 100%: the families' discrimination on tool use
   comes entirely from the planted mutations (§3.2), not from the ladder. A model that is merely
   "shallower" than the reference on B2 would not show it. **[E]**
3. **B9 and B10 do not measure self-verification.** `stub_no_verify` ties the reference on both, because
   their scorers check the resulting world/memory state independently of what the solver claims. The
   families measure *outcome*, not *checking*. Self-verification is currently measured only in B3 and in
   B8 extraction (via citations). **[E]**

The largest family spread belongs to `stub_naive` on B8 (4/18, 22%, against 18/18 for the reference):
it fails all six extraction tasks on four fields *and* the citation, all six synthesis tasks on the
conflict set, and two of six planning tasks where first-fit dead-ends — the three sub-failures are
separable in `summary.by_subfamily`. **[E]**

### 3.2 X-10 with nine planted regressions: 9 of 9 caught by their expected gate

`python3 -m eval.harness x10 --baseline eval/baselines/baseline-b-stage1.json` → exit **0**;
`eval/results/x10-report-stage1.json`, 9 mutant runs. **[E]**

| Mutation | Block | Clean | Mutant | Expected gate | Gates that fired |
|---|---|---:|---:|---|---|
| `drop_timezone` | B2 | 1.000 | 0.714 | `success_drop` | `success_drop=0.7143` |
| `retry_without_idempotency` | B2 | 1.000 | 0.857 | `duplicate_writes_zero` | `false_successes_zero=4`, `duplicate_writes_zero=4`, `success_drop=0.8571` |
| `attempt_blocked_action` | B2 | 1.000 | 1.000 | `unauthorized_side_effects_zero` | `unauthorized_side_effects_zero=4` |
| `cite_top1_only` | B1 | 1.000 | 0.750 | `false_successes_zero` | `false_successes_zero=2`, `success_drop=0.75`, `citation_precision_drop=0.75` |
| `drop_last_step` | B3 | 1.000 | 0.571 | `success_drop` | `success_drop=0.5714` |
| `skip_verification` | B3 | 1.000 | 0.857 | `false_successes_zero` | `false_successes_zero=1`, `success_drop=0.8571` |
| **`skip_conflict_detection`** | B8 | 1.000 | 0.667 | `success_drop` | `false_successes_zero=6`, `success_drop=0.6667` |
| **`disable_skill_reuse`** | B9 | 1.000 | 0.000 | `success_drop` | `success_drop=0.0` |
| **`accept_poisoned_memory`** | B10 | 1.000 | 0.000 | `false_successes_zero` | `false_successes_zero=6`, `success_drop=0.0` |

Two rows are instructive. `disable_skill_reuse` moves accuracy to zero but produces **no false
successes**: the solver that ignores the stored skill writes the wrong bundle, notices the acceptance
checks fail, and reports failure. Honest failure is the behaviour we want, but it means the family
currently punishes inability rather than rewarding bluffing — the bluffing case is covered by the B10
row, where the poisoned fact is accepted and six attempts claim success while being wrong. **[E]**

### 3.3 Baseline, gates and reproducibility

- **Baseline-b** (`eval/baselines/baseline-b-stage1.json`, `git_dirty: false`, sha `1fbf17e4d647`):
  six families, 73 attempts, reference solver, all 100%. **[E]**
- **Scorecard** (`eval/results/scorecard-stage1.md`): six blocks, every zero-tolerance gate 0, citation
  precision 1.00 on B1, `cost_ratio` explicitly `skip` ("baseline cost is zero"), and
  `family_regression` **pass** — "worst family B1 +0.0 pp, aggregate +0.0 pp across 6 families". **[E]**
- **Reproducibility:** identical outcome hashes and run ids on rerun; 28 B2 records reproduced identically
  in two separate processes; registry and task-set hashes are part of run identity. `python3 -m
  eval.harness selftest` → **16/16**; `python3 -m unittest discover -s eval/tests -t .` → **76 tests OK**
  (the 58 Stage 0 cases plus 18 new ones; three Stage 0 assertions were updated to the new suite
  size — block list, self-check count and mutation count — because they assert the harness's own
  dimensions). **[E]**
- **Isolation:** no new run changed any evaluator-owned file, including the mutated run
  (`evaluator_isolation`). **[E]**

## 4. What this does and does not establish

**Established here [E]:** the suite has six independently-gated families with a frozen baseline; three
new families reject malformed fixtures at load time; nine planted regressions across five families are
each caught by the gate that protects the invariant they break; a capability registry can be checked
against the fixtures and cannot be written by the system under test; a hand-written shallow program is
separated from a hand-written careful one by ≥10 pp on five of six families.

**Not established [U]:** that these families exercise *real* assistant failure modes; that the
`stub_naive` profile resembles any model's weak behaviour (it was written by the same author as the
fixtures); that thresholds of 0 pp / 5 pp survive a sampled model; that a model scoring 60% on B8 is
better at breadth than one scoring 55%; anything about cost, latency, calibration or user preference.

**Still cannot be measured by this harness** — Phase 15 §7 lists ten limits; Stage 1 removes none of them
and adds four more that are specific to this milestone:

1. **The ladder rungs are authored, not sampled.** "Weak" means "what I chose to leave out of a
   hand-written program". A model can be weak in ways no rung models.
2. **Ceiling saturation.** Because the oracle ties the reference on every family, the fixtures contain no
   difficulty the reference program does not already implement; a future model comparison can therefore
   still saturate at 100% and be uninformative.
3. **Deterministic scores have no variance.** Every number in §3 has zero run-to-run spread by
   construction; no confidence interval, no pass^k beyond B2.
4. **The registry's `tests` field points at unit tests, not at behaviour.** An entry says "this
   capability is defined and tested in code", never "the system can do this at human level"; every owner
   is `unassigned`, which is a tracked gap rather than a pass.

## 5. What was learned, what remains uncertain, and the next research step

**What was learned.** A suite can pass its own discrimination test for a reason you did not intend: the
ladder separates weak from strong behaviour, but the gold-reading ceiling probe ties the reference
program on all six families, so what X-13 actually certifies is "this suite can rank hand-written
programs", not "this suite has headroom for a better system". The gap is visible only because the ladder
included a probe that has no incentive to be modest. Two further findings came from looking at where the
rungs *tie* rather than where they differ: B2 discriminates nothing (its signal is entirely in the
mutations), and B9/B10 tie for a `stub_no_verify` rung because their scorers read the world instead of
the solver's self-report — so self-verification is measured in only two of six families. Breadth, in
other words, arrived as an inventory with contracts, coverage checks and an isolation digest, and the
inventory immediately showed which families are shallow.

**What remains uncertain.** Whether any of these six families correlates with real assistant work at all;
whether a sampled model's failures fall inside the behaviour space the mutations cover; where thresholds
should sit once scores are stochastic; and whether the difficulty backlog below is the right one — the
oracle tie means the suite's headroom cannot be inferred from its own fixtures. All of it is unresolved
by design, because no model has been run. **[U]**

**The next research step** is not model selection by preference but model measurement by the same
harness: run 2–3 sampled model configurations (O1) through all six families with variance, cost and
latency recorded, keeping the ladder and the mutations as controls, and treat the known blind spots as
the difficulty backlog — B2 needs a ladder-relevant rung (e.g. an argument-repair program), B9 needs an
*acquisition* task rather than only reuse, and every family needs at least one item the reference
program cannot solve for free. Until the fixtures contain that item, a model's 100% and the reference
program's 100% are the same statement about the fixtures, not about capability.
