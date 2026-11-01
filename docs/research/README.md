# J.A.R.V.I.S. Research Index

Research notes for Project J.A.R.V.I.S.: a personal AI system that understands goals, researches,
plans, uses tools, remembers context, executes approved tasks, verifies results, and improves through
measured experiments.

This index is the single entry point for the research phase. Each phase is published as a separate,
dated Markdown document. Nothing here is a final architectural commitment; every recommendation is
expected to be tested against the workload defined in Phase 1.

- **Research window:** started 2026-11-01. All sources re-checked 2026-11-01.
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

## Cross-cutting reading order

1. **01 → 02** establishes what J.A.R.V.I.S. must do and which models could do it.
2. **03 → 07** covers how models are built, orchestrated, remembered, spoken to, and served.
3. **08 → 09** defines how we will know whether any of it works, and what could go wrong.
4. **10** synthesises everything into a testable architecture.

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

## Current state and next action

All ten phase documents are published (v0.1–v0.3), and **Stage 0 of the
[roadmap](10-proposed-architecture-and-experiments.md#9-staged-roadmap) is complete**: the harness in
`eval/` runs task blocks B1–B3 reproducibly, records every attempt, compares against the frozen
reference baseline `baseline-a-stub.json`, and passes the planted-regression test X-10 (6 of 6
mutations caught, each by its expected gate). See the [execution notes](#execution-notes-stage-0--harness-and-baseline).

What that does **not** mean: no model has been run. The baseline is a deterministic keyword stub, so
its 100% scores are a ceiling by construction, not a capability result, and twelve user requirements
(Phase 1 §9, Q1–Q12) remain unanswered.

**Immediate next action:** decide Open decision O1 (workhorse model) within the Phase 1 cost envelope,
then freeze a **model** baseline with the harness unchanged and re-run X-10 against a stochastic
solver. No model should be adopted before that paired, cost-controlled run; nothing in Stage 0
recommends one.

**Review requests for the project owner:**

1. Answer Q1–Q12 in [Phase 1 §9](01-jarvis-workload-and-requirements.md#9-unresolved-requirements-questions-not-assumptions).
2. Confirm or amend the provisional latency/cost envelopes in [Phase 1 §6](01-jarvis-workload-and-requirements.md#6-latency-and-cost-envelopes).
3. Approve or reject the proposed architecture and its alternatives in [Phase 10 §2–§3](10-proposed-architecture-and-experiments.md#2-proposed-modular-architecture).

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
| 08 Evaluation | v0.2 | Harness not yet built (Stage 0) |
| 09 Security | v0.2 | CaMeL-style design not yet prototyped |
| 10 Architecture | v0.2 | Experiments X-1–X-10 not yet run |
