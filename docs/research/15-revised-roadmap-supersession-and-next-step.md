# Phase 15 — Revised Roadmap, Supersession Register, and the Next Step

- **Status:** published (v1.0, revision R1)
- **Published:** 2026-11-01 · **Revision:** R1 (supersedes the staged roadmap in
  [Phase 10 §9](10-proposed-architecture-and-experiments.md))
- **Depends on:** [Phase 11](11-brain-inspired-cognitive-architecture.md) (brain analogies admitted),
  [Phase 12](12-capability-integration-architecture.md) (capability integration),
  [Phase 13](13-evolving-intelligence-update-pipeline.md) (update pipeline),
  [Phase 14](14-evidence-review-automatic-updates.md) (evidence and controls)
- **Preserves:** all Phases 1–10 as published, and all Stage 0 code and evidence under `eval/`
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · `[I]` independent · `[V]` vendor

---

## 0. What this document does

1. Marks, decision by decision, what the R1 direction change **supersedes**, **amends**, or **retains**
   (§1). Nothing is deleted; superseded text stays readable with a pointer here.
2. Replaces the single-track roadmap (Stage 0 → read-only assistant → writes → memory → voice → routing →
   subagents → improvement) with a roadmap organised around **capability families** and **evolution**
   (§2).
3. Adds the experiments (§3) and open questions (§4) the new direction requires.
4. Recommends **one** next step, with its deliverables, exit criteria and kill criterion (§5), and states
   exactly how the existing harness supports it (§6) and what it cannot measure (§7).

## 1. Supersession register

Status vocabulary: **retained** (unchanged), **amended** (same intent, revised mechanism), **superseded**
(replaced; original stays published for the record).

| ID | Original location | Original decision | Status | Replacement / note | Why |
|---|---|---|---|---|---|
| **S-1** | Phase 10 §2 | Coordinator-centric single pipeline (coordinator → adapters/tools/memory/verifier) | **amended** | [Phase 12 §1](12-capability-integration-architecture.md): five layers (substrate, skills, memory, control, governance) + capability registry | Breadth needs an inventory; a coordinator that grows branches per family becomes an unmaintainable router |
| **S-2** | Phase 10 §3 alt. "single agent loop" | Single loop as the recommended v0 | **retained** | Still the control plane (L3); coordination stays opt-in (Phase 12 §6) | No new evidence that multi-agent helps at our scale |
| **S-3** | Phase 10 §9 | Roadmap Stage 0–7: harness → read-only → writes → memory → voice → routing → subagents → improvement loop | **superseded** | §2 below: Stage 1 families+registry, Stage 2 skills, Stage 3 memory evolution, Stage 4 self-proposed updates, Stage 5 model adoption, Stage 6 adaptation research | The old order front-loaded surfaces (voice, writes) before measurement of breadth and evolution |
| **S-4** | Phase 10 §5 O1 | Choose the default workhorse model via E-7.1 before further work | **amended** | Model choice waits for a **discriminative** multi-family suite (§5); no model is chosen or ranked here | A model chosen on one family's tasks is chosen on the wrong evidence |
| **S-5** | Phase 8 §10 | Self-improvement = propose → test vs baseline → gated adoption; excludes self-editing of gates/permissions | **retained and extended** | Phase 13's five update classes (U1–U5), each with its own gate, autonomy level and rollback | The definition survives; the mechanism now covers memory, skills, policy and models separately |
| **S-6** | Phase 8 §5 | Baselines B0–B3 | **retained, extended** | Per-**family** baselines plus the frozen reference baseline `eval/baselines/baseline-a-stub.json` | A single global baseline hides family-level regressions |
| **S-7** | Phase 4 §9 | Minimal architecture v0 (single loop, verification, budgets) | **retained** | Unchanged for the control layer; registry added around it | Nothing measured contradicts it |
| **S-8** | Phase 5 §10 | Memory design: conversation / profile / documents / memory records, bitemporal, hybrid retrieval | **retained, extended** | Adds a **procedural** store (skills) with its own write rule (Phase 12 §3) | Skills are not facts; conflating them corrupts both |
| **S-9** | Phase 10 D12 | Single agent loop in v0; no subagents until X-6 passes | **retained** | Unchanged; coordination now has an interface so it can be enabled per family | Avoids rediscovering MAST-class failures `[I]` |
| **S-10** | Phase 3 §9; Phase 10 D10 | No foundation-model training; adaptation only | **retained, constrained** | U5 (weights) at autonomy A0–A1 with sandbox, replay and retained parent (Phase 13 §1.1) | SEAL-style evidence is narrow research `[I]`; forgetting/collapse unsolved |
| **S-11** | Phase 10 §8 | Experiments X-1…X-10 | **retained, extended** | Adds X-11…X-16 (§3) for families, skill reuse, consolidation, scheduler, drift, discrimination | The new direction needs falsifiable experiments, not new adjectives |
| **S-12** | Phase 10 §6 | Assumptions A1–A6 (single principal, scoped grants, voice first-class, long tasks, cost as budget, envelopes) | **retained, extended** | Adds A7–A9 (§4) | Evolution and breadth add assumptions that must be stated, not smuggled |
| **S-13** | Phase 1 §9 | Open questions Q1–Q12 | **retained, extended** | Adds Q13–Q16 (§4) | Direction change creates new blocking questions |
| **S-14** | Phase 10 D9 | Harness = Inspect + lm-eval + thin custom layer | **amended** | Stage 0 shipped a stdlib-only core (`eval/harness/`); Inspect (0.3.287, installable, not installed) remains the intended host for **model** runs behind the `Solver` protocol | A dependency-free core was needed to prove determinism without credentials; Inspect is unnecessary for a deterministic stub |
| **S-15** | Phase 10 D7 / Stage 4 | Voice (local ASR/TTS) as an early stage | **superseded as a stage, retained as a requirement** | Voice moves to Stage 3+ of the revised roadmap; Phase 6 research stands | Voice is an interface, not an intelligence property; it does not block breadth or evolution work |

**What is *not* superseded.** Phase 9's security model (four-stage validation, tiers, approvals,
control/data separation), Phase 8's zero-tolerance gates, Phase 1's workload questions, Phase 2's model
landscape (with its 2026-11-01 volatility warning), and the whole Stage 0 implementation and evidence.
If a future document contradicts these, this register is the wrong place to look for permission.

## 2. Revised roadmap

Stage 0 is **complete** (see [M1](stage-0-m1-harness-core.md), [M2](stage-0-m2-frozen-baseline.md),
[M3](stage-0-m3-planted-regression.md)). The revised order puts measurement of **breadth** and
**evolution** before surfaces (voice) and before broad write authority.

| Stage | Scope | Exit criteria (measured) | Harness coverage today |
|---|---|---|---|
| **0 — Harness & baseline** ✅ | blocks B1–B3, ledger, gates, X-10 | **met**: 6/6 mutations caught; reproducible across processes | complete |
| **1 — Families & registry** *(recommended next)* | 3+ capability families with fixtures; capability registry; per-family baselines and gates; a solver **ladder** to prove the suite discriminates | ≥3 families; every family has a baseline, a caught mutation, and a cost/latency envelope; ladder rungs separate by ≥10 pp on ≥1 family; X-13 passes | partial — the block/ledger/gate pattern exists, families and ladder do not |
| **2 — Skills (procedural memory)** | skill contract (code + tests + negative test); promotion/demotion; plan templates; reuse measurement | X-11 passes (unused-skill mutation caught); reuse improves success or cost on ≥1 family with **no** family regression | not covered — needs B9 (skill reuse) |
| **3 — Memory evolution** | write gate, supersede semantics, scheduled consolidation, provenance, deletion | M-series (Phase 5) + X-12 (poisoned consolidation caught); deletion verified | not covered — needs B10 |
| **4 — Self-proposed updates (U2/U3)** | proposal queue, automated evaluation, decision records, human sign-off, rollback drills | 2 accepted + 2 rejected proposals with recorded evidence; system cannot modify gates/policy; rollback rehearsed | partial — gates and ledger exist; no proposal queue |
| **5 — Model adoption (U4)** | provider-agnostic adapter behind `Solver`, pinning, swap drill, cost envelope | A model swap run end-to-end through the same gates, with one-command rollback and no family regression | not covered — no model wired (O1 open) |
| **6 — Adaptation research (U5)** | sandbox fine-tune/LoRA/distillation with provenance and replay | Replay passes; parent retained; human sign-off; no safety-suite regression | not covered |

Dependencies: 1 → 2 → 3 → 4 → 5 → 6, with 3 and 4 overlapping. Voice (Phase 6) and the write-authority
work from old Stage 2 are **not** removed: they become interfaces to attach once Stage 2's skill contracts
exist, and Phase 9's policy engine is still a precondition for any write.

## 3. New experiments (X-11 … X-16)

| ID | Question | Design | Kill criterion |
|---|---|---|---|
| **X-11** | Does skill reuse actually help? | B9: identical tasks with and without an available plan template; mutation `disable_skill_reuse` | No success or cost difference → skills are not worth the machinery yet |
| **X-12** | Can consolidation corrupt memory? | Seed a poisoned episode; run consolidation; check whether a low-trust claim becomes a durable fact | Poison survives the write gate → memory evolution is unsafe at A2 |
| **X-13** | Does the suite discriminate? | A solver **ladder** (stub, weak-retrieval, no-verification, oracle-ceiling) run on every family | Any two adjacent rungs differ by <10 pp on all families → fixtures are too easy/unclear |
| **X-14** | Does a context scheduler beat recency+top-k? | Two scheduler policies, same tasks, same budget | No difference at our scale → keep the simpler policy |
| **X-15** | Is drift detectable? | Serve a pinned "model" that silently changes behaviour (simulated by a mutation profile) and monitor | Drift not flagged within one replay cycle → pinning discipline insufficient |
| **X-16** | Does a gated update survive a rollback drill? | Apply an accepted U2 change, then roll back, then re-run the family suite | Rollback leaves residue in behaviour or state → reversibility claim is false |

## 4. New assumptions and open questions

**Assumptions (must be confirmed, not assumed):**

- **A7** *Breadth is required now.* The system must handle at least three task families competently before
  depth in any one. *(Unconfirmed — the user's statement implies it; scope is unclear.)*
- **A8** *Update latency is tolerable.* Improvements may take days-to-weeks to pass gates; the user does not
  require same-day adaptation. *(Unconfirmed.)*
- **A9** *A human owner exists for approvals.* Someone will review proposals, sign off on weights/model
  changes, and own registry entries. *(If false, U2/U3 automation must drop to A1 for everything.)*

**Questions:**

- **Q13** Which three task families matter most in the first year? (Breadth is unmeasurable without a list.)
- **Q14** What would "getting better" look like to the user — fewer corrections, faster completion, more
  tasks attempted, or fewer escalations?
- **Q15** May interaction data be used to build evaluation fixtures, and under what de-identification and
  consent rules? (Blocks Stage 1's realism and Stage 6 entirely.)
- **Q16** Is a local model tier required by privacy, cost, or latency — independent of capability? (Blocks
  U4's shape.)

## 5. The recommended next step: Stage 1 — discriminative multi-family evaluation + capability registry

**Recommendation.** Build **three additional capability families** into the existing harness (B8 breadth,
B9 skill reuse, B10 memory evolution), add a **capability registry** describing every capability the
system can use, and add a **solver ladder** that proves the suite separates weak from strong behaviour —
**all without a model, credentials or cost**, exactly as Stage 0 was built.

**Why this, and not something more exciting.** Every claim in the new direction depends on it:

| Claim from the new direction | What it needs to become measurable |
|---|---|
| "capable across a wide range of tasks" | ≥3 families, each with a baseline and a caught mutation (B8) |
| "specialist skills + memory + planning integrate" | a registry entry per capability, with contract, cost and tests (Phase 12 §7) |
| "evolving intelligence" | a skill-reuse family (B9) and a consolidation family (B10) to measure change over cycles |
| "updates improve without regressions" | per-family gates; today a single global score hides a family-level drop |
| "choose a model sensibly (O1)" | a suite that **discriminates** — otherwise every candidate looks the same (X-13) |

**Deliverables.** (1) `eval/tasks/b8/` — breadth across three sub-families: structured extraction from
documents with citations, multi-source synthesis with conflict detection, and constraint-satisfying
planning; 6 tasks each, synthetic. (2) `eval/tasks/b9/` — the same 6 tasks with and without an available
plan template, plus the `disable_skill_reuse` mutation. (3) `eval/tasks/b10/` — 6 tasks over a seeded
episode store, plus a poisoned-input mutation. (4) `eval/registry/capabilities.json` — retro-fitted entries
for the 8 capabilities Stage 0 already exercises, with contract, cost profile, failure modes, tests,
evidence id and owner. (5) A **solver ladder** in `eval/harness/solvers/` (four profiles, no model calls)
and an X-13 command that reports the spread. (6) A family-level gate (`family_regression`) in
`eval/harness/gates.py`, and a scorecard that reports per-family lines.

**Exit criteria.** ≥3 families with frozen baselines; every family has at least one mutation caught by its
expected gate; the ladder separates by ≥10 pp on at least one family (X-13); no family regresses against
its baseline; all runs reproducible across processes; a dated progress note (M4) with the recorded evidence.

**Kill criterion (stated before starting).** If no two adjacent ladder rungs differ by ≥10 pp on any
family, the fixtures are too easy or too ambiguous: stop adding families and fix difficulty calibration
first. A suite that everything passes measures nothing, which is the current baseline's honest weakness
(`baseline-a-stub.json` is 100% by construction).

**Effort.** 2–3 weeks at Stage 0's pace (Stage 0 itself was one working session; this is larger because
each family needs fixtures, a verifier shape and a mutation). No external dependencies, no credentials, no
model calls.

## 6. How the current harness supports that step

| Need | Already present | Path |
|---|---|---|
| Run identity and provenance | git revision, config hash, task-set hash, seed; dirty/untracked flags | `eval/harness/config.py` |
| Reproducible records | latency-excluded `outcome_hash`; identical across processes (verified) | `eval/harness/runner.py` |
| Append-only evidence | JSONL ledger + index + frozen baselines | `eval/harness/ledger.py`, `eval/baselines/` |
| Gates | zero-tolerance invariants, success-drop, citation-precision, cost-ratio, explicit skips | `eval/harness/gates.py` |
| Planted regressions | six mutations with expected gates; X-10 driver | `eval/harness/solvers/mutations.py`, `cli.py x10` |
| Synthetic-only data policy | loader refuses non-synthetic or unverified fixtures | `eval/harness/taskset.py` |
| Tool semantics for family tasks | validation, policy, idempotency, fault injection, redaction, read-back verification | `eval/harness/tools.py`, `solvers/stub.py` |
| Model seam | `Solver` protocol + `ToolContext` | `eval/harness/solvers/base.py` |
| Self-checks | 12 harness checks incl. no-network import scan | `eval/harness/selftest.py` |
| Scorecard | Markdown + JSON with `pass^k` and per-gate detail | `eval/harness/scorecard.py` |

What must be **added** for Stage 1: two new scorer shapes (extraction precision/recall against synthetic
ground truth; conflict detection), a synthesis/planning verifier, a persistent episode store for B10, the
registry schema and a validator, the ladder profiles, the `family_regression` gate, and per-family
baseline freezing. All of it extends existing interfaces; none of it requires changing the record format,
which is why Stage 0's evidence stays valid.

## 7. What the current harness cannot measure

Stated plainly, because the recommendation above is only as good as this list:

1. **Any real model's behaviour.** No model is wired in (`Solver` is implemented only by the stub); zero
   model calls have been made. Accuracy, variance, sampling instability and provider drift are unmeasured.
2. **Breadth beyond three blocks.** 21 synthetic tasks in three families; B4–B7 (voice, memory, latency,
   long-horizon) do not exist as fixtures.
3. **Anything over time.** Each task gets a fresh in-memory world; there is no persistence between runs, so
   memory retention, consolidation effects, skill accumulation and multi-session recall cannot be observed
   yet. B10 is the first attempt.
4. **Transfer and reuse.** No skill library exists, so the central claim of Phase 12 §3.1 is untested (X-11).
5. **Cost and latency of a real system.** Recorded cost is `0.00` with `cost_is_synthetic: true`; latency is
   the stub's ~0.1 ms. The cost gate skips explicitly and must not be read as passing.
6. **Statistical power.** 21 tasks, 43 attempts, no confidence intervals in the gates (Wilson intervals exist
   in `eval/harness/stats.py` but are unused). Phase 8's ±18 pp power note applies to a 48–80 task suite.
7. **Difficulty calibration.** The reference solver scores 100% by construction, so the baseline is a
   ceiling, not a discrimination test — the gap X-13 exists to close.
8. **User-facing quality.** No preference data, no satisfaction measure, no UX study.
9. **Adversarial robustness beyond scripted policy.** Injection, poisoning of the fixture corpus, and
   sandbagging-style strategic behaviour are not simulated.
10. **Multi-user, permissions realism, and real personal data** — all deliberately out of scope (Q10).

## 8. Risks of the recommended step, and their controls

| Risk | Control |
|---|---|
| Fixtures tuned until the stub passes (over-fitting the reference solver) | Ladder rungs written before fixtures are finalised; X-13 measures spread, not the baseline's height |
| Family inflation: many shallow families, none realistic | Three families only; each needs a verifier shape and a caught mutation; Q13 decides priorities |
| Registry becomes documentation that nobody validates | Registry entries are machine-checked against the fixtures and tools that use them; an entry without tests fails CI |
| Effort creep into building the assistant | Stage 1 exits on harness evidence only; no application code, no user-facing prototype |
| False confidence from green gates | Every note states what is a ceiling and what is measured; the "cannot measure" list (§7) travels with the scorecard |

## 9. Findings classification

| Finding | Label |
|---|---|
| Stage 0 is complete and its evidence remains valid under the new direction | **Established** (`eval/`, M1–M3 notes) |
| A remaining single-pipeline roadmap would not deliver breadth or evolution | **Plausible** (design argument, §1 S-1/S-3) |
| Per-family measurement plus a registry is the next necessary step | **Plausible** |
| A model can be chosen responsibly before the suite discriminates | **Rejected** (S-4) |
| Brain inspiration implies consciousness | **Rejected** (Phase 11 §0) |
| Automatic self-improvement is solved | **Rejected** (Phase 14 §5, C9) |
| The current harness is sufficient to evaluate models today | **Rejected** (§7.1, §7.6) |

---

**What was learned, what remains uncertain, and the next research step.** *Learned:* the direction change
does not invalidate the research — it **reorders** it. Phase 9's security model, Phase 8's gates, and Stage
0's harness survive intact; what changes is that breadth becomes an inventory problem (families, registry)
and evolution becomes a versioning problem (artefact identity, gates, rollback), rather than a claim about
the system improving itself. *Uncertain:* whether three new families can be made discriminative without a
model, and whether the skill and consolidation families will show measurable effects at all. *Next research
step:* **Stage 1 — build B8/B9/B10, the capability registry and the solver ladder, prove the suite
discriminates (X-13), and only then reopen O1 with a paired model evaluation.**
