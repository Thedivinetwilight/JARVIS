# Phase 11 — Brain-Inspired Cognitive Architecture: What Transfers, What Does Not

- **Status:** published (v1.0, revision R1 of the research programme)
- **Published:** 2026-11-01 · **Revision:** R1 (direction change: brain-inspired, broad-capability, evolving)
- **Sources checked:** 2026-11-01 (see §9 for which were fetched this session and which are carried over)
- **Related:** revises the framing of [Phase 3 §2–§4](03-model-architectures-and-training.md),
  [Phase 4](04-reasoning-agents-and-orchestration.md) and [Phase 5](05-memory-and-knowledge.md);
  feeds [Phase 12](12-capability-integration-architecture.md); supersession register in [Phase 15](15-revised-roadmap-supersession-and-next-step.md)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · **Speculative (not adopted)** · `[V]` vendor · `[I]` independent · `[U]` unverified

---

## 0. Purpose, and four things this document does not claim

The project direction has changed: J.A.R.V.I.S. is intended to be **brain-inspired**, **broadly capable**,
and **able to evolve its intelligence**. This document answers one question — *which human cognitive
functions can usefully inspire software components, and which brain analogies remain speculative* — and
nothing else. It is deliberately written against four temptations:

1. **It does not claim that brain inspiration implies consciousness, sentience, or human-like experience.**
   Nothing here is evidence for or against machine consciousness. The word "cognitive" below means
   "functional decomposition borrowed from cognitive science", not "inner life". **[Established]** as a
   statement about the scope of this document; any inference from function to experience is
   **[Speculative (not adopted)]** and will not be used to justify design, marketing, or autonomy.
2. **It does not claim the brain is the best engineering blueprint.** Biological brains are the product of
   selection under constraints (metabolic cost, development time, survival) that are not ours. Where a
   conventional engineering pattern is better, the conventional pattern wins and the analogy is dropped.
3. **It does not claim neuron-level mimicry.** We are not building spiking networks, cortical columns, or
   connectome-scale models. The borrowable material is *functional organisation and control principles*,
   at the granularity of modules, interfaces, budgets and update rules.
4. **It does not claim that self-directed learning in brains transfers to software.** Continual learning
   without catastrophic forgetting is an open research problem in machine learning (§7.4); a brain does
   it, and that fact gives us a target, not a method.

## 1. Method: a three-filter test for every analogy

An analogy is admitted into the architecture only if it passes three filters, in order:

| Filter | Question | Failure mode it prevents |
|---|---|---|
| **F1 — Functional necessity** | Does the system fail a *measured* task without this component? | Adding brain-shaped modules because they sound profound |
| **F2 — Implementable mechanism** | Can the function be expressed as an interface, a budget, a data structure or a control rule — not as a metaphor? | "Attention" that is a slide, not a scheduler |
| **F3 — Falsifiable benefit** | Is there an experiment that would show the component earns its cost, and a result that would remove it? | Permanently unfalsifiable architecture |

Analogies that pass all three are **adopted as design constraints**. Those that pass F1–F2 but not yet
F3 are **heuristics under measurement**. Those that fail F1 or F2 are **speculative and not adopted** —
recorded, so that a later reader can see they were considered, not overlooked.

## 2. The mapping table

Fourteen functions were examined. "Software component" is what we would actually build; "tier" is the
disposition under §1.

| # | Cognitive function | What the brain does (compressed) | Software component | Evidence | Tier |
|---|---|---|---|---|---|
| 1 | **Selective attention** | Prioritises a subset of available input under a capacity limit | Explicit **context scheduler**: budget in tokens, priority scores, eviction policy, per-step re-allocation | Attention bottleneck and "lost in the middle" degradation are measured in LLMs `[I]` (2307.03172); brain-side capacity limits are textbook `[P]` | **Adopted** (constraint on how context is filled) |
| 2 | **Working memory** | Holds and manipulates a small amount of information for seconds | Bounded **task state object**: plan, open questions, retrieved facts, budget left; explicitly summarised rather than accumulated | LLM context degrades with length and position `[I]`; MemGPT-style virtual context shows tiering is implementable `[I]` (2310.08560) | **Adopted** (bounded state, explicit eviction) |
| 3 | **Episodic memory** | Records specific events with time and context | Append-only **episode log**: what was asked, planned, done, verified, with provenance and timestamps | LongMemEval and similar suites show multi-session recall is real but uneven `[I]` (2410.10813, Phase 5) | **Adopted** (already in Phase 5 §4; now first-class) |
| 4 | **Semantic memory** | Stores decontextualised facts | **Fact store** with bitemporal validity, trust labels, source spans | Phase 5 §4 design (D3, D4); provenance requirement is Phase 1 T4 | **Adopted** |
| 5 | **Procedural memory** | Learns skills that become automatic with practice | **Skill library**: executable plans/tools with pre/postconditions and regression tests | Voyager's skill library compounds capability and is reusable in a new world `[I]` (2305.16291) | **Adopted** (see Phase 12 §3.4) — the newest addition |
| 6 | **Consolidation / replay** | Offline replay moves memories into long-term storage and abstracts regularities | **Scheduled batch jobs**: dedupe, summarise episodes into facts, re-test skills, expire stale entries | Complementary-learning-systems theory `[P]`; replay is standard in continual learning `[P]`; in LLM agents it is engineering, not established science | **Heuristic under measurement** |
| 7 | **Prediction and prediction error** | Compares top-down expectation with input; error drives updating | **Verifier as predictor**: expected post-state → observed state; the delta is the error signal that triggers retry, repair or escalation | Predictive-processing theory `[P]`; the *engineering* form (read-back verification) is independently justified by Phase 8 §4 and already implemented as block B3 | **Adopted** (as verification architecture, not as a theory of mind) |
| 8 | **Executive control / inhibition** | Suppresses prepotent actions; arbitrates competing goals | **Policy engine + budget gate**: deny, require approval, cap cost/time; can veto the model's proposal | Phase 9 §4–§6 evidence on injection and over-broad agency `[I]`; already the D1/D2 decision | **Adopted** |
| 9 | **Planning and mental simulation** | Constructs and evaluates sequences before acting | **Plan object** with acceptance checks, cost estimate, and *simulated* dry-run for side effects | ReAct/plan-execute literature supports explicit plans but shows decomposition failures `[I]` (MAST, 2503.13657) | **Adopted** (plan object); *simulation fidelity* is **Unproven** |
| 10 | **Metacognition / confidence** | Monitors own certainty; triggers information seeking or deferral | **Calibrated confidence + abstention**: abstain, ask, or escalate when evidence is thin | Abstention is measurable (B1 has two abstention tasks); calibration of LLM self-report is known to be poor `[I]` | **Adopted** (abstain/ask); *trusting* model self-report is **rejected** (D1) |
| 11 | **Language** | Produces and comprehends structured communication | The LLM itself, plus typed interfaces for tool arguments | Phase 2, Phase 4 §3 | **Adopted** |
| 12 | **Neuromodulation** (global gain, salience, reward prediction error) | Global signals reconfigure processing: arousal, priority, learning rate | **Global control signals**: priority weight, verbosity/effort level, exploration rate, escalation sensitivity — as configuration, not as a simulated neurotransmitter | Dopamine reward-prediction-error is `[P]` in neuroscience; the software analogue is ordinary adaptive configuration `[P]` | **Heuristic under measurement** |
| 13 | **Dual-process control** (fast/automatic vs slow/deliberate) | Cheap habitual response, expensive deliberate reasoning when needed | **Effort router**: cheap tier for routine, deliberate multi-step for hard, with a *measured* trigger | RouteLLM-style routing shows real cost savings `[I]` (2406.18665); the "two systems" framing is a metaphor `[P]` | **Heuristic under measurement** (routing adopted; the metaphor is not) |
| 14 | **Affect, curiosity, social cognition, theory of mind** | Drives exploration, bonding, coordination | Would imply intrinsic motivation objectives and models of other agents | No reliable evidence these improve a personal assistant's task success; affective simulation risks misleading users | **Speculative (not adopted)** |

Two functions that a reader might expect are deliberately missing: **motor control** (no embodiment; the
"actuators" are APIs) and **consciousness / global workspace** (§7.1).

## 3. The four analogies that carry real architectural weight

These are the ones that changed the design, not just the vocabulary.

### 3.1 Multiple memory systems, not one vector store

The strongest transferable finding in cognitive science for our purposes is the *taxonomy*: episodic,
semantic and procedural memory behave differently — they are written differently, decay differently, and
are retrieved differently (Tulving's distinction `[P]`; complementary learning systems `[P]`). Phase 5
already separates conversation, profile, documents and memory records; this revision adds **procedural
memory as a first-class store** (§2 row 5), because "evolving intelligence" is mostly *skill acquisition*
in a software system, and skills are not facts.

**Concrete consequence.** A failed task produces an episode (store 3) and possibly a fact correction
(store 4); only a *verified, reusable* procedure becomes a skill (store 5). Mixing these was the single
most likely design error in Phase 5 §10.

### 3.2 Attention is a scheduler, not a slide

Human attention is capacity-limited and selective. The engineering reading: **context window is a scarce
resource with a budget, a priority function and an eviction policy**, and "put everything in the prompt"
is the failure mode the brain's limit exists to prevent. Because position and length effects are
measurable in LLMs `[I]` (2307.03172), this analogy passes F1–F3 and becomes a constraint: every step
declares its context budget, and the scheduler — not the model — decides what to evict (Phase 12 §4).

### 3.3 Prediction error is the verification signal we already use

Predictive processing says the brain is continually predicting its input and learning from the mismatch
`[P]`. We do **not** adopt predictive coding as a model architecture. We adopt one operational
consequence: **every action declares its expected post-condition, and the verifier compares observation
to prediction**. That is exactly Stage 0's B3 block (`verify_world`, independent read-back) — so the
analogy arrived after the implementation and confirms it, rather than motivating new machinery. This is
the right direction of inference and worth stating explicitly, because it is usually reversed.

### 3.4 Consolidation as a scheduled, testable job

Brains consolidate offline, and replay is implicated in skill learning `[P]`. The software analogue is a
**batch process with its own tests**: summarise episodes into candidate facts, deduplicate, expire stale
entries, re-run each skill's regression tests, and *demote* skills that stopped passing. It is
"brain-inspired" only in timing and purpose; its correctness is judged by whether the store is more
accurate afterwards, which is measurable (Phase 13 §3.3).

## 4. Useful heuristics that have not yet earned their place

- **Neuromodulation → global control signals.** Priority weight, effort level and exploration rate as
  explicit configuration is good engineering. Calling it dopamine adds nothing and risks implying a
  mechanism we do not have. **Status: heuristic**, to be kept only if per-task-family measurements show
  a gain over fixed settings.
- **Dual-process routing.** The router is justified by cost evidence `[I]` (2406.18665), not by the
  fast/slow metaphor. Phase 10's O5 ("does routing earn its complexity") remains open and unchanged.
- **Curiosity / intrinsic motivation.** Attractive for open-ended research agents; for a personal
  assistant with a defined workload, exploration consumes budget the user did not authorise. Not adopted
  without a measured task family that needs it.
- **Sleep-like consolidation cadence.** The *idea* of a nightly job is fine; the biology is decoration.
  Treat the schedule as a cost-latency trade-off to be measured, nothing more.

## 5. What remains speculative — recorded, not adopted

| Speculative claim | Why it is not adopted | What would change our mind |
|---|---|---|
| Consciousness, self-awareness, or "global workspace" as an emergent property of our stack | No operational definition, no measurement, no engineering consequence; claiming it would mislead users | A task-relevant capability that only a global-workspace mechanism provides, demonstrated under our harness |
| Spiking / neuromorphic compute as a cost or latency win for this workload | Our bottleneck is token cost and API latency, not multiply-accumulate energy; no hardware path in scope | A measured end-to-end cost or latency win on a real task family, on available hardware |
| Emotion simulation as a driver of better decisions | No evidence it improves task success; plausible harm in user trust | Controlled study showing better outcomes with affect modelling, with no deception risk |
| Theory-of-mind modules for multi-agent coordination | Multi-agent value is unproven even *without* ToM (Phase 4 §6); adding a model of other agents multiplies an unmeasured benefit | X-6-class evidence that coordination helps at all |
| Brain-scale parameter matching or "human-level" benchmarks | Parameter count is not a capability metric; benchmark matching is not a task outcome | Evidence tying scale to our task families' success |
| Continual learning without forgetting, because brains do it | Brains' mechanism is unavailable to us; catastrophic forgetting is an open ML problem (§7.4) | A method that survives our regression gates over multiple update cycles |

## 6. What brain inspiration must never be used to justify

This section is a guardrail, not a disclaimer:

1. **Autonomy by analogy.** "The brain acts without asking" is not an argument for a system that spends
   money, sends mail or changes its own permissions without approval. Phase 9's tiers stand unamended.
2. **Skipping evaluation.** "It learns like a brain" is not evidence that an update improved anything.
   Every change still faces the Phase 8 gates and Phase 13's update pipeline.
3. **Consciousness claims in user-facing language.** The assistant may be described as an assistant. It
   must not be described as aware, feeling, or "alive", including in marketing copy derived from this
   research.
4. **Complexity as sophistication.** A brain has ~86 billion neurons; our system should have the
   smallest number of components that passes the gates. Every added layer must name the gate it serves.

## 7. Where the metaphor breaks, stated plainly

### 7.1 There is no consciousness claim and no global workspace

Global workspace theory is a *theory of access consciousness* `[P]`; we do not implement it, and if we
later build a shared blackboard for coordination, that is a software pattern (Phase 12 §6), not evidence
of awareness. Readers should treat any future text that blurs this line as a defect in that text.

### 7.2 Brains are not modular in the way software is

The mapping table is a *functional* decomposition, not an anatomical one. Biological "modules" are
distributed, plastic and heavily interconnected; our components are discrete and versioned. Do not infer
from the table that the brain has a "skill library" or a "policy engine" — those are our constructs,
inspired by function, not located in tissue.

### 7.3 Development and evolution are not available to us

A brain is trained by development plus evolution over millions of years; an LLM is trained by gradient
descent on a corpus and adapted by prompts, tools and small fine-tunes. The analogy explains *why*
breadth and memory matter; it supplies no training method.

### 7.4 Forgetting is the unsolved part

Elastic weight consolidation (Kirkpatrick et al., PNAS 2017) `[I]` reduces but does not eliminate
catastrophic forgetting; recursive training on self-generated data degrades models (model collapse,
2305.17493) `[I]`. Brains handle this; our systems do not. Any document claiming a solved continual-
learning story for J.A.R.V.I.S. would be wrong, and Phase 13 §6 constrains weight updates accordingly.

### 7.5 The brain's cost profile is not ours

Brains run on ~20 W and are always on; our system pays per token and per call. Attention-as-budget in
our system means *money and latency*, not glucose. That is why the scheduler in Phase 12 §4 is a cost
controller as much as an information controller.

## 8. Consequences for the architecture (handoff)

| Consequence | Where it lands |
|---|---|
| Four memory stores with distinct write rules, procedural memory added | [Phase 12 §3](12-capability-integration-architecture.md), [Phase 13 §1](13-evolving-intelligence-update-pipeline.md) U1/U2 |
| Attention as an explicit budgeted scheduler | Phase 12 §4 |
| Verification as prediction-vs-observation, per step | Phase 12 §5; already B3 in `eval/harness/scorers.py` |
| Consolidation as a scheduled, tested batch job | Phase 13 §3.3; new experiment X-12 |
| Broad capability needs a registry and per-family measurement | Phase 12 §2, §7; Phase 15 Stage 1 |
| Evolution is skill/memory/model updates under gates, not "the system rewrites itself" | Phase 13, Phase 14 |
| No consciousness, autonomy, or solved-continual-learning claims | §0, §6, §7 — carried into Phase 15's risk register |

## 9. Findings classification

| Finding | Label | Basis |
|---|---|---|
| Separating episodic, semantic and procedural stores improves write discipline and auditability | **Plausible** | Phase 5 taxonomy + Voyager skill reuse `[I]`; not yet measured in our harness |
| A bounded context scheduler beats "fill the window" | **Plausible → testable** | Position/length degradation `[I]` (2307.03172); our B1/B2 runs are too small to show it |
| Verification as prediction-vs-observation is the right shape for a verifier | **Established** in our code | B3 read-back scoring in Stage 0; X-10 `skip_verification` mutation detection |
| Offline consolidation improves store accuracy | **Unproven** | No measurement; X-12 proposed |
| Curiosity, affect, ToM, neuromorphic compute improve this workload | **Speculative (not adopted)** | No task-family evidence |
| Brain inspiration implies consciousness | **Rejected** | Not a claim of this programme (§0.1, §6.3) |
| Catastrophic forgetting is solved for our update model | **Rejected** | EWC limits it `[I]`; model collapse `[I]` (2305.17493) |

**Source check for §9 and §2.** Fetched and confirmed on 2026-11-01 for this revision: Voyager
`arxiv.org/abs/2305.16291`; MemGPT `arxiv.org/abs/2310.08560`; model collapse `arxiv.org/abs/2305.17493`.
Carried over from Phase 1–10 (fetched earlier, same date-stamp): 2307.03172 (lost-in-the-middle),
2410.10813 (LongMemEval), 2503.13657 (MAST), 2406.18665 (RouteLLM). Neuroscience and cognitive-science
works are cited by author/year without identifiers and were **not** re-verified this session: they are
background framing, not decision evidence — treat them as **`[P]`** at best.

---

**What was learned, what remains uncertain, and the next research step.** *Learned:* the useful
borrowing from cognitive science is **taxonomic and control-theoretic** — several memory stores with
different write rules, attention as a bounded budget, verification as prediction error, consolidation as
a scheduled job — while the impressive-sounding analogies (consciousness, neuromodulation, affect,
neuromorphic efficiency) fail the falsifiability filter and are recorded as speculative. *Uncertain:*
whether the memory taxonomy pays for itself in measured task success, and whether a context scheduler
beats a naive prompt at our scale; both are unmeasured in Stage 0's 21 synthetic tasks. *Next research
step:* turn the taxonomy into an integration contract and per-family measurement — [Phase 12](12-capability-integration-architecture.md)
— so that "broad capability" stops being a claim and becomes a scorecard with families, skills and costs.
