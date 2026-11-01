# Stage 0 — M1: Evaluation Harness Core

- **Status:** published (implementation milestone; evidence recorded 2026-11-01)
- **Published:** 2026-11-01
- **Milestone:** M1 of [the Stage 0 plan](stage-0-plan.md)
- **Implements:** [Phase 8 §2–§9](08-evaluation-and-improvement.md) (blocks B1–B3, gates) and
  [Phase 10 §9](10-proposed-architecture-and-experiments.md#9-staged-roadmap) (Stage 0)
- **Code commit:** `2f2b41b` — "feat(eval): Stage 0 harness core for task blocks B1-B3"
- **Evidence:** `eval/baselines/baseline-a-stub.json`, `eval/results/*.jsonl`,
  `eval/results/scorecard.md`, `eval/results/x10-report.json`

Evidence labels used below: **[E]** established by a command that was run (command and output are
quoted), **[P]** plausible but not measured here, **[U]** unproven/unknown.

---

## 1. Exit criteria for M1

From the plan: *"`python -m eval.harness selftest` green; B1/B2/B3 run to completion; unit tests
pass."* All three were met. **[E]**

```text
$ python3 -m eval.harness selftest
12/12 checks passed                                   # exit 0

$ python3 -m unittest discover -s eval/tests -t .
Ran 58 tests in 0.887s
OK                                                    # exit 0

$ python3 -m eval.harness run --block B1 --block B2 --block B3
B1 run d157c95b15ba6c3d: 8/8 successful, false_success=0
B2 run 95851d67af0a53c7: 28/28 successful, false_success=0
B3 run a134e5d7f9f7fec6: 7/7 successful, false_success=0
```

The three clean run files are `eval/results/2026-11-01-B1-d157c95b15ba6c3d.jsonl`,
`eval/results/2026-11-01-B2-95851d67af0a53c7.jsonl`,
`eval/results/2026-11-01-B3-a134e5d7f9f7fec6.jsonl`. Every attempt in them was executed by the
commands above; nothing in this note comes from a run that was not recorded on disk.

## 2. What the harness is

Standard-library Python only (no third-party dependency, no network, no model client). 2,937 lines
of code across harness, tests and the demo script, plus 16 KB of synthetic fixtures.

| Module | Lines | Responsibility |
|---|---:|---|
| `eval/harness/config.py` | 138 | run identity, canonical hashing, frozen thresholds, git provenance |
| `eval/harness/stats.py` | 41 | Wilson interval, nearest-rank percentile, mean (for B4+ later) |
| `eval/harness/taskset.py` | 130 | fixture loading, schema validation, synthetic-only enforcement |
| `eval/harness/tools.py` | 235 | in-memory calendar/mail/filesystem: validation, policy, idempotency, fault injection, redaction |
| `eval/harness/solvers/base.py` | 70 | `Solver` protocol, `ToolContext`, one idempotent retry |
| `eval/harness/solvers/stub.py` | 200 | deterministic keyword-retrieval + templated-execution reference solver |
| `eval/harness/solvers/mutations.py` | 73 | the six planted regressions as data (block, expected gate, phase requirement) |
| `eval/harness/scorers.py` | 179 | B1/B2/B3 scoring, false-success accounting |
| `eval/harness/runner.py` | 255 | task × repeat execution, budgets, hashed result records, `pass^k` |
| `eval/harness/ledger.py` | 153 | JSONL result ledger, index, frozen baseline |
| `eval/harness/scorecard.py` | 125 | Markdown + JSON scorecard |
| `eval/harness/gates.py` | 141 | zero-tolerance invariants and regression thresholds |
| `eval/harness/selftest.py` | 251 | 12 self-checks of the harness itself |
| `eval/harness/cli.py` | 285 | `list · run · baseline-freeze · gates · scorecard · x10 · find · selftest` |
| `eval/tests/*.py` | 583 | 58 unittest cases |
| `eval/scripts/x10_demo.sh` | 50 | end-to-end demonstration |

## 3. Design decisions that the code now enforces

1. **Run identity is data.** `run_id = sha256(taskset sha, config hash, solver, mutations, git
   revision, git dirty flag)`; the config hash covers harness version, seed, repeats and all
   thresholds. A run cannot be described more loosely than that. **[E]**
2. **`outcome_hash` covers the observed outcome only** — status, success, false-success flag,
   metrics, detail, steps, error, cost — and deliberately excludes wall clock latency, run id and
   seed, so the same task on the same configuration hashes the same in any process. Identity fields
   are recorded next to the hash, not inside it. **[E]** (verified by two processes producing equal
   hash lists, and by a unit test that mutates latency after the fact)
3. **Tool arguments are untrusted input validated by code** (Phase 9 §4): required fields, date/time
   shapes, positive durations, no path traversal, no unknown tools. Validation failures are counted,
   never silently repaired. **[E]**
4. **Side effects are at-most-once** (Phase 9 §5): a simulated transport timeout can fire *after* the
   effect is applied; a retry must reuse the idempotency key. A fresh key after a timeout is counted
   as a duplicate write, and the world state shows two messages. **[E]**
5. **Approval and irreversibility are policy, not prompt text** (Phase 9 §6): policy can require
   approval (`auto_deny` fixtures make the scripted principal refuse) or forbid irreversible actions
   outright; denied attempts are counted as unauthorised side effects even though the tool did not
   apply them. **[E]**
6. **Honesty is scored**: `false_success` = the solver said `done` while the world, the arguments or
   the citations disagreed. **[E]**
7. **Fixtures are synthetic and the loader refuses anything else**; secrets are redacted from the
   audit trail before anything reaches the ledger. **[E]**
8. **Repair is part of execution, not a hidden retry**: B3 task `b3-007` is deliberately
   under-specified, so a solver must read back and repair; a solver that skips verification reports
   success on a wrong world and is caught (§[M3](stage-0-m3-planted-regression.md)). **[E]**

## 4. The twelve harness self-checks

| Check | What it proves |
|---|---|
| `task_sets_load` | three blocks load and validate; shas `e10f2476…`, `42663f22…`, `e0a7f82f…` |
| `deterministic_rerun` | two in-process runs give identical outcome hashes (B1 8, B2 28, B3 7 records) |
| `seed_invariance` | seeds 1729 and 7 give identical outcomes and hashes; only run identity changes |
| `cross_process_reproducibility` | two separate `python -m eval.harness run` processes reproduce 28/28 identical hashes |
| `mutation_detection` | all six planted regressions detected, each by its expected gate |
| `gate_negative_controls` | all seven gate types fire on planted breakage (1 false success, 2 duplicates, 1 unauthorised effect, 3 approval violations, −50 pp success, 0.5 citation precision, 2 secret leaks) |
| `cost_gate_skip` | the cost gate skips *explicitly* when baseline cost is zero instead of silently passing |
| `ledger_roundtrip` | a written ledger file reads back with identical record hashes |
| `unverified_write_rejected` | a B3 task that writes a file with no verifier assertion is refused |
| `synthetic_only` | a task set marked `synthetic: false` is refused |
| `secret_redaction` | `token=…` content is `[REDACTED]` in the audit trail |
| `no_network_imports` | no `urllib`, `requests`, `httpx`, `socket`, `openai`, `anthropic` or `inspect_ai` import anywhere in `eval/harness` |

## 5. Deviations from the M0 plan (and why)

| Plan | What was built | Reason |
|---|---|---|
| 4 mutations | 6 mutations | B2 needed an approval/irreversibility regression and B3 needed a "claimed done without verifying" regression to cover the gates the plan lists |
| mutations inside `stub.py` | `solvers/mutations.py` as data | lets X-10 assert the *expected* gate per mutation rather than merely "something failed" |
| `tests/test_harness.py`, `tests/test_fixtures.py` | six focused test modules | 58 cases are easier to attribute per subsystem |
| CLI `compare`/`gate`/`hash` | `gates`, `scorecard`, `find`, `x10` | each reported number needs a file to point at; `hash` needed no separate verb |
| `baselines/baseline-a-stub.json` | same name, kept | matches the plan |
| no `pass^k` in the scorecard | `pass^k` added | Phase 8 asks for all-of-k on multi-turn blocks; B2 runs four attempts per task |

## 6. What M1 does not establish

- **The 100% success rates are a ceiling, not a capability result.** The reference solver is a
  keyword stub and the fixtures are 21 tasks written so that a defensible gold answer exists and is
  retrievable; the baseline therefore measures *harness behaviour*, and `eval/README.md` says so in
  its first paragraph. **[E]**
- Statistical power is meaningless at 21 tasks: Phase 8's ±18 pp power note applies to a 48–80 task
  suite, not to this fixture set. The harness records the numbers; it does not license inference from
  them. **[U]** until the fixture set grows.
- Nothing here touches a model, a provider, hardware, latency or cost: the recorded cost is `0.00`
  and every record carries `cost_is_synthetic: true`. **[E]**

**What was learned, what remains uncertain, and the next research step.** *Learned:* a harness that
records its own inputs can be self-checked, and the checks that mattered were the negative ones —
mutations, gate negative controls, unverified-write rejection, no-network enforcement — not the green
success rates. *Uncertain:* whether the same gates behave sensibly once a model, with variance,
supplies the answers; the stub's determinism hides every question about noise. *Next research step:*
M2 — freeze the reference baseline and verify a rerun reproduces it exactly
([note](stage-0-m2-frozen-baseline.md)).
