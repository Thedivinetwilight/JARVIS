# Stage 0 — Pre-coding Plan (Harness and Baseline)

- **Status:** published (plan, approved for implementation by the Stage 0 brief)
- **Published:** 2026-11-01
- **Scope:** Stage 0 of the [roadmap in Phase 10 §9](10-proposed-architecture-and-experiments.md#9-staged-roadmap)
- **Implements:** [Phase 8 §2–§9](08-evaluation-and-improvement.md) and experiment
  [X-10](10-proposed-architecture-and-experiments.md#8-experiments-that-could-falsify-this-recommendation)
- **Constraints accepted for this stage:** no model credentials, no personal accounts, no external
  actions, no application features, no change to the repository root `README.md`.

---

## 1. What Stage 0 must deliver

From Phase 10 §9, Stage 0 exits only when:

1. **X-10 passes** — the harness detects a deliberately planted regression and fails the release gate.
2. **B1/B2/B3 run reproducibly** — same inputs produce the same recorded results.

The brief adds: record results, compare against a frozen baseline, keep personal data and credentials
out of fixtures and logs, and publish a dated progress note per milestone.

**Explicitly out of scope:** choosing a model, provider or hardware; any hosted or local model call;
connectivity to real calendars, mail, files, or web; B4–B7; the full Inspect/lm-eval integration; any
application feature.

---

## 2. Planned files

```
eval/
  README.md                     # what this is, how to run it, what it does NOT measure
  harness/
    __init__.py                 # package version + public API
    cli.py                      # run | compare | gate | selftest | hash commands
    config.py                   # RunConfig, canonical hashing, thresholds, seeds
    taskset.py                  # task-set loading + schema validation
    tools.py                    # synthetic calendar/mail/filesystem + world state
    solvers/
      base.py                   # Solver interface, ToolContext, mutation hooks
      stub.py                   # reference solver v0 (deterministic, imperfect)
      mutations.py              # X-10 mutations: drop_timezone, skip_verification,
                                #                 retry_without_idempotency, cite_top1_only
    scorers.py                  # B1 citation/groundedness, B2 world-state, B3 verifier
    runner.py                   # task x repeat execution, budgets, fault injection
    ledger.py                   # per-attempt records, JSONL results store
    stats.py                    # Wilson intervals, paired comparison helpers
    scorecard.py                # aggregation + scorecard rendering
    gates.py                    # zero-tolerance invariants + regression thresholds
  tasks/
    b1/{corpus.json,tasks.json}
    b2/tasks.json
    b3/tasks.json
  baselines/
    baseline-a-stub.json        # frozen snapshot: config hash, task-set hashes, outcomes, metrics
  results/                      # committed evidence: manifest, results.jsonl, scorecard, gate
  tests/
    test_harness.py             # unittest: determinism, scoring, gates, X-10 (all four mutations)
    test_fixtures.py            # schema, synthetic-data and secret hygiene checks
  scripts/
    x10_demo.sh                 # end-to-end demonstration of X-10 (clean pass, mutated fail)
```

Fixtures are **synthetic**: fictional organisations, `*.example` / `*.test` addresses, no real names,
no personal data, no credentials. Every task file carries `"synthetic": true` and
`"data_class": "synthetic"`, and a test enforces it.

---

## 3. Milestones

| # | Milestone | Contents | Exit evidence |
|---|---|---|---|
| **M0** | This plan | Scope, files, blocking questions, fixture policy | This document + index entry |
| **M1** | Harness core | Schema, runner, tools, scorers, ledger, scorecard, gates, determinism test | `python -m eval.harness selftest` green; B1/B2/B3 run to completion; unit tests pass |
| **M2** | Frozen baseline | Reference solver run → `baselines/baseline-a-stub.json`; rerun produces identical hash; compare command reports no regression | Two runs, identical `outcome_hash`; gate exit 0 |
| **M3** | Planted regression (X-10) | Four mutations run through the same gate; each must fail with a named gate; X-10 note records exit codes and metrics | Gate exit 1 for every mutation; recorded JSONL + scorecard; summary note |

Each milestone is a separate commit containing its progress note and an index update, with the commit
hash and GitHub paths reported.

---

## 4. Phase 1 questions: which block implementation

| Question | Blocks Stage 0? | Why |
|---|---|---|
| Q1 primary surface (desktop/mobile/voice) | No | Affects B6 and Stage 4; fixtures are interface-agnostic |
| Q2 local-only data classes | **Partially** | Stage 0 makes no model calls, so nothing leaks. It *does* block the Stage 1 model/hosting decision and the privacy-scoping assertions in real B1 data |
| Q3 monthly cost ceiling | No | Stub runs cost $0.00; cost thresholds are implemented but marked `cost_is_synthetic: true` |
| Q4 writable accounts | **Yes — for real B2** | The synthetic tool set is invented. Final policy semantics (which operations need approval, which scopes exist) cannot be fixed without knowing what the user will actually connect |
| Q5 unattended operation | No | Affects approval asynchrony in Stage 2 |
| Q6 approval tolerance | **Yes — for B2 thresholds** | The auto-approval stub assumes confirmation is available; approval-fatigue thresholds need the user's answer |
| Q7 languages/accents | No | B6 only |
| Q8 hardware | No | Stage 0 is hardware-free by design (no inference at all) |
| Q9 retention | No | Memory stage |
| Q10 regulated data | **Yes — for any real fixture** | Synthetic fixtures avoid the question entirely; real corpora cannot be created before this is answered |
| Q11 irreversible-action policy | **Yes — for B2 gate semantics** | "Block, or ask" changes the expected outcome of Tier 3 tasks; Stage 0 encodes *both* as separate fixture cases rather than choosing |
| Q12 trace visibility | No | Affects the UI, not the ledger (the ledger records everything regardless) |

**Net:** no Phase 1 question blocks **writing or running** Stage 0. Four questions (Q2, Q4, Q6, Q11)
block **replacing synthetic fixtures with the user's real workload**; Q10 blocks any real data at all.
The harness therefore ships with an explicit `--taskset` seam so real fixtures can be added later
without code changes.

---

## 5. What can proceed on synthetic fixtures, without assuming user preferences

| Part | Synthetic stand-in | What is *not* assumed |
|---|---|---|
| B1 corpus and questions | Fictional policy/status/appointment/router documents, `*.example` addresses | Topic interests, reading habits, real documents |
| B2 tools and policies | `calendar`, `mail`, `files` with fixed schemas; both "confirm" and "block" variants for irreversible actions | Which accounts the user will connect, how much confirmation they want |
| B3 plans | In-memory sandbox filesystem with arithmetic/file assertions | The user's actual long-running goals |
| Budgets and thresholds | Phase 1 provisional envelopes, implemented as configurable constants | That the user accepts those envelopes (they remain provisional) |
| Costs | Recorded as 0.00 with `cost_is_synthetic: true` | Any price, provider, or subscription plan |

The harness deliberately **encodes contradictions rather than resolving them**: where Phase 1 leaves a
policy open (Tier 3 = block vs. ask, Q11), fixtures exist for both behaviours and the gate checks
consistency with the declared policy, not with a preferred policy.

---

## 6. Design decisions taken for this stage

1. **Standard library only.** Stage 0 must run in any checkout with no network and no lockfile
   drift. Inspect and lm-evaluation-harness remain the planned hosts for the *agent loop* and public
   capability checks (Phase 8 §2.1); Stage 0 exposes a solver interface so Inspect can wrap it later.
   Rationale and the installability check are recorded in the M1 progress note.
2. **The reference solver is a deliberately imperfect deterministic stub**, not an oracle. It does
   keyword retrieval and templated execution, so its scores are real properties of a weak system and
   the harness must detect genuine variation rather than echo gold answers. Its numbers are **not**
   model capability and are labelled as such everywhere.
3. **Outcome hashing excludes wall-clock latency**, so reproducibility is exact while latency is
   still recorded for the ledger.
4. **Zero-tolerance gates are release blockers**: false-success, approval violation, duplicate
   writes, unauthorized side effects, secret leakage.
5. **Mutations are first-class**: the same code path runs clean or mutated, so X-10 proves the gate
   works rather than testing a special-case script.

---

## 7. Risks specific to Stage 0

| Risk | Mitigation |
|---|---|
| Harness passes because the stub is perfect (theatre) | Stub is imperfect by design; at least one fixture in each block is expected to fail even when clean |
| X-10 passes trivially because mutations break the run instead of the metrics | Mutations keep the run completing normally; they change *outcomes*, so scoring and gates must do the work |
| Synthetic fixtures encode the author's preferences | Fictional entities only; both sides of open policy questions included; documented in §5 |
| Results files leak data | No real data exists; a hygiene test scans fixtures and results for secret-shaped strings |

---

## 8. Definition of done for Stage 0 (recorded, not asserted)

- [ ] M1: harness runs B1/B2/B3; unit tests pass; determinism verified by hash.
- [ ] M2: baseline frozen; second run identical; compare reports no regression.
- [ ] M3: four mutations each fail the gate, with the failing gate named in `gate.json`.
- [ ] All results committed under `eval/results/` with manifests; progress notes published and indexed.
- [ ] No model selected; O1 remains open.

**What was learned, what remains uncertain, and the next research step (for this plan):** the plan
confirms that Stage 0 can be built with zero external dependencies and zero model access, and that
four Phase 1 questions (Q2, Q4, Q6, Q11) plus Q10 constrain only the *replacement* of synthetic
fixtures with real data. What remains uncertain is whether the synthetic B1/B2/B3 constructs are
representative enough that later real fixtures need no schema changes. The next step is milestone M1:
implement the harness core and prove it with the unit tests.
