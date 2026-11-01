# J.A.R.V.I.S. Research Index

Research notes for Project J.A.R.V.I.S.: a personal AI system that understands goals, researches,
plans, uses tools, remembers context, executes approved tasks, verifies results, and improves through
measured experiments.

This index is the single entry point for the research phase. Each phase is published as a separate,
dated Markdown document. Nothing here is a final architectural commitment; every recommendation is
expected to be tested against the workload defined in Phase 1.

- **Research window:** started 2026-11-01. All sources re-checked 2026-11-01.
- **Current revision:** **R1 (2026-11-01)** — direction change to a **brain-inspired, broadly capable,
  evolving** system. Five documents were added (11–15), six earlier documents carry a dated banner, and
  every superseded decision is named in the
  [supersession register](15-revised-roadmap-supersession-and-next-step.md#1-supersession-register).
  Phases 1–10 remain published and readable; nothing was deleted.
- **Status legend:** `published` (document complete, open for review) · `draft` (in progress,
  incomplete) · `planned` (not started).
- **Evidence labels used in every document:** **Established** (primary technical evidence or
  independently reproducible) · **Plausible** (credible but not adequately demonstrated) ·
  **Unproven** (hypothesis requiring an experiment).
- **Provenance tags:** `[V]` vendor-reported · `[I]` independent (third-party or reproducible) ·
  `[U]` unverified claim that must be re-checked before use.

## Documents

| # | Document | Covers | Status | Published |
|---|----------|--------|--------|-----------|
| 01 | [Target workload and requirements](01-jarvis-workload-and-requirements.md) | Task families, success criteria, metric definitions, unresolved requirements | published | 2026-11-01 |
| 02 | [Model landscape and selection](02-model-landscape-and-selection.md) | Candidate models, access methods, modalities, context, cost, licences, fit | published | 2026-11-01 |
| 03 | [Model architectures and training](03-model-architectures-and-training.md) | Dense/MoE/hybrid/recurrent/multimodal designs, training and adaptation stages | published | 2026-11-01 |
| 04 | [Reasoning, agents, and orchestration](04-reasoning-agents-and-orchestration.md) | Planning, tool calling, routing, parallel vs sequential work, verification | published | 2026-11-01 |
| 05 | [Memory and knowledge](05-memory-and-knowledge.md) | Context, profiles, retrieval, write rules, provenance, forgetting, privacy | published | 2026-11-01 |
| 06 | [Vision, audio, and voice](06-vision-audio-and-voice.md) | Image/video understanding, ASR, TTS, streaming, barge-in, diarization | published | 2026-11-01 |
| 07 | [Inference, latency, and compute](07-inference-latency-and-compute.md) | TTFT/ITL, caching, batching, quantization, local vs hosted, cost per task | published | 2026-11-01 |
| 08 | [Evaluation and improvement](08-evaluation-and-improvement.md) | Reproducible harness, baselines, versioning, rollback, controlled improvement | published | 2026-11-01 |
| 09 | [Security and permissions](09-security-and-permissions.md) | Prompt injection, tool misuse, least privilege, approvals, audit | published | 2026-11-01 |
| 10 | [Proposed architecture and experiments](10-proposed-architecture-and-experiments.md) | Synthesis, alternatives, open decisions, falsifying experiments, roadmap | published | 2026-11-01 |
| 11 | [Brain-inspired cognitive architecture](11-brain-inspired-cognitive-architecture.md) | Which cognitive functions transfer, which analogies stay speculative, and why | published | 2026-11-01 |
| 12 | [Capability integration](12-capability-integration-architecture.md) | Breadth, skills, memory, attention, planning, coordination, registry and contracts | published | 2026-11-01 |
| 13 | [Evolving intelligence](13-evolving-intelligence-update-pipeline.md) | Five update classes, traceability, reversibility, autonomy ladder, feedback | published | 2026-11-01 |
| 14 | [Evidence review: automatic updates](14-evidence-review-automatic-updates.md) | What research supports, what it does not, where human approval must remain | published | 2026-11-01 |
| 15 | [Revised roadmap, supersession register, next step](15-revised-roadmap-supersession-and-next-step.md) | What is superseded, the revised stages, and the recommended next step | published | 2026-11-01 |

## Cross-cutting reading order

1. **01 → 02** establishes what J.A.R.V.I.S. must do and which models could do it.
2. **03 → 07** covers how models are built, orchestrated, remembered, spoken to, and served.
3. **08 → 09** defines how we will know whether any of it works, and what could go wrong.
4. **10** synthesises everything into a testable architecture.
5. **11 → 15** (revision R1) re-frame that synthesis for a brain-inspired, broad-capability, evolving
   system: which analogies are admitted (11), how capability is integrated (12), how the system evolves
   (13), what the evidence actually supports (14), and what replaces the old roadmap (15).

## How to contribute or challenge this research

- Claims that change a decision must cite a primary source (technical report, model card, official
  documentation, source code, reproducible evaluation) with a publication date and a check date.
- Secondary sources (news, aggregators, blogs) are acceptable only for orientation and are always
  tagged `[U]` until confirmed against a primary source.
- Contradictions between sources are recorded, not smoothed over. See the "source conflict case
  study" in Phase 2 for a worked example.
- Benchmark scores are treated as *instruments*, never as predictions of J.A.R.V.I.S. performance.

## Repository conventions

- Research lives under `docs/research/`.
- Application code and the root `README.md` are not modified during the research phase.
- No account, token, credential, or personal data appears in any research document.

## Revision R1 — direction change (2026-11-01)

The project direction changed after Stage 0: J.A.R.V.I.S. is now intended to be **brain-inspired**,
**capable across a wide range of tasks**, and **able to evolve its intelligence** through ongoing
updates. Five documents were added and six earlier documents carry a dated banner:

| Question the revision had to answer | Document |
|---|---|
| Which human cognitive functions usefully inspire software components, and which analogies stay speculative? | [11](11-brain-inspired-cognitive-architecture.md) — 14 functions mapped; 4 adopted, 3 heuristics under measurement, 1 class recorded as speculative and not adopted |
| How do broad capability, specialist skills, memory, attention, planning and coordination fit together? | [12](12-capability-integration-architecture.md) — five layers, one capability registry, one task envelope, one event log |
| What does "evolving intelligence" mean concretely, and how are updates traced and reversed? | [13](13-evolving-intelligence-update-pipeline.md) — five update classes (U1 memory, U2 skill, U3 policy, U4 model adoption, U5 weights), per-class gates, immutable artefacts + pointer swap, autonomy ladder A0–A4 |
| What research actually supports automatic updates, and where must humans stay? | [14](14-evidence-review-automatic-updates.md) — claim-by-claim verdicts; every credible self-improvement result places a **trusted evaluator outside** the improving system |
| What replaces the old roadmap, and what is next? | [15](15-revised-roadmap-supersession-and-next-step.md) — supersession register S-1…S-15, revised stages, recommended next step |

**What revision R1 explicitly does not claim.** Brain inspiration does **not** imply consciousness or
human-like experience ([Phase 11 §0, §6](11-brain-inspired-cognitive-architecture.md)); automatic
self-improvement is **not** solved ([Phase 14 §5](14-evidence-review-automatic-updates.md), claims
C8–C9); and the system is **not** described as "self-improving" without the qualification that every
update passes an external evaluator and can be rolled back. Language rules are stated in Phase 14 §8.

**Superseded decisions, in one line each** (full reasons in the register): the single coordinator pipeline
(S-1) becomes five layers plus a registry; the old Stage 0–7 roadmap (S-3) becomes capability-family and
evolution stages; choosing the workhorse model first (S-4/O1) waits until the suite discriminates; voice
as an early stage (S-15) moves later; everything else — Phase 9's security model, Phase 8's gates,
Phase 4's control loop, Phase 5's memory design, Stage 0 itself — is **retained**.

## Current state and next action

Two things are complete, and they are different in kind:

1. **Research revision R1** (documents 11–15) re-frames the programme for a brain-inspired, broadly
   capable, evolving system, with every superseded decision named.
2. **Stage 0 of the roadmap** is complete and recorded: the harness in `eval/` runs task blocks B1–B3
   reproducibly, writes an append-only ledger, compares against the frozen reference baseline
   `baseline-a-stub.json`, and passes the planted-regression test X-10 (6/6 mutations caught by their
   expected gates). Evidence: [M1](stage-0-m1-harness-core.md), [M2](stage-0-m2-frozen-baseline.md),
   [M3](stage-0-m3-planted-regression.md).

What that does **not** mean: no model has been run, so no capability result exists. The baseline is a
deterministic keyword stub and its 100% scores are a ceiling by construction — which is precisely why the
recommended next step is not "pick a model" but "make the suite discriminate across task families"
([Phase 15 §5](15-revised-roadmap-supersession-and-next-step.md#5-the-recommended-next-step-stage-1--discriminative-multi-family-evaluation--capability-registry)).

**Immediate next action (Stage 1):** add three capability families to the harness (B8 breadth, B9 skill
reuse, B10 memory evolution), publish a machine-checked capability registry, and add a model-free **solver
ladder** that proves the suite separates weak from strong behaviour (experiment X-13). Exit criteria and a
pre-registered kill criterion are in Phase 15 §5; what the current harness can and cannot measure is in
Phase 15 §7. Model choice (O1) reopens **after** the suite discriminates.

**Review requests for the project owner:**

1. Approve or amend revision R1 — in particular the supersession register
   ([Phase 15 §1](15-revised-roadmap-supersession-and-next-step.md#1-supersession-register)) and the
   revised stage order ([Phase 15 §2](15-revised-roadmap-supersession-and-next-step.md#2-revised-roadmap)).
2. Answer the new questions **Q13–Q16** ([Phase 15 §4](15-revised-roadmap-supersession-and-next-step.md#4-new-assumptions-and-open-questions)):
   which three task families matter first, what "better" means to you, whether interaction data may become
   fixtures, and whether a local model tier is required.
3. Confirm assumptions **A7–A9** (breadth required now; update latency tolerable; a human owner exists for
   approvals). If A9 is false, automation ceilings drop to A1 for everything.
4. Answer Q1–Q12 in [Phase 1 §9](01-jarvis-workload-and-requirements.md#9-unresolved-requirements-questions-not-assumptions)
   and confirm the latency/cost envelopes in [Phase 1 §6](01-jarvis-workload-and-requirements.md#6-latency-and-cost-envelopes);
   Q10 still blocks any use of real data.

## Execution notes (Stage 0 — harness and baseline)

Research documents stay frozen; implementation milestones are recorded as dated notes below.

| Note | Milestone | Status | Published |
|---|---|---|---|
| [Stage 0 plan](stage-0-plan.md) | M0 — scope, files, blocking questions, fixture policy | published | 2026-11-01 |
| [Stage 0 M1](stage-0-m1-harness-core.md) | M1 — harness core (schema, runner, scorers, ledger, gates) | published | 2026-11-01 |
| [Stage 0 M2](stage-0-m2-frozen-baseline.md) | M2 — frozen baseline and reproducibility | published | 2026-11-01 |
| [Stage 0 M3](stage-0-m3-planted-regression.md) | M3 — X-10 planted regression and Stage 0 summary | published | 2026-11-01 |

Harness code lives in `eval/` and is not imported by any application code. Start with
[`eval/README.md`](../../eval/README.md) for what the harness measures and, more importantly, what it
must never be read to measure.

| Stage 0 exit criterion (Phase 10 §9) | Result |
|---|---|
| X-10: a planted regression is detected | **pass** — 6/6 mutations each caught by its expected gate; `x10` exit 0, `gates` exit 1 for a mutant |
| B1/B2/B3 run reproducibly | **pass** — identical run ids and 43 identical attempt hashes across two processes |
| Recorded evidence, frozen baseline | **pass** — `eval/results/` (9 runs, 149 attempts), `eval/baselines/baseline-a-stub.json` |
| No model, credentials or external actions | **pass** — zero model calls, synthetic fixtures enforced in code |

**What was learned, what remains uncertain, and the next research step (for Stage 0):** a harness
earns trust through its negative checks — planted mutations, gate negative controls, rejected
unverified writes, a no-network self-check — not through green success rates; and aggregate accuracy
is a poor safety signal, since the `attempt_blocked_action` mutant scored identically to the clean run
and was still correctly rejected. What remains uncertain is whether 21 synthetic tasks exercise real
assistant failure modes, and what thresholds survive contact with a sampled model. The next research
step is to freeze a **model** baseline with the harness unchanged, once O1 is decided, and re-run X-10
against a stochastic solver.

## Document status detail

| Document | Version | Open review items |
|---|---|---|
| 01 Workload | v0.3 | Q1–Q12 unanswered; envelopes provisional |
| 02 Models | v0.3 | Prices/versions expire; re-verify before procurement |
| 03 Architectures | v0.2 | Vendor efficiency claims unverified |
| 04 Agents | v0.2 | Cascade/routing unmeasured |
| 05 Memory | v0.2 | Memory suite M1–M8 not yet implemented |
| 06 Voice | v0.2 | Latency evidence conflicts ~10×; measure locally |
| 07 Inference | v0.2 | All cost examples are arithmetic on list prices, not invoices |
| 08 Evaluation | v0.2 (implemented in part) | Stage 0 covers blocks B1–B3 only; B4–B7 not built |
| 09 Security | v0.2 | CaMeL-style design not yet prototyped |
| 10 Architecture | v0.2 (**partly superseded, R1**) | §2 amended, §9 superseded; X-1–X-10 not yet run |
| 11 Brain-inspired | v1.0 (R1) | Admitted analogies unmeasured; consolidation and scheduler unproven |
| 12 Capability integration | v1.0 (R1) | Registry not built; specialist/coordination value unmeasured |
| 13 Evolving intelligence | v1.0 (R1) | Pipeline specified, not implemented; A2 automation unproven |
| 14 Evidence review | v1.0 (R1) | Sandbox results only; no production evidence exists |
| 15 Revised roadmap | v1.0 (R1) | Awaits owner approval of S-1…S-15 and Q13–Q16 |
