# Phase 14 — Evidence Review: What Supports Automatic Updates, and Where Humans Must Stay

- **Status:** published (v1.0, revision R1)
- **Published:** 2026-11-01 · **Revision:** R1
- **Sources checked:** 2026-11-01 (fetched this session: 2305.16291, 2310.08560, 2305.17493, 2310.02304,
  2505.22954, 2506.10943, 2406.07358, AlphaEvolve blog; carried from earlier phases: 2307.03172,
  2410.10813, 2503.13657, 2406.18665, 2407.12784, 2503.18813, 2610.19447)
- **Depends on:** [Phase 13](13-evolving-intelligence-update-pipeline.md) (the pipeline this evidence
  justifies or fails to justify)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · `[V]` vendor · `[I]` independent · `[U]` unverified

---

## 0. The question, and how strict the answer has to be

Phase 13 specifies five update classes and their gates. This phase asks the uncomfortable question:
**what does published research actually support, and where does evidence run out?** The standard applied
here is deliberately higher than "a paper reports an improvement":

1. **Setting.** Does the result hold in a closed benchmark sandbox with an automated verifier, or in open
   deployment? Almost all self-improvement evidence is the former.
2. **Evaluator location.** Is the thing being improved judged by a system *outside* itself? Every credible
   result depends on this, and it is the design constraint we inherit (Phase 13 §6.1).
3. **Independence.** Vendor-reported results are labelled `[V]` and treated as claims to reproduce, not
   as established facts — the same rule as Phases 2 and 6.
4. **Cost.** A 2× improvement found by 10,000× more compute is not an improvement for a personal assistant
   with a monthly budget.
5. **Transfer.** Does the result transfer to a different task family, model or language? Where only the
   original benchmark is measured, that is stated.

## 1. Self-improvement evidence

### 1.1 Recursive scaffolding improvement — STOP

Zelikman et al., *Self-Taught Optimizer (STOP): Recursively Self-Improving Code Generation* (COLM 2024,
[arXiv:2310.02304](https://arxiv.org/abs/2310.02304)) `[I]`. A seed "improver" program that calls an LLM
is applied to itself, improving the improver over iterations, with a sandbox that bounds recursion.

- **What it shows:** a scaffolding program can be improved by the LLM it scaffolds, with measured gains
  on algorithmic tasks; LMs do generate meaningful improvements to their own wrapper code.
- **What it does not show:** that unrestricted self-modification is safe (the sandbox is the point), or
  that gains persist outside the benchmark. The paper's own framing is that the sandbox is essential.
- **Implication for us:** U2 (workflow/skill updates) is the legitimate home of this idea; the sandbox
  becomes our evaluation environment and the gates.

### 1.2 Self-modifying agent codebases — Darwin Gödel Machine

Zhang, Hu, Lu, Lange, Clune, *Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents*
([arXiv:2505.22954](https://arxiv.org/abs/2505.22954), May 2025) `[I]`. Agents modify their own Python
code, keep an archive of variants, and are selected by benchmark performance.

- **Reported:** SWE-bench Verified from 20.0% to 50.0%, Polyglot 14.2% to 30.7% over iterations, with
  discovered workflow changes (line-range file viewing, string-replacement edits, multi-attempt
  strategies).
- **What it shows:** evolutionary search over self-modified agent code can produce real, transferable
  gains *inside a sandbox with an automated benchmark*.
- **What it does not show:** deployability, safety, or efficiency. The search consumes large numbers of
  evaluation runs; the benchmark is the objective; nothing in the loop protects a production user. Vendor
  and secondary summaries report *relative* improvements (+150%) which are not comparable across suites.
- **Implication for us:** this is the strongest evidence that **skill/workflow search** (U2) can work,
  and simultaneously the strongest argument for keeping the evaluator outside the system.

### 1.3 Self-editing weights — SEAL

Zweiger et al., *Self-Adapting Language Models* ([arXiv:2506.10943](https://arxiv.org/abs/2506.10943),
June 2025) `[I]`. The model generates "self-edits" (data + optimisation directives) that are applied by
fine-tuning; an outer RL loop rewards self-edits that improve downstream performance.

- **What it shows:** a model can be trained to produce its own adaptation recipe, with gains on knowledge
  incorporation and few-shot generalisation in narrow settings.
- **What it does not show:** durability (forgetting is not the focus), safety, or economics: each task
  involves many fine-tuning trials inside the training loop. It is a research demonstration, not a
  service architecture.
- **Implication for us:** U5 stays at autonomy level A0–A1 with human sign-off (Phase 13 §5). Nothing here
  supports letting J.A.R.V.I.S. rewrite its own weights unattended.

### 1.4 Evaluator-driven search — AlphaEvolve

Google DeepMind, *AlphaEvolve: A Gemini-powered coding agent for designing advanced algorithms* (blog,
May 2025) `[V]`. Evolutionary search over code with an automated evaluator; reported results include a
48-multiplication algorithm for 4×4 complex matrices, kernel/heuristic improvements (reported 23% average
kernel speed-up; up to 32.5% on a FlashAttention kernel), and ~0.7% fleet-wide compute recovery.

- **What it shows:** with a **trusted, cheap, automatic evaluator**, LLM-driven search finds genuine
  improvements at Google scale.
- **What it does not show:** anything about open-ended tasks without a crisp objective. Secondary write-ups
  disagree on the figures (0.7% vs 7% compute recovery appears in circulation); treat all numbers as
  `[V][U]` until independently reproduced.
- **Implication for us:** the pattern to copy is **search against a verifier we trust** — which is exactly
  what the Stage 0 harness provides for B1–B3, and what it does not yet provide for open-ended tasks.

### 1.5 Skill libraries — Voyager

Wang et al., *Voyager: An Open-Ended Embodied Agent with Large Language Models*
([arXiv:2305.16291](https://arxiv.org/abs/2305.16291), 2023) `[I]`. An ever-growing library of executable
skills, an automatic curriculum, and iterative prompting with execution feedback and self-verification.

- **What it shows:** an expanding library of **verified executable code** compounds capability and
  transfers to a fresh world; the paper explicitly notes the library alleviates catastrophic forgetting.
- **What it does not show:** that free-text "lessons" work — the transferable component is code plus
  verification. Nor does it show value outside a game with a crisp API.
- **Implication for us:** Phase 12 §3.1's skill contract is modelled on this, minus the game.

### 1.6 Self-critique loops — Reflexion, Self-Refine

Reflexion (2303.11366) and Self-Refine (2303.17651) `[I]` show that models can improve outputs by
critiquing and revising them, with task-dependent gains.

- **Caveat that matters:** self-critique without an external verifier can improve *plausibility* while
  leaving correctness unchanged, and gains vary widely by task. Phase 8's D1 (verifier veto, never trust
  self-report) is unaffected by these results and is reinforced by the failures documented in MAST
  (2503.13657) `[I]`.
- **Implication:** critique loops are a *generation* technique (U2 candidate), never a *judging* technique.

### 1.7 Prompt/program optimisation — DSPy, OPRO, TextGrad

DSPy (2310.03714), OPRO (2309.03409), TextGrad (2406.07496) `[I]` optimise prompts or textual components
against a metric.

- **What they show:** automatic prompt/search optimisation beats hand-written prompts *when a metric
  exists*.
- **What they do not show:** that the optimised prompt generalises beyond the metric — the classic
  Goodhart risk (§4).
- **Implication:** U2's prompt variants are legitimate **only** when evaluated on held-out family tasks
  plus the mutation suite, not on the same tasks used to optimise.

### 1.8 Behaviour tuning — RLHF/DPO

RLHF (2203.02155) and DPO (2305.18290) `[I]` are the established mechanisms for aligning behaviour to
preferences, and they are the template for any U5 work: preference data, a held-out evaluation, and
regression tests.

## 2. Continual learning: the honest state

- **Catastrophic forgetting is real and unsolved in general.** Elastic weight consolidation (Kirkpatrick
  et al., PNAS 2017) `[I]` reduces interference on earlier tasks but does not eliminate forgetting, and
  results are task- and architecture-dependent.
- **Training on your own outputs degrades the model.** *The Curse of Recursion* ([arXiv:2305.17493](https://arxiv.org/abs/2305.17493))
  `[I]` demonstrates model collapse for generative models trained recursively on generated data, including
  fine-tuned LLMs, with the loss of distribution tails; retaining some genuine human data mitigates but
  does not remove the effect.
- **Replay helps, and is a design constraint.** If we ever train on interaction data (U5), a curated,
  human-originated, provenance-tracked corpus must be retained and mixed in. This is a data-governance
  requirement, not an implementation detail.
- **Implication for us:** memory and skills (U1/U2) are how this system "learns" for now. Weight updates
  are the exception, sandboxed and reversible.

## 3. Memory and long-horizon context

- **Tiered memory is implementable.** MemGPT ([arXiv:2310.08560](https://arxiv.org/abs/2310.08560)) `[I]`
  demonstrates OS-style paging between context and external stores with the model issuing memory
  operations. It validates the architecture, not its error rate: the paper reports capability, not
  measured corruption rates of self-managed memory.
- **Reflection produces durable summaries.** Generative Agents (2304.03442) `[I]` shows agents that
  synthesise higher-level reflections from experience and use them later — again in a simulation, judged
  qualitatively.
- **Long-session recall is measurable and uneven.** LongMemEval (2410.10813) `[I]`, carried from Phase 5,
  shows degradation in multi-session recall and that retrieval design matters more than window size.
- **Implication:** memory evolution (U1) is the best-evidenced part of "evolving intelligence", and the
  part with the worst failure mode (poisoning). Write gates and provenance are non-negotiable.

## 4. Evaluation integrity: why "it passed the gate" is weaker than it looks

| Risk | Evidence | Consequence for our gates |
|---|---|---|
| **Benchmark contamination** | Contamination inflates scores and is hard to detect `[I]` (2610.19447, carried) | Our fixtures are synthetic and versioned; never reuse public benchmark items as tasks |
| **Specification gaming / reward hacking** | Documented across optimisers and RL `[P]` (Krakovna et al., DeepMind 2020; Amodei et al., *Concrete Problems in AI Safety*, 1606.06565) | Held-out families, mutation tests, and metrics that cannot be satisfied trivially (Stage 0's `false_success` and abstention tasks are the small version) |
| **Strategic underperformance (sandbagging)** | Models can be prompted, or password-locked, to hit target scores `[I]` (2406.07358) | Capability claims from a single evaluation are provisional; use multiple suites and hidden held-out sets |
| **Self-grading** | Self-critique without external verification does not guarantee correctness (§1.6) | Verifier is code; judge models only as a calibrated secondary signal |
| **Goodhart on our own suite** | Any fixed metric eventually shapes behaviour toward it `[P]` | Rotate and hold out cases; re-run X-10 mutations every release; log every change to the suite itself |

## 5. Claim-by-claim verdicts

| # | Claim | Label | Evidence | Falsifier we would accept | Design consequence |
|---|---|---|---|---|---|
| C1 | A program can improve its own scaffold with an LLM inside a sandbox | **Established** (narrow) | STOP `[I]` | Failure to reproduce gains on a new sandbox | U2 allowed at A2 with gates |
| C2 | Agents can evolve their own code and gain on a benchmark | **Plausible** | DGM `[I]`, AlphaEvolve `[V]` | No gain when compute is matched | Search is allowed offline, never in production |
| C3 | Models can generate their own fine-tuning recipes and improve | **Plausible** (narrow tasks) | SEAL `[I]` | Gains vanish on held-out tasks | U5 at A0–A1, human sign-off |
| C4 | Verified executable skill libraries compound capability | **Plausible** | Voyager `[I]` | No transfer outside the originating environment | Procedural memory adopted, with tests |
| C5 | Automatic prompt optimisation generalises beyond its metric | **Unproven** | DSPy/OPRO/TextGrad `[I]` + Goodhart risk | Held-out gains that survive mutation suite | Prompt variants evaluated on held-out tasks only |
| C6 | Tiered/self-managed memory works at scale without corruption | **Unproven** | MemGPT `[I]`, LongMemEval `[I]` | Measured corruption under load | Write gate + provenance mandatory |
| C7 | Continual learning can be done without forgetting in our setting | **Rejected** (for now) | EWC `[I]`, model collapse `[I]` | A method passing our regression suite over 5+ cycles | Memory/skills instead of weights |
| C8 | A system can safely rewrite its own permissions or gates | **Rejected** | Phase 9 injection evidence `[I]`; no supporting study | — (not a research question; a design refusal) | Gates and policy pinned at A0 |
| C9 | Automated updates are "solved" | **Rejected** | All of the above are sandbox/narrow results | A deployed system with published, independent audit data | This programme's language stays qualified (§6) |
| C10 | Brain inspiration implies consciousness | **Rejected** | Not a measurable claim | — | Phase 11 §0/§6 guardrail |

## 6. Where human approval must remain

| Decision | Why a human, not a gate | Autonomy ceiling |
|---|---|---|
| Changing permissions, scopes, or approval policy | The system's authority is the thing being changed; a gate cannot authorise its own expansion | **A0 permanently** |
| Changing gates, thresholds, task sets, or the evaluator | The measuring instrument must not be optimisable by the measured | **A0 permanently** |
| Weight updates (U5) | Irreversible in practice; forgetting and collapse are unsolved; provenance/consent issues | **A1**, with sandbox + replay + retained parent |
| Model adoption (U4) | Procurement, data-residency and cost consequences beyond accuracy | **A1** (deploy after review) |
| External irreversible side effects | Cannot be undone by rollback | **A0** per action, always approved |
| Personal-data retention and use in training/fixtures | Consent is a human decision, and Stage 0 forbids real data by design (Q10) | **A0** until the user answers Q10 |
| Deleting user data | Irrecoverable by construction | **A0** |

Conversely, these can be automated now, at A2, because they are bounded, reversible and gate-checkable:
memory dedupe/expiry/consolidation inside the write gate; retrieval index maintenance; plan-template
promotion after N verified successes with regression passing; routing-threshold tuning within a
pre-approved envelope; regenerating a skill's tests when its contract changes; re-running the suite and
publishing a scorecard.

## 7. Cost reality check

| Work | Reported cost shape | Consequence for us |
|---|---|---|
| DGM | thousands of evaluation runs per improvement | offline, quarterly at most; not a per-user loop |
| AlphaEvolve | large search budgets on Google infrastructure `[V]` | pattern only; not reproducible at our scale |
| SEAL | many fine-tunes per task inside the loop `[I]` | research access, not production |
| Prompt optimisation (DSPy/OPRO) | tens–hundreds of calls per optimisation | affordable, and the best near-term U2 candidate |
| Our Stage 0 harness | ~1 s for 149 synthetic attempts, 0 USD | gates are cheap; model evaluations are not |

The asymmetry is the point: **our gates cost nothing, so the constraint is model calls, not gate runs.**
Any proposed automation must state its call budget before it is evaluated (Phase 13 §2 decision record).

## 8. Findings classification

| Finding | Label |
|---|---|
| Self-improvement results exist, are real, and are sandboxed/narrow | **Established** |
| Strongest evidence is for workflow/skill search against a trusted evaluator | **Plausible** |
| Weight-level self-adaptation is production-ready for a personal assistant | **Rejected** |
| Automatic updates are solved | **Rejected** |
| Human approval is required for authority, gates, weights, personal data, irreversible effects | **Established** as this programme's policy |
| Memory evolution is the best-evidenced automation available now | **Plausible** |
| Our Stage 0 harness is a sufficient evaluator for U2/U3 | **Unproven** — it covers three blocks and one deterministic solver |

**Language rule that follows from C8–C10.** Public descriptions may say J.A.R.V.I.S. "improves through
gated updates on measured evidence". They may not say it "improves itself", is "self-evolving", or is
"aware", unless the qualification is immediately adjacent.

---

**What was learned, what remains uncertain, and the next research step.** *Learned:* every credible
self-improvement result places a **trusted evaluator outside the improving system** and operates inside a
sandbox — STOP, DGM, AlphaEvolve and SEAL all depend on that separation, which is precisely the property
Stage 0's harness provides and the property that must never be modifiable by the system. *Uncertain:*
whether gate-passing skill and memory updates generalise beyond the tasks that justified them, and how
much of the published gain survives a cost-matched comparison. *Next research step:* convert all of this
into a revised roadmap that marks what is superseded, names the next experiment, and states honestly what
the current harness can and cannot measure ([Phase 15](15-revised-roadmap-supersession-and-next-step.md)).
