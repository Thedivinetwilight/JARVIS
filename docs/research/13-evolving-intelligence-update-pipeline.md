# Phase 13 — Evolving Intelligence: What It Means, How Updates Are Traced and Reversed

- **Status:** published (v1.0, revision R1)
- **Published:** 2026-11-01 · **Revision:** R1
- **Extends:** [Phase 8 §9–§10](08-evaluation-and-improvement.md) (release process and the normative
  definition of self-improvement, retained as S-5)
- **Depends on:** [Phase 11](11-brain-inspired-cognitive-architecture.md) (memory taxonomy),
  [Phase 12](12-capability-integration-architecture.md) (registry, skills, layers)
- **Evidence:** [Phase 14](14-evidence-review-automatic-updates.md) reviews the research that supports
  or fails to support each update class
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · `[I]` independent · `[V]` vendor

---

## 0. "Evolving intelligence", defined concretely

A system that "evolves its intelligence" is, in engineering terms, a system where **artefacts that
determine behaviour are updated on evidence, through a controlled process, and can be reverted.** That is
the whole definition. It is deliberately unglamorous, because the alternative — a system that rewrites
itself and is judged by its own opinion — is not evolution, it is an unmeasured variable.

Five artefact classes can change. Each has a different mechanism, risk and approval requirement, and
conflating them is the most common conceptual error in "self-improving AI" discussions:

| ID | Update class | What changes | Time constant | Blast radius | Reversible? |
|---|---|---|---|---|---|
| **U1** | **Memory update** | facts, episodes, consolidations | minutes–hours | one user, one topic | yes (supersede, not delete) |
| **U2** | **Skill / workflow update** | plan templates, tool contracts, prompts for a family, code | days–weeks | one task family | yes (versioned artifact) |
| **U3** | **Policy / behaviour update** | routing thresholds, effort levels, style, escalation sensitivity | days | all families | yes (config) but affects safety surface |
| **U4** | **Model adoption** | which model/tier serves which family | weeks–months | everything | yes (pin + pointer swap) |
| **U5** | **Model adaptation** | weights (fine-tune, LoRA, distillation) | months | everything, invisibly | partially (keep parent weights) |

**What follows from this table:** most of the value and nearly all of the safety engineering lies in
U1–U3, which are ordinary software updates with an unusually strict evaluation harness. U4 is
procurement plus gates. U5 is the only class where "the model itself changed", and it is the class with
the weakest evidence, the highest cost and the least reversibility — so it is the most constrained.

**What "evolving intelligence" explicitly does not include:** the system editing its own gates, its
permissions, its evaluator, or this document; unbounded retries; or changes that cannot be rolled back.
Phase 8 §10's exclusions are retained verbatim (S-5).

## 1. The loop, per class

```
        ┌──────────────────────────── observe ────────────────────────────┐
        │  failures, near-misses, user corrections, cost anomalies,       │
        │  verifier vetoes, drift between expected and observed state     │
        └───────────────────────────────┬─────────────────────────────────┘
                                        ▼
                              attribute to a class
                     (U1 memory · U2 skill · U3 policy · U4 model · U5 weights)
                                        ▼
                          propose: hypothesis + expected delta
                          + falsifier + cost + blast radius
                                        ▼
                    offline evaluation vs frozen per-family baseline
                     (Stage 0 harness: identity, ledger, gates, mutations)
                          │ pass                          │ fail
                          ▼                               ▼
                   shadow mode (no side effects)      record rejection
                          ▼                          with evidence (anti-oscillation)
                   canary (N tasks / 10% traffic)
                          ▼
                adopt + changelog + monitor window
                          ▼
             regression? ── yes ──► rollback (one command, rehearsed)
```

Differences from Phase 8 §9 (which stands for U2–U5): **U1 gets a lighter path** (write-gate + audit,
no canary, because a single fact is reversible in seconds), and **U5 gets a heavier path** (sandbox,
parent-weights retention, offline replay, explicit human sign-off, and a re-run of the entire suite plus
mutations).

### 1.1 Per-class gates

| Class | Gate before adoption | Monitoring after | Rollback trigger |
|---|---|---|---|
| U1 memory | write gate: source span, trust class, no contradiction with higher-trust fact; audit entry | contradiction rate, user correction rate | any contradictory fact from a lower-trust source (auto-supersede) |
| U2 skill | skill tests + family gates (`eval/harness/gates.py`) + X-10 mutations for the family | skill test pass rate, reuse rate, cost per success | failing skill test at nightly run (auto-demote) |
| U3 policy | family gates on **all** families, not just the target; safety invariants zero-tolerance | approval violations, egress count, cost per success | any zero-tolerance event |
| U4 model | full suite + all mutations + a base-model-swap drill; cost envelope re-measured | drift in false-success, latency, cost | >0.0 pp success drop on any family, or budget breach |
| U5 weights | everything in U4 **plus**: offline replay on held-out data, contamination check, parent model retained, human sign-off, documented training-data provenance | monthly replay; behavioural diff report | inability to reproduce a passing replay, or any safety-suite regression |

## 2. Traceability: every artefact says what it is and where it came from

The requirement is not "log everything"; it is **"given a behaviour change, name the artefact, its
parents, its evidence and its approver."** Stage 0 already implements the pattern for evaluation runs
(`eval/harness/config.py` computes a run identity from git revision + config hash + task-set hash + seed;
`eval/harness/ledger.py` writes append-only JSONL with an index). This phase generalises it to production:

| Artefact | Identity | Parents | Evidence | Approver |
|---|---|---|---|---|
| Memory fact | `fact_id`, bitemporal validity | source episode + span | verifier result | system (write gate) |
| Skill | `skill_id@version` (git sha) | template + failed attempts it fixes | skill tests + family run ids | human owner |
| Policy/config | config hash | previous config hash | gate report path | human owner |
| Model serving config | `model_id@provider_version` + decode params | previous pin | full-suite run ids | human owner |
| Adapter weights | weights hash + training manifest | base model hash + dataset ids | replay report | human owner + review |

**Decision records.** Every adopted change writes a short record — the analogue of this research
programme's own phase documents — containing: the failure class it addresses, the hypothesis, the expected
metric movement, the falsifier, the measured result, the cost, and the rollback command. Rejected
proposals are recorded too, with their evidence, so the same idea is not re-proposed monthly (Phase 8
§10's anti-oscillation rule, retained).

**Why this matters more than the update mechanism:** without artifact identity, a regression cannot be
attributed, and an unattributed regression forces a system-wide rollback. Stage 0's `outcome_hash`
demonstrates the cheap version: the same task on the same configuration hashes identically across
processes, so "did this change anything?" is a set comparison, not an argument.

## 3. Reversibility, concretely

Reversibility is a property of the *storage and deployment* design, not a promise.

### 3.1 Immutable artifacts, pointer swap

Every artefact class is append-only and content-addressed: skills are commits, configs are hashes, model
pins are manifest entries, memory facts are bitemporal rows. Deployment is a pointer:

```
serving = { family: "B2", skill_set: "s_19", policy: "cfg_7f3", model: "m_x@p1", memory_schema: "v4" }
```

Rollback = write the previous pointer. Requirements (Phase 8 §9 retained, now per class): the previous
pointer must be deployable in one command, tested within 30 days, and a rollback **drill** must have been
run for the class before the first production adoption.

### 3.2 Memory: supersede, never silently overwrite

A fact is never edited in place: a new row supersedes the old one with a reason and a source. "Forgetting"
is a validity end, not a deletion (except on explicit user request, which must be a real deletion —
Phase 5 §8 privacy rules stand). Consequence: the memory state as of any timestamp is reconstructible,
which is what makes a memory-caused regression diagnosable.

### 3.3 Skills: quarantine and automatic demotion

A skill that fails its nightly tests is **demoted to quarantine** immediately (still inspectable, no
longer selectable by the planner), and its last known-good version is restored. This is a scheduler-level
decision, not a model decision — the component that wants to keep using the skill does not get a vote.

### 3.4 Models: pin, and keep one previous pin warm

Provider versions change without notice (Phase 2's volatility warning; Phase 10 R4). The serving manifest
pins an exact model version and decode parameters. Rollback to the previous pin must be possible within
one operator command and must have been rehearsed. A **base-model-swap drill** — run the full suite with
the previous pin while the new one serves — is required for U4.

### 3.5 What is deliberately *not* reversible, and must therefore be gated hardest

- External side effects (mail sent, event created, money moved). Idempotency keys make retries safe; they
  cannot unsend. Phase 9 approvals and Phase 8 zero-tolerance gates are the only defence, which is why
  they are excluded from automated change.
- Deleting user data. Explicit confirmation only.
- Publishing anything to a third party. Human approval, always.

## 4. Feedback: where the learning signal actually comes from

| Signal | Quality | Bias to control | Use |
|---|---|---|---|
| Explicit user correction ("no, the 6th") | high | sparse; users correct only salient errors | label + fixture candidate |
| Implicit acceptance (task not re-asked) | low | noisy; absence of complaint ≠ success | weak prior only |
| Verifier veto (read-back mismatch) | high | only as good as the assertion | immediate repair + fixture candidate |
| Approval refusal | high | policy preference, not correctness | policy tuning, not capability |
| Cost/latency anomaly | objective | — | U3/U4 routing |
| Model self-report ("I am confident") | **poor** | systematically overconfident | never a success signal (D1 retained) |

**Feedback must be curated, not hoovered.** Every accepted fixture from production feedback is reviewed
for personal data before it enters a task set; Stage 0's loader refuses non-synthetic data by design, and
that rule stays until the user consents and a de-identification pipeline exists (Q10 remains blocking).

## 5. Autonomy ladder

| Level | System may | Human must | Applies to |
|---|---|---|---|
| **A0** | nothing; all changes human-authored | author, review, deploy | U5 initially |
| **A1** | propose changes with evidence | approve, deploy | U2, U3, U5 |
| **A2** | apply **bounded, reversible** changes that pass gates automatically | review within a window; may veto | U1, and U2 only for prompt/plan-template variants inside an approved envelope |
| **A3** | deploy reversible changes to canary without review | review before full rollout | not granted in the near term |
| **A4** | full autonomous deployment including weights | — | **not granted**, and not on any roadmap stage |

The ladder is per class and per artefact, and it can be walked back. Anything touching permissions,
approval policy, gates, the evaluator, personal data retention, or irreversible side effects is pinned at
**A0** permanently, whatever the rest of the system achieves.

## 6. Automatic updates are not solved — and what that constrains

This document specifies a pipeline, not a capability. Three facts about the evidence (reviewed in Phase 14)
shape it:

1. **Published systems that improve themselves do so inside a narrow, machine-checkable loop.** STOP
   recursively improves a code-generation scaffold and finds real gains, but also uses an unbounded
   sandbox to do it `[I]` (2310.02304). DGM evolves its own codebase and roughly doubles SWE-bench
   performance over iterations, at large search cost, within a benchmark sandbox `[I]` (2505.22954).
   AlphaEvolve improves kernels and algorithms inside an automated evaluator `[V]` (DeepMind, May 2025).
   In every case the *evaluator* is outside the system and trusted; that is the design we copy.
2. **Self-managed weight updates remain research.** SEAL trains models to generate their own fine-tuning
   directives and reports gains on knowledge incorporation, with heavy per-task compute and evaluation
   inside the training loop `[I]` (2506.10943). It is a demonstration on narrow tasks, not a service.
3. **The failure modes are the familiar ones, amplified.** Training on self-generated data degrades
   models (model collapse, 2305.17493) `[I]`; optimisation pressure on a fixed metric produces
   specification gaming `[P]`; and models can strategically underperform on evaluations (sandbagging,
   2406.07358) `[I]`, which means "it passed the eval" is weaker evidence than it looks.

Therefore: **U1/U2/U3 have defensible automation paths today; U4 is procurement with gates; U5 stays at
A0–A1 and behind a human sign-off for the foreseeable future.** No claim in this programme should
describe J.A.R.V.I.S. as "self-improving" without that qualification.

## 7. Failure modes of the loop

| Failure | Mechanism | Control |
|---|---|---|
| **Oscillation** | two changes alternately pass and fail the same test | rejected-proposal log; require a change to beat the median of the last 3, not the last 1 |
| **Goodharting** | optimisation against a fixed suite | held-out family, mutation testing of the suite (X-10 generalised), rotating red-team cases |
| **Silent drift** | model/provider changes underneath a pinned abstraction | version pinning + weekly replay of the baseline with the pinned model; drift alarm |
| **Memory rot** | stale facts outvote fresh ones | bitemporal validity, trust ordering, consolidation with expiry |
| **Skill rot** | dependency or data schema changed | nightly skill tests, quarantine on failure |
| **Cost spiral** | each improvement adds retries/agents | hard budget in L3; cost per success is a gate, not a report |
| **Feedback poisoning** | adversarial user/injected content becomes training or fixtures | write gate, provenance, review before fixture promotion |
| **Self-graded success** | model judges its own output | verifier is code; judge models only as a secondary signal with calibration |

## 8. What can be built now, with the harness we already have

Ordered by evidence value per unit of effort:

1. **Per-family baselines and a registry** (Stage 1 in Phase 15): no new infrastructure, extends the
   existing task-set/ledger/gate pattern from three blocks to families.
2. **U2 skill promotion as a gated artifact**: skills are code + tests; promotion = a run of the family
   suite; demotion = nightly test failure. Stage 0's gate API is already the right shape.
3. **U1 memory write gate with provenance**: the tools/audit pattern in `eval/harness/tools.py` (validate →
   authorise → apply → record) transfers directly to memory writes.
4. **U3 policy changes behind full-suite gates**: currently trivial because the stub solver is deterministic;
   becomes meaningful when a model is wired in.
5. **U4 model adoption drill**: needs one real model call — blocked on O1 and the cost envelope.

## 9. Findings classification

| Finding | Label | Basis |
|---|---|---|
| Most of "evolving intelligence" is U1–U3, i.e. versioned software with strict gates | **Established** by construction | this document's decomposition |
| Per-class gates and rollback drills are implementable with the Stage 0 pattern | **Established** in code for evaluation runs; transfer untested for production | `eval/harness/{config,ledger,gates}.py` |
| Automatic, gate-passing updates to memory and bounded skills are safe enough to allow at A2 | **Unproven** | no production data; no adversarial testing yet |
| Autonomous weight updates are safe or necessary now | **Rejected** | SEAL is narrow research `[I]`; model collapse `[I]`; no live evidence |
| Model self-report can be trusted as a success signal | **Rejected** | overconfidence is well documented; D1 retained |
| Traceability is a prerequisite for autonomy | **Plausible** | unattributable regressions force system-wide rollback (design argument) |

---

**What was learned, what remains uncertain, and the next research step.** *Learned:* "evolving
intelligence" resolves into five artefact classes with wildly different risk profiles, and the engineering
that matters is traceability plus reversibility — the same discipline Stage 0 already applies to
evaluation runs, generalised to production artefacts. *Uncertain:* whether automatic gate-passing updates
(U1–U2) survive adversarial feedback and drift without human review, and how much of the improvement
users actually notice. *Next research step:* establish what the published evidence really supports, and
where human approval must remain — [Phase 14](14-evidence-review-automatic-updates.md) — because the
pipeline above is only as good as the evidence for its gates.
