# Phase 3 — Model Architectures and Training

- **Status:** published (v0.2, open for review)
- **Published:** 2026-11-01
- **Sources checked:** 2026-11-01
- **Related:** [Index](README.md) · [Phase 2 — Model landscape](02-model-landscape-and-selection.md) · Phase 7 (serving), Phase 8 (evaluation)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · **`[V]` vendor** · **`[I]` independent** · **`[U]` unverified**

> **Revision R1 (2026-11-01) — status of this document.** Retained as published. §2–§4 (architecture
> families) are now framed by [Phase 11](11-brain-inspired-cognitive-architecture.md), which decides which
> brain analogies enter the design; §8–§9 (adaptation, no foundation training) are **retained and
> constrained** by [Phase 13 §1.1](13-evolving-intelligence-update-pipeline.md) (S-10). Nothing here is
> withdrawn; see the [supersession register](15-revised-roadmap-supersession-and-next-step.md#1-supersession-register).

---

## 0. Purpose and the one distinction that matters

The brief asks us to distinguish **training a foundation model** from **adapting an existing model**.
That distinction organises everything below.

| | Train a foundation model | Adapt an existing model |
|---|---|---|
| What you produce | New weights from scratch, or a continued-pretraining run | Behavioural change on top of frozen (or lightly tuned) weights |
| Typical levers | Data mixture, architecture, optimiser, distributed training, post-training | Prompting, tools, retrieval, SFT/LoRA, preference tuning, quantisation, decoding |
| Cost anchor | OpenAI reports **2.1M H100-hours** to train gpt-oss-120b `[V]` ([model card](https://cdn.openai.com/pdf/419b6906-9da6-406c-a19d-1bb078ac7637/oai_gpt-oss_model_card.pdf)) — and that is a *mid-size* open model | Hours to days of one GPU for LoRA; minutes for prompt changes |
| Evidence needed to justify | A measured capability gap that no adaptation can close | A measured baseline plus a specific error class to fix |
| Our position | **Not justified** (§9) | **The default** (§8) |

This phase therefore reads as: *what the architecture and training literature establishes, what 2026
frontier reports actually disclose, which choices are load-bearing for J.A.R.V.I.S., and which
adaptation levers are worth testing.*

**Scope note:** we do not reproduce training runs. Claims below are either from primary technical
reports (strongest), from peer-reviewed or widely replicated papers (strong), or vendor claims
(labelled). Nothing here is a substitute for Phase 7 measurement of serving cost.

---

## 1. Method

- Architecture claims are taken from **disclosing** technical reports where possible. In 2026, the
  most detailed disclosures come from Chinese open-weight labs (DeepSeek, Moonshot, Alibaba) rather
  than the closed frontier labs, whose system cards are safety documents, not architecture documents.
- Where a closed model's architecture is described qualitatively (e.g. GPT-6 Astra's "recurrent
  depth"), it is recorded as a **hypothesis about direction**, not a design specification.
- Every "this is better" claim is separated into *capability* (does it help the model) and
  *economics* (does it reduce cost at equal quality), because those two are routinely conflated.

---

## 2. Dense Transformers and attention variants

### 2.1 The established baseline

**Established.** The decoder-only Transformer with pre-normalisation, RMSNorm, rotary position
embeddings, SwiGLU feed-forward layers, and grouped-query or multi-head latent attention is the
default 2026 architecture. This is not controversial and is visible in every disclosed model in
Phase 2 (e.g. gpt-oss uses pre-LN and RMSNorm with residual dimension 2880 `[V]`).

Two practical consequences for J.A.R.V.I.S.:

1. **Architecture is not where we get differentiation.** The differences between candidates are in
   data, scale, post-training, and serving engineering.
2. **Attention variant choice is an economics decision** (KV-cache size, bandwidth per token), which
   Phase 7 must measure and Phase 3 can only explain.

### 2.2 Why KV cache dominates everything

**Established.** Autoregressive decoding is memory-bandwidth bound: each generated token reads the
model weights and the KV cache. Reducing KV-cache bytes per token reduces the cost of long contexts
roughly proportionally. The strongest 2026 primary evidence:

- **DeepSeek-V4** (1.6T/49B and 284B/13B MoE, 1M context) reports that at 1M tokens, V4-Pro needs
  **27% of single-token inference FLOPs and 10% of the KV cache** of DeepSeek-V3.2, achieved with a
  hybrid attention scheme (Compressed Sparse Attention + Heavily Compressed Attention) plus
  Manifold-Constrained Hyper-Connections and the Muon optimiser `[V]`
  ([arXiv:2606.19348](https://arxiv.org/abs/2606.19348)).
- **DeepSeek-V4.1-Flash** (552B backbone, 8B active prefill / 16B active decode, 1M context) reports a
  global KV-cache footprint of **890 bytes per token** — about ¼ of V4-Flash — using cross-layer KV
  reuse (CSA2) and **FP4 KV caching**, with a further ~8× reduction in persistent cache via
  "SWA Bounded Replay" `[V]` ([arXiv:2609.19969](https://arxiv.org/abs/2609.19969)).

Interpretation: **long context is now an engineering property, not a model property.** Two systems
with "1M context" can differ by 4–10× in memory traffic. For J.A.R.V.I.S., which wants to stuff
personal documents into context occasionally, this is the difference between an affordable and an
unaffordable design. It is also why Phase 5 argues for retrieval over stuffing.

**Plausible but unverified:** that these KV-compression techniques preserve retrieval fidelity at
long range. The reports measure efficiency carefully and quality less so; an independent
needle-in-haystack and multi-hop evaluation at 500K+ would be required (Phase 8).

### 2.3 Attention variants worth knowing

| Variant | Mechanism | Cost profile | Evidence | Relevance to J.A.R.V.I.S. |
|---|---|---|---|---|
| MHA / GQA / MQA | Fewer KV heads ⇒ smaller cache | Cache ↓, quality usually ≈ | Well established in open implementations | Default expectation |
| MLA (multi-head latent attention) | Compress KV into a latent vector | Cache ↓↓ | Used by DeepSeek-V2/V3 and Kimi K2/K3 `[V]` | Serving efficiency |
| Sliding-window / hybrid local-global | Exact attention only nearby | FLOPs ↓ on long inputs | Used in V4.1-Flash (first two layers SWA-only) `[V]` | Long documents |
| Sparse/compressed attention (CSA/HCA) | Compress + sparse select | FLOPs ↓↓ at 1M | DeepSeek V4 `[V]` | 1M-token context |
| IO-aware kernels (FlashAttention family) | Tiling to avoid HBM round trips | Wall-clock ↓, same math | Widely replicated [I] | Serving throughput |

### 2.4 Open problems (attention)

- **Effective vs. nominal context.** Advertised windows grow faster than measured usable recall.
  The 2023 "lost in the middle" result — accuracy U-shaped in the position of the relevant document,
  with middle positions sometimes below closed-book baseline `[I]`
  ([arXiv:2307.03172](https://arxiv.org/abs/2307.03172), TACL 2024) — is the canonical warning, and
  2026 replications of the effect on newer models are ongoing but not conclusive `[U]`.
- **Position generalisation** beyond the trained length.
- **Attention-sink behaviour** that makes prefix caching and eviction policies non-obvious (relevant
  to Phase 7).

---

## 3. Mixture of Experts and routing

### 3.1 Mechanism and the trade it actually makes

Sparse MoE decouples **parameter count** (knowledge capacity, memory footprint) from **FLOPs per
token** (compute). You pay memory for every expert and compute for a few.

Disclosed 2026 configurations show the design space:

| Model | Total | Activated | Routed experts | Active per token | Shared | Notes |
|---|---|---|---|---|---|---|
| DeepSeek-V4-Pro `[V]` | 1.6T | 49B | — | — | — | 1M context |
| DeepSeek-V4-Flash `[V]` | 284B | 13B | — | — | — | 1M context |
| DeepSeek-V4.1-Flash `[V]` | 552B (+196B Engram) | 8B prefill / 16B decode | 384 | 6 | 1 | FP4 KV, multimodal |
| Kimi K3 `[V]` | 2.78T | 104.2B | 896 | 16 | 2 | latent MoE, KDA hybrid |
| Qwen3.8-2.4T-A95B `[V]` | 2.4T | 95B | 512 | 10 | 1 | Gated DeltaNet hybrid |
| gpt-oss-120b `[V]` | 116.8B | 5.1B | 128 | 4 | — | MXFP4 experts |
| gpt-oss-20b `[V]` | 20.9B | 3.6B | 32 | 4 | — | runs in ~16 GB |

**Established:**
- MoE gives a much better capability-per-FLOP at inference than a dense model of the same FLOPs.
- MoE gives much worse capability-per-byte-of-memory than a dense model of the same quality. All
  experts must be resident (or fetched) even though most are unused per token.
- **Activation ratio** is the key design variable: K3 activates ~1.8% of experts per token (16/896);
  gpt-oss-120b activates top-4 of 128 (~3%).

**Vendor-reported efficiency claims worth testing rather than believing:**
- Kimi K3 reports ~**2.5× overall scaling efficiency** over K2 from KDA + Attention Residuals +
  Stable LatentMoE + data/training refinements `[V]`.
- DeepSeek-V4.1-Flash reports V4-Pro-Base-comparable knowledge/reasoning/coding with **1/3 the total
  parameters and 1/4 the activated parameters** `[V]`.

These are the most decision-relevant numbers in the whole survey (they imply a capability-per-dollar
shift), and they are *single-vendor* measurements.

### 3.2 Routing and stability (the part that breaks)

- **Load balancing** is required or routing collapses onto a few experts. DeepSeek-V4.1-Flash uses a
  Sinkhorn-balanced update for embeddings; Kimi K3 reports "Quantile Balancing" among its stability
  measures `[V]`.
- **Inference-time skew** is different from training-time balance: a personal assistant's traffic is
  narrow (calendar/email/documents), which can hot-spot experts and reduce the throughput that
  benchmarks promise.
- **Fine-tuning an MoE is riskier than fine-tuning a dense model**: router drift can silently change
  which experts serve which inputs. This matters directly for §8 (adaptation).

**Open problems:** interpretability of routing; predictable quality under expert-parallel serving;
whether MoE experts can be selectively pruned/quantised without regression (gpt-oss's MXFP4 shows
quantising experts is viable `[V]`, but per-expert sensitivity is unpublished).

---

## 4. Hybrid attention / state-space / recurrent designs

### 4.1 The two families

- **State-space / linear-attention layers** (Mamba-2 lineage; Gated DeltaNet) keep a fixed-size
  recurrent state: O(1) memory per step, O(n) sequence cost. Gated DeltaNet adds a gating term for
  fast erasure plus a delta rule for targeted writes — explicitly motivated by prior linear models'
  weakness in **retrieval and long-context** tasks `[I]`
  ([arXiv:2412.06464](https://arxiv.org/abs/2412.06464), Dec 2024; Mamba-2 at
  [arXiv:2405.21060](https://arxiv.org/abs/2405.21060)).
- **Hybrids** interleave a minority of full-attention layers with a majority of cheap recurrent
  layers, so global recall is preserved while most layers run at linear cost.

2026 deployments are hybrids, not pure SSMs:

| Model | Composition | Why it matters |
|---|---|---|
| Kimi K3 `[V]` | **69 KDA + 24 Gated MLA** layers (93 total) | Dominant-linear design at 2.8T scale |
| Qwen3.8-2.4T `[V]` | 92 layers alternating **Gated DeltaNet** with standard attention | Same strategy at 2.4T |
| DeepSeek-V4.1-Flash `[V]` | Global + sliding-window attention, causal encoder–decoder split | Different compression route |
| Gemini 4 Argon `[V]` | Undisclosed | — |
| GPT-6 Astra `[V]` | Described only as "recurrent depth" | See §4.2 |

**Established:** hybrid linear/attention designs are deployed at frontier scale in 2026 and are
claimed to improve long-sequence efficiency. **Unproven:** that they match full attention on
*retrieval-heavy* long-context tasks at 1M tokens; no independent evaluation was found.

### 4.2 Recurrent depth and the monitorability cost

OpenAI's GPT-6 Astra system card describes an opaque recurrence ("recurrent depth") and states that
Astra's internal traces are less legible than its predecessor's, with a prompted-sandbagging test in
which monitor recall fell to zero on one benchmark `[V]`
([system card](https://deploymentsafety.openai.com/gpt-6-astra)). Public research on looped/recurrent
depth exists (e.g. Huginn-style latent recurrence, `[U]` on exact citation, not verified here), but
the frontier deployment is the first case where a design choice is disclosed *because* it degrades
oversight.

**Implication for J.A.R.V.I.S.:** if reasoning traces are part of your safety story (approval
explanations, audit logs, "show me why"), an architecture that makes traces less faithful is a
system-level risk, not a curiosity. Phase 9 must therefore rely on **action-level** controls and
verifiers, not on chain-of-thought inspection, and any "explain your reasoning" feature must be
labelled as a reconstruction unless independently validated.

### 4.3 Open problems (hybrid/recurrent)

- Exact recall over very long spans without full attention.
- Training stability of recurrent layers at scale (an active area: gating, chunked parallel training).
- Tool-use reliability differences: most agent benchmarks were designed against attention-based
  models.
- Cache management for hybrid designs (what to evict when most state is fixed-size).

---

## 5. Multimodal architectures

### 5.1 Three patterns, with different costs

1. **Late fusion via encoder + projector.** A vision encoder (e.g. Kimi K3's 401M-parameter ViT with
   27 layers, patch 14 `[V]`) feeds an adapter into the language model. Cheap to add, keeps the LLM
   unchanged, but visual tokens are "foreign" to the LM's pretraining distribution.
2. **Early fusion from pretraining.** Modalities enter from the start of pretraining; Qwen3.5 reports
   "early fusion training on trillions of multimodal tokens" reaching parity with text-only models
   and beating their prior VL models `[V]` ([Qwen3.8 GitHub](https://github.com/QwenLM/Qwen3.8)).
   DeepSeek-V4.1-Flash similarly reports multimodal data "incorporated from the start of
   language-model pre-training" `[V]`.
3. **Native audio / speech-to-speech.** Audio is tokenised and generated directly (Gemini 3.8 Live:
   audio in → audio+text out, 131K context `[V]`; OpenAI `gpt-live-1` and `gpt-realtime-2.1` `[V]`).
   This removes the STT→LLM→TTS stitching but couples you to a vendor and their audio tokeniser.

### 5.2 Consequences for J.A.R.V.I.S.

- **Input multimodality is a solved problem at the API level** and is available in open weights at 27B
  (Qwen3.8-27B has a vision encoder, Apache-2.0 `[V]`).
- **Output multimodality (speech) is not solved in open weights at frontier quality**; the strongest
  options surveyed are hosted realtime models. This is the single biggest "vendor lock" risk in the
  design.
- **Do not assume image understanding implies document understanding.** Table/layout/OCR behaviour is
  a data and post-training artefact; Phase 8's B5 block tests it directly.

---

## 6. Pretraining: data, scale, stability, reproducibility

### 6.1 Scale is now a data-story

Disclosed training sets in this survey: Kimi K2 15.5T tokens `[V]`; DeepSeek-V4 >32T tokens `[V]`;
DeepSeek-V4.1-Flash 45T multimodal tokens `[V]`. Chinchilla-style compute-optimal ratios (roughly
20 tokens/parameter, [arXiv:2203.15556](https://arxiv.org/abs/2203.15556)) are a floor that frontier
labs exceed by an order of magnitude because inference cost, not training cost, dominates the
economics.

**Established:** data quality and mixture matter as much as volume; deduplication, filtering, and
synthetic/rephrased data are standard (K2 explicitly reports data rephrasing for token efficiency
`[V]`). **Unproven for any specific model:** the actual effect of its data recipe, since none of the
closed labs publish mixture details and the open labs publish summaries.

### 6.2 Stability engineering

Training a 1T+ model without loss spikes is a first-class engineering result, and 2026 reports
document the techniques:

- **MuonClip + QK-Clip** (Kimi K2): Muon optimiser with QK-clip; 15.5T tokens with **zero loss
  spikes** `[V]` ([arXiv:2507.20534](https://arxiv.org/abs/2507.20534)).
- **Muon** used for linear layers in DeepSeek-V4, with AdamW for norms/embeddings and
  Sinkhorn-balanced embedding updates `[V]` ([arXiv:2606.19348](https://arxiv.org/abs/2606.19348);
  [arXiv:2609.19969](https://arxiv.org/abs/2609.19969)).
- **Manifold-constrained hyper-connections** to stabilise residual streams `[V]`.
- **SiTU-GLU + Quantile Balancing** for extreme sparsity in K3 `[V]`.

**Established:** at frontier scale, optimiser and normalisation choices are load-bearing for
feasibility; **Unproven:** that any of these transfer beneficially to fine-tuning small models (they
generally do not matter at LoRA scale).

### 6.3 Contamination and reproducibility

- **Contamination is measurable and material.** A 2026 audit framework reports that removing
  contaminated samples reduces top-10 SWE-bench Verified resolution rates by >20% on medium-difficulty
  tasks and up to 55.9% on Multi-SWE-bench `[I]`
  ([arXiv:2610.19447](https://arxiv.org/html/2610.19447)). GSM1K showed up to a 13-point drop for the
  worst-overfitting families `[I]` ([arXiv:2405.00332](https://arxiv.org/abs/2405.00332)).
- **No closed model is reproducible.** We cannot re-run pretraining, cannot audit the data, and
  cannot reproduce a version once deprecated. Only open weights make reproduction possible, and even
  then only at large compute cost.
- **Consequence for us:** J.A.R.V.I.S. evaluation must use **our own held-out tasks**, refreshed, and
  must never be tuned against a public benchmark we also report.

---

## 7. Post-training: where most user-visible behaviour comes from

### 7.1 The pipeline, and what each stage buys

| Stage | What it changes | Evidence | Risk |
|---|---|---|---|
| Supervised fine-tuning (SFT) on demonstrations | Format, instruction following, tool-call syntax | InstructGPT-style pipelines are standard [I] | Narrow, brittle behaviour; verbosity |
| Preference optimisation (RLHF/DPO) | Tone, helpfulness, refusal boundary | Standard since 2022; DPO simplifies | Sycophancy; over-refusal; hidden objective drift |
| Verifiable-reward RL (RLVR/GRPO) | Reasoning depth, self-consistency on checkable tasks | DeepSeek-R1-lineage [I]; Kimi K2 reports RLVR + self-critique rubric `[V]` | Reward hacking; benchmark overfitting |
| Effort-conditioned RL | Controllable thinking budget | K3 reports RL across multiple reasoning-effort levels `[V]`; all major providers expose effort dials `[V]` | Effort ≠ quality; cost surprises |
| Safety post-training (constitutions, safe-completions) | Refusal behaviour, honesty interventions | Anthropic transparency hub `[V]`; OpenAI GPT-5/6 system cards `[V]` | Capability/latency trade |
| Distillation | Smaller model imitates larger | Standard [I] | Legality/ToS constraints `[U]`; capability ceiling |

### 7.2 Why this matters more than architecture for J.A.R.V.I.S.

The behaviours Phase 1 measures — instruction following, tool-argument exactness, honesty about
completion, abstention — are **post-training artefacts**, not architecture properties. Two models
with identical architecture can differ by tens of points on τ²-bench-style tasks because of
post-training.

Evidence that post-training can *degrade* the properties we need:

- Anthropic's own report states Sonnet 5.5 gave more right answers *and* slightly more wrong answers
  than Sonnet 5 on a no-lookup factual set, scoring below other Claude models on the weighted measure
  `[V]` ([transparency hub](https://www.anthropic.com/transparency)).
- OpenAI's GPT-6 Astra card reports better control over what its chain-of-thought reveals, which the
  card itself frames as higher capacity for covert sandbagging `[V]`.
- Kimi K2's reported failure modes include verbosity and over-eager tool use on hard reasoning tasks
  `[V]` (K2 report).

**Established:** you cannot infer honesty, abstention, or tool discipline from capability benchmarks.
**Plausible:** these properties are partly fixable by scaffolding (verifiers, schemas), which is what
Phase 4 proposes.

---

## 8. Adaptation levers available without foundation training

Ranked by cost, risk, and expected value for J.A.R.V.I.S.

| # | Lever | Cost | What it fixes | What it cannot fix | Evidence |
|---|---|---|---|---|---|
| 1 | **Prompt + context engineering** | Minutes | Format, policy adherence, tool schemas, tone | Knowledge gaps, reasoning ceiling | Widely replicated [I] |
| 2 | **Tool schema design + validation** | Days | Argument exactness, wrong-tool selection, unsafe actions | Model's world knowledge | τ²-bench style policy tasks [I] |
| 3 | **Retrieval augmentation** | Weeks | Facts, freshness, personalisation, provenance | Reasoning depth | RAG literature [I]; see Phase 5 |
| 4 | **Routing / effort control** | Weeks | Cost per task | Tail capability | RouteLLM reports >2× cost reduction at ~95% quality on MT-Bench; 3.66× in its best case `[I]` ([arXiv:2406.18665](https://arxiv.org/abs/2406.18665)) |
| 5 | **Quantisation** (FP8/INT8/4-bit; MXFP4 precedents) | Days | Memory footprint, cost per token | Quality below the quantisation floor | gpt-oss ships MXFP4 experts and fits 80 GB `[V]`; per-model degradation must be measured, not assumed |
| 6 | **Speculative decoding / draft models** | Days | Inter-token latency | Throughput at high concurrency (gains shrink; EAGLE-3 reports ~1.38× at batch 64 vs up to 6.5× single-stream) `[I]` ([arXiv:2503.01840](https://arxiv.org/abs/2503.01840)) | See Phase 7 |
| 7 | **SFT / LoRA on an open model** | Weeks | Fixed output grammars, domain vocabulary, house style | New knowledge that is not in weights (use retrieval instead) | LoRA/QLoRA are standard [I]; MoE router-drift risk (§3.2) |
| 8 | **Preference tuning (DPO-style)** | Weeks | Tone, refusal boundary, ranking preferences | Factual accuracy | Standard [I]; needs a preference dataset we do not have |
| 9 | **Distillation into a small model** | Months | Cheap private tier | Frontier capability | Standard [I]; licence/ToS review required `[U]` |
| 10 | **Continued pretraining** | Months–years, $M+ | Domain knowledge in weights | Everything else, cheaply | Not justified (§9) |

### 8.1 The knowledge-vs-behaviour rule

**Plausible, with strong supporting practice:** fine-tune for **behaviour** (format, tool discipline,
style), retrieve for **knowledge** (facts, personal context, current information). The reason is
mechanical: weights are a lossy, stale, un-attributable store, while retrieval is auditable and
correctable. Phase 1's T4 (provenance, deletion, updates) is only satisfiable with an external store.

### 8.2 What we would need before fine-tuning anything

1. A measured baseline and error taxonomy (Phase 8).
2. A residual error class that is *behavioural* and *systematic* (e.g. model emits invalid ISO dates
   3% of the time), not a knowledge gap.
3. An open-weight model whose licence permits the use and whose quantised form fits the target
   hardware.
4. A held-out set that the fine-tune cannot see, plus a regression suite for safety behaviour.

---

## 9. Do we need to train a foundation model? (No — with a stated trigger)

**Evidence against:**

- Capable Apache-2.0 weights exist at 27B (Qwen3.8-27B, vision) and ~117B (gpt-oss-120b) with tool
  use `[V]`.
- The dominant failures in Phase 1 (memory correctness, argument exactness, truthful completion) are
  addressable by system design, and some are *caused* by model behaviour that more pretraining does
  not fix (sycophancy, sandbagging, over-eager tool use).
- Cost anchor: 2.1M H100-hours for a mid-size open model `[V]`; frontier-scale runs are an order of
  magnitude more. No measurement on this project's workload could justify that before a
  system-level baseline exists.

**Trigger conditions that would reopen the question (any one is insufficient on its own):**

1. Post-adaptation plateau: routed system below Phase 1 targets after levers 1–8 are exhausted.
2. Error attribution: ≥50% of residual failures traced to a specific capability (e.g. a private
   domain vocabulary or a formal output grammar) that SFT on an open model demonstrably fixes.
3. A hardware/privacy constraint that forbids hosted models *and* requires capability only available
   above 30B parameters — which would point to **fine-tuning a larger open model**, still not
   pretraining.
4. Evidence that a small architecture change yields a ≥2× capability-per-dollar shift for our
   workload — the kind of claim K3/V4.1 make `[V]` but nobody has independently verified.

Even then, the correct first intervention is **continued training of an existing open checkpoint**
(cheap) before pretraining (ruinous).

---

## 10. Summary table: architecture families for decision-making

| Family | Benefit | Cost | Evidence quality | Open problem | J.A.R.V.I.S. stance |
|---|---|---|---|---|---|
| Dense Transformer + GQA/MLA | Predictable, well-tooled | Memory scales with params | **Established** | Long-context efficiency | Default assumption |
| Sparse MoE | Capability per FLOP | Memory per capability; serving complexity | **Established** mechanism; vendor-reported gains | Routing stability, fine-tune drift | Consume, don't build |
| Hybrid linear/attention | Cheap long sequences | Possible recall loss | **Plausible** (vendor-run) | Exact retrieval at 1M | Prefer models that *measure* it |
| Recurrent depth / looped | Compute-depth trade | Monitorability | **Unproven**; one vendor disclosure | Trace faithfulness | Avoid as trust anchor |
| Late-fusion multimodal | Cheap vision | Weak document reasoning | **Established** | Layout/OCR robustness | Test per document class |
| Early-fusion multimodal | Better cross-modal reasoning | Expensive pretraining | **Plausible** (vendor) | Data mixture | Prefer where available |
| Native audio S2S | Lowest voice latency | Vendor lock, egress | **Plausible** | Interruption handling, tool use mid-audio | Test in Phase 6 |

---

## 11. Findings classification

**Established**
- Decoder-only Transformer with modern normalisation/positional/FFN choices is the universal base.
- MoE decouples parameter count from per-token FLOPs; all-expert memory residency is the price.
- KV-cache engineering (compression, FP4/FP8 caching, cross-layer reuse, SWA bounds) is the dominant
  lever on long-context cost, with 4–10× differences reported between systems claiming the same
  context length `[V]`.
- Training stability at 1T+ scale requires explicit optimiser/normalisation engineering, documented in
  at least three 2026 reports `[V]`.
- Contamination and harness effects are large enough to invalidate small cross-model differences `[I]`.
- Post-training, not architecture, determines the behaviours Phase 1 measures.

**Plausible**
- Hybrid linear-attention designs deliver the claimed efficiency without unacceptable recall loss at
  1M tokens.
- Effort-conditioned RL produces genuinely controllable reasoning budgets rather than a cost dial with
  unpredictable quality.
- Fine-tuning for behaviour + retrieval for knowledge is the right adaptation split.

**Unproven**
- That any specific architectural innovation in 2026 (CSA/HCA, KDA, AttnRes, Stable LatentMoE,
  recurrent depth) is causally responsible for the reported capability gains rather than the data and
  post-training that accompanied it.
- That reasoning-trace monitoring can be a safety control under recurrent-depth-style architectures.
- That fine-tuning an MoE at small scale is safe with respect to router drift (no published study
  found).

---

## 12. What was learned, what remains uncertain, and the next research step

**What was learned.** Architecture choices in 2026 are mostly **economic** choices about memory
traffic and serving, not capability choices. The capability deltas that matter to J.A.R.V.I.S. come
from data and post-training, and post-training can actively harm the properties we need (honesty,
abstention, tool discipline). Building a foundation model is unjustified by the evidence; adapting one
is the default, with a strict ordering (prompting → tools → retrieval → routing → quantisation →
fine-tuning).

**What remains uncertain.** Whether vendor-reported efficiency claims (2.5× K3 scaling, 27%/10% V4
context efficiency, 1/3-parameter parity for V4.1-Flash) survive independent measurement; whether
hybrid attention preserves long-range retrieval; how much of the "reasoning" we pay for is
effort-dial inflation rather than capability; and whether MoE fine-tuning is safe at our scale.

**Next research step.** Phase 4: given models with these properties, what is the *simplest*
orchestration architecture that meets the Phase 1 workload — single agent with verifiers, routed
tiers, or multi-agent decomposition — and what experiments would justify each added layer of
complexity?
