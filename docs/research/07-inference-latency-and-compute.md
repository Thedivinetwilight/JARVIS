# Phase 7 — Inference, Latency, and Compute

- **Status:** published (v0.2, open for review)
- **Published:** 2026-11-01
- **Sources checked:** 2026-11-01
- **Related:** [Index](README.md) · [Phase 1 — envelopes](01-jarvis-workload-and-requirements.md) §6 · [Phase 3 — KV economics](03-model-architectures-and-training.md) §2.2 · Phase 6 (voice path)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · **`[V]` vendor** · **`[I]` independent** · **`[U]` unverified**

---

## 0. The rule for this phase

> **Do not assume an optimisation helps. Measure it on the workload that matters, and report the
> settings that produced the number.**

This document therefore contains: (a) the physics that constrains any inference system, (b) the
techniques with their measured or reported effects, (c) a cost model with worked examples and
explicit assumptions, and (d) a measurement protocol precise enough to reproduce.

Everything numeric is stamped with a date. Prices and GPU specifications move.

---

## 1. Metrics, defined once

| Metric | Definition | Why it matters for J.A.R.V.I.S. |
|---|---|---|
| **TTFT** | request sent → first output token/frame available | Perceived responsiveness of every interactive turn |
| **ITL / TPOT** | inter-token latency; time per output token | Reading speed; whether streaming feels smooth |
| **End-to-end latency** | request → verified terminal state | The only number the user experiences for tasks |
| **Tail latency (p95/p99)** | upper percentiles | Averages hide the turns users remember |
| **Goodput** | successful requests per second under an SLO | Capacity planning that accounts for failures |
| **Throughput** | tokens/second aggregate | Cost per token, not user experience |
| **Cost per successful task** | total cost ÷ verified successes | Phase 1 §5.6; the only fair comparison (Kapoor et al. `[I]`) |
| **Energy** | Wh per task | Local/battery deployment (Q8) |

Two normalisation traps to avoid:

- **Do not compare tokens/sec across different prompt lengths.** Prefill and decode have different
  bottlenecks; report TTFT and ITL separately.
- **Do not compare cost per token across providers without accounting for reasoning tokens.** A
  "cheaper" model at max effort can cost more than a premium model at default effort (Phase 4 §1.1).

---

## 2. The physics: decode is memory-bandwidth bound

**Established.** In autoregressive decode, each generated token requires reading the model's active
weights (and the KV cache) from memory. Arithmetic intensity is low, so throughput is limited by
**memory bandwidth**, not FLOPs. The standard first-order estimate for a single sequence:

```
tokens/second (upper bound) ≈ memory_bandwidth_bytes_per_s
                              ────────────────────────────────
                              bytes_of_active_weights + KV_bytes_per_token × context_length
```

Worked estimates using 2026 hardware figures (B200: 192 GB HBM3e at ~8 TB/s; B300: 288 GB at ~8 TB/s,
15 PFLOPS dense NVFP4 `[U]`, vendor-review sources consistent across two independent write-ups):

| Model | Active weights | Bytes/token (weights) | Theoretical single-stream tok/s on 8 TB/s |
|---|---|---|---|
| gpt-oss-120b (5.1B active, MXFP4 ≈4.25 bit) | 5.1B | ≈2.7 GB | ≈2,900 (ignoring KV and real-world efficiency) |
| A 104B-active model in BF16 (Kimi K3-class) | 104B | ≈208 GB | ≈38 |
| A 49B-active model in FP8 (DeepSeek-V4-Pro-class) | 49B | ≈49 GB | ≈163 |

**Interpretation, not prediction:** the same bandwidth buys two orders of magnitude difference in
single-stream speed depending on activated parameters and precision. This is why:

- small-active MoE models feel fast (and why gpt-oss-20b "runs on a laptop");
- 100B-active models need batching to be economic even on modern accelerators;
- people quantise (fewer bytes ⇒ more tok/s), use speculative decoding (more tokens per weight read),
  and compress KV caches (less per-token overhead).

Real systems achieve a fraction of these bounds; the estimate is for ordering decisions only.

**Unproven:** that any published tokens/sec figure transfers to your hardware, batch mix, and prompt
length distribution.

---

## 3. Batching, scheduling, and where tail latency comes from

| Technique | What it does | Established effect | Cost/complication |
|---|---|---|---|
| **Continuous (iteration-level) batching** | Admits new requests every decode step | Large throughput gain over static batching `[I]` (Orca, OSDI 2022) | Requires careful scheduling |
| **Paged KV cache** | Non-contiguous KV blocks, sharing, copy-on-write | Removes fragmentation; enables prefix sharing `[I]` ([vLLM, arXiv:2309.06180](https://arxiv.org/abs/2309.06180)) | Kernel ~20–26% slower per-op, more than repaid by batch size (reported in the paper's own analysis) |
| **Chunked prefill** | Splits long prompts into chunks interleaved with decode | Reduces decode stalls, improves tail ITL `[I]` (Sarathi-Serve, `[U]` exact number) | Slightly higher TTFT for the long request |
| **Prefill/decode disaggregation** | Separate pools for prompt processing and generation | Better SLO adherence under mixed load `[I]` (DistServe/Splitwise/Mooncake line of work `[U]` for exact gains) | Two clusters, KV transfer bandwidth |
| **Priority/fair scheduling** | Prevents one long job from starving chat | Reduces p99 for interactive traffic | Requires a real scheduler |
| **Admission control / queue limits** | Rejects rather than queues forever | Tail latency bounded | A product decision (Phase 1: progress updates) |

**Established cause of tail latency in J.A.R.V.I.S. specifically:** long agent runs (T3) mixing with
interactive turns in the same pool. If a 40-step plan occupies the same capacity as voice turns, the
voice p99 collapses. **Recommendation:** separate capacity pools for interactive and batch/long-task
traffic, or hard concurrency caps per class.

---

## 4. Caching: the highest-leverage cost lever

### 4.1 Types

| Cache | What is reused | Where it helps | Caveat |
|---|---|---|---|
| **Prefix / KV cache** | KV for an identical prompt prefix | Stable system prompts, tool schemas, long documents | Only helps if the prefix is byte-identical and ordered stably |
| **Provider prompt caching** | Billed at a discount for cached input | Same, at the API level | Cache lifetime and write cost vary by provider |
| **Radix/prefix tree (SGLang-style)** | Shared prefixes across requests | Serving many sessions with a common preamble | Memory pressure under diverse prompts |
| **Semantic/answer cache** | Whole answers for similar queries | Repetitive personal queries | **Dangerous** for personalised or time-sensitive answers |
| **Retrieval cache** | Embeddings and search results | Repeated document questions | Must be invalidated on document change |
| **Tool-result cache** | Idempotent reads (calendar, weather) | Reduces tool latency | Never cache writes |

### 4.2 The economics are large and published

OpenAI's cached-input price is **10× cheaper** than standard input across the GPT-6 family
(e.g. Azure-tier Astra $1.00 vs $10.00; Sol $0.10 vs $2.00) `[V]`
([pricing](https://developers.openai.com/api/docs/pricing)). Anthropic publishes cache pricing
separately per model `[V]` ([models overview](https://platform.claude.com/docs/en/models/overview)).

**Design consequence:** structure prompts as `[immutable core: identity, policy, tool schemas] +
[cached documents] + [volatile turn]`. Do not interleave volatile content into the core; doing so
destroys the cache and multiplies cost.

### 4.3 What not to do

- **Do not semantically cache answers** for anything personal, time-sensitive, or consequential. The
  failure mode (serving a stale answer about the user's own data) is exactly T4's contradiction
  failure.
- **Do not assume cache hits.** Measure hit rate; log cache-read tokens as a first-class metric.

---

## 5. Quantisation

| Scheme | Bits | Where it applies | Evidence | Risk |
|---|---|---|---|---|
| BF16/FP16 | 16 | Reference | Standard | Memory 2× vs FP8 |
| FP8 (E4M3) | 8 | Weights + activations; also KV cache | Widely deployed; DeepSeek-V4.1-Flash uses **FP4 KV caching** `[V]` | Quality loss task-dependent; measure |
| NVFP4 / MXFP4 | 4 | Weights (and KV in some designs) | gpt-oss ships MoE weights in **MXFP4** (~4.25 bits) enabling 120b in one 80 GB GPU and 20b in ~16 GB `[V]` | Larger risk on long-tail knowledge; per-model measurement required |
| GPTQ / AWQ | 4 (weight-only) | Local serving | Established `[I]` ([AWQ arXiv:2306.00978](https://arxiv.org/abs/2306.00978), [GPTQ arXiv:2210.17323](https://arxiv.org/abs/2210.17323)) | Degrades instruction following if over-aggressive |
| QLoRA-style | 4-bit base for fine-tuning | Adaptation | Established `[I]` (`[U]` exact citation) | Training instability at scale |

**Established:** 4-bit weight quantisation of MoE experts is production-viable (gpt-oss is shipped that
way by its vendor) `[V]`. **Unproven:** that any given 4-bit quantisation preserves the specific
behaviours J.A.R.V.I.S. needs (argument exactness, abstention, long-context recall).

**Mandatory practice:** every quantised variant is evaluated on the **same** Phase 8 suite as its
BF16 reference, and reported with the delta. A quantisation that improves latency but shifts the
false-success rate is a regression, not an optimisation.

---

## 6. Speculative decoding and draft models

| Method | Mechanism | Reported effect | When it does **not** help |
|---|---|---|---|
| Draft-model speculation | Small model proposes, target verifies | ~2–3× for T5-class pairs `[I]` ([arXiv:2211.17192](https://arxiv.org/abs/2211.17192)) | Draft too slow or low acceptance |
| EAGLE-3 | Trained feature-level draft | Up to **6.5×** single-stream; **1.38×** throughput at batch 64 (SGLang) `[I]` ([arXiv:2503.01840](https://arxiv.org/abs/2503.01840)) | High concurrency (gain shrinks) |
| n-gram / prompt lookup | Copy spans from the prompt | Up to ~2.8× on summarisation/code-edit tasks `[U]` | Creative generation with little copying |
| Multi-token prediction (MTP) heads | Model predicts several tokens | Used in DeepSeek/Kimi architectures `[V]` | Requires model support |

**Established:** output is lossless (verification preserves the target distribution). **Established
caveat:** gains are largest at low batch sizes and shrink with concurrency, so a personal assistant
(single user, low concurrency) is the *best case*, while a busy server is not.

---

## 7. Efficient attention and KV-cache management

Covered mechanically in Phase 3 §2.2. The serving consequences:

| Technique | Effect | Caution |
|---|---|---|
| MLA / GQA | Smaller KV per token | Architecture-dependent; not a free post-hoc change |
| Sliding-window attention | Bounds KV growth | Loses exact recall beyond the window |
| KV quantisation (FP8/FP4) | 2–4× less KV memory/bandwidth | DeepSeek reports FP4 KV with quality claims `[V]`; verify on our tasks |
| KV eviction/compression (H2O-style, StreamingLLM-style) | Constant memory for long sessions | Can silently drop the fact you needed |
| Prefix sharing | Reuses KV across sessions/documents | Requires stable prompt structure |

**Plausible:** for a single user, KV-cache memory is a secondary constraint compared with weight
memory; for multi-hour sessions with large documents, it becomes primary. Measure both.

---

## 8. Hosted vs. local inference

| Dimension | Hosted API | Local / self-hosted |
|---|---|---|
| Capability ceiling | Highest (frontier closed models) | Lower (best open weights) |
| Marginal cost at low volume | Near zero | High (hardware idle) |
| Marginal cost at high volume | Linear in tokens | Falls with utilisation |
| Latency floor | Network RTT + provider queue | No WAN; but your own scheduling |
| Tail latency | Provider-dependent, opaque | You control it (or you don't) |
| Privacy | Egress; terms govern | Full control |
| Availability | Provider outages | Your hardware failures |
| Version stability | Model IDs can be deprecated | Frozen weights |
| Ops burden | None | Drivers, frameworks, upgrades, monitoring |

**Recommendation:** use hosted models for capability and elasticity; run local models for privacy,
offline, ASR/TTS, routing, and as a fallback when a provider fails. The economic break-even depends
entirely on utilisation; §10 gives the arithmetic.

---

## 9. Hardware: inference vs training, and what's realistic

| GPU class | Memory | Bandwidth | Dense NVFP4 (vendor) | Notes |
|---|---|---|---|---|
| H100 SXM | 80 GB HBM3 | ~3.35 TB/s | — (FP8 class) | Common rental baseline |
| B200 | 192 GB HBM3e | ~8 TB/s | 10 PFLOPS | Fits gpt-oss-120b (~61 GiB) with headroom |
| B300 (Blackwell Ultra) | 288 GB HBM3e | ~8 TB/s | 15 PFLOPS; 1,400 W | More memory, not more bandwidth |

Sources: vendor review/aggregator tables `[U]` (two independent write-ups agree on memory/bandwidth;
re-verify against NVIDIA datasheets before purchase).

**Rules of thumb:**

- **Inference memory ≈ weights + KV + activations.** For a 4-bit 117B model: ~61 GiB weights ⇒ 80 GB
  card with little headroom for long-context KV. For a 27B at 4-bit: ~14 GiB ⇒ workstation-class.
- **Training needs 4–8× the memory** and interconnect (NVLink/cluster networking); inference does not.
- **A personal assistant's training needs are ~zero** (Phase 3 §9). Buy inference capacity, not
  training capacity, and only after E-7.x measurements.

---

## 10. Cost model, with worked examples

### 10.1 Formula

```
cost_per_task = Σ over calls (input_tokens × in_price + output_tokens × out_price + cached_reads × cache_price)
                + tool/infra cost
                + amortised local hardware cost
cost_per_success = cost_per_task_total / verified_successes     # failures are charged
```

### 10.2 Hosted examples (published prices, 2026-11-01)

Assumptions: 1,500 input + 300 output tokens per chat turn; no cache; standard tier; one attempt.

| Scenario | Model | Arithmetic | Cost |
|---|---|---|---|
| Chat turn | gpt-6.1-sol ($2/$10) | 1500×2/1e6 + 300×10/1e6 | **$0.0060** |
| Chat turn (80% of a 1,000-token core cached) | gpt-6.1-sol, cached $0.10 | 1000×0.10/1e6 + 500×2/1e6 + 300×10/1e6 | **$0.0041** |
| Chat turn | gpt-6-astra ($10/$50) | 1500×10/1e6 + 300×50/1e6 | **$0.0300** |
| Chat turn | gpt-6-luna ($0.10/$0.50) | 1500×0.1/1e6 + 300×0.5/1e6 | **$0.0003** |
| Research synthesis | gpt-6.1-sol (40K in, 6K out) | 40000×2/1e6 + 6000×10/1e6 | **$0.1400** |
| Research synthesis | gpt-6-astra | 40000×10/1e6 + 6000×50/1e6 | **$0.7000** |
| Long plan (20 steps, 200K cumulative in, 20K out) | gpt-6.1-sol | 200000×2/1e6 + 20000×10/1e6 | **$0.6000** |
| Long plan | gpt-6-astra | 200000×10/1e6 + 20000×50/1e6 | **$3.0000** |
| Voice | gpt-live-1 | $0.05/min × 10 min/day | **$0.50/day ≈ $15/month** |

Prices from [OpenAI pricing](https://developers.openai.com/api/docs/pricing) `[V]`; Anthropic
equivalents (Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5 from $0.10/$0.50) from
[models overview](https://platform.claude.com/docs/en/models/overview) `[V]`; Gemini 4 Argon
$2/$10 introductory (until 2026-11-30, then $4/$20) `[V]`.

**The load-bearing observation:** at these prices, the *difference* between a mid-tier and premium
model is $0.02 per chat turn and $0.56 per research task. Phase 1's provisional ceiling
(≤$0.50 per research task) is therefore satisfiable with a premium model *if* tasks are attempted
once; it is breached by retries and multi-agent fan-out (15× tokens, Phase 4 §5.1). **Cost control is
a control-flow problem, not a model-choice problem.**

### 10.3 Local break-even (illustrative, not a quote)

Assumptions (all `[U]`, must be replaced with real rentals): an 80 GB-class GPU rented at ~$2–3/hour;
gpt-oss-120b served with vLLM; sustained aggregate output of 800–2,000 tok/s at moderate batch.

```
cost per 1M output tokens = hours × hourly_rate / (tokens_per_second × 3600 / 1e6)
                          = (1e6 / (1000 × 3600)) × $2.50      ≈ $0.69   (at full utilisation)
```

At 30% utilisation the same token costs ~$2.30; at 10%, ~$6.90 — still below premium hosted output
prices, but only for tasks the hosted model does not do better. **Conclusion:** self-hosting is
economically attractive only at high, steady utilisation; for a single personal user, it is justified
by privacy and offline capability, not by cost.

### 10.4 What is missing from every cost estimate

- **Failure cost.** Phase 1's `cost_per_success` charges failures. A 70%-success task at $0.14 per
  attempt costs $0.20 per success.
- **Reasoning-token variance.** Effort settings can multiply output tokens by 5–20× (Phase 4 §1.1).
- **Cache hit rate.** Unmeasured cache hits make 10× pricing differences invisible.
- **Human time.** Approval prompts and error recovery cost the user minutes; this is a real cost we
  do not yet model.

---

## 11. Measurement protocol (what to record so results are reproducible)

For every benchmark run, record:

1. **Model identity:** exact ID/version/date (e.g. `gpt-6.1-sol`, `claude-opus-5-5`,
   `gemini-4-argon`, open-weight commit hash).
2. **Serving stack:** provider and region, or vLLM/SGLang version, CUDA/driver, quantisation format.
3. **Hardware:** GPU model, count, memory, interconnect.
4. **Workload:** prompt token distribution, output token limits, concurrency (open loop vs closed),
   arrival process, think-time.
5. **Settings:** temperature/top-p, max tokens, reasoning effort, tool schemas, cache on/off.
6. **Environment:** client location, network path, time of day.
7. **Statistics:** ≥100 requests per cell; report p50/p95/p99 and the number of failures; separate
   cold and warm runs; state warm-up policy.
8. **Cost:** tokens by type (input/cached/output/reasoning), price sheet version, and total.

**Established pitfalls:** measuring a single request and calling it throughput; ignoring warm-up;
comparing providers at different times of congestion; comparing different prompt lengths; trusting a
provider's status page instead of measuring; forgetting that agent tasks issue many sequential calls
(so per-call latency compounds).

---

## 12. Experiment plan (Phase 7)

| ID | Experiment | Metrics | Decision it informs |
|---|---|---|---|
| E-7.1 | Tier bake-off: Luna/Terra/Sol/Astra + Haiku/Sonnet/Opus + Flash/Argon on the B-block suite | TTFT, ITL, end-to-end, cost/success | Default workhorse and escalation tier |
| E-7.2 | Prefix-cache impact: stable core prompt vs interleaved | cache hit %, cost, TTFT | Prompt architecture (§4.2) |
| E-7.3 | Effort sweep per family | tokens, latency, success per dollar | Effort routing rules |
| E-7.4 | Concurrency sweep (1/4/16/64) locally on gpt-oss-120b and Qwen3.8-27B | ITL p50/p95, goodput, GPU memory | Local capacity, separate-pool design |
| E-7.5 | Quantisation delta: FP8/BF16 vs 4-bit on the suite | success, false-success, latency | Whether local 4-bit is acceptable |
| E-7.6 | Speculative decoding at batch 1 and 32 | ITL, acceptance rate | Whether to enable for the personal path |
| E-7.7 | Fault/outage drill: provider 5xx injection | fallback success, added latency | Local fallback design |
| E-7.8 | 20-minute session drift (hosted and local) | TTFT/ITL by minute; context growth | History trimming policy |

---

## 13. Findings classification

**Established**
- Decode is memory-bandwidth bound; active-parameter count and precision dominate single-stream speed.
- Paged KV caches, continuous batching, and prefix reuse are mature, widely-deployed wins `[I]`.
- Provider prompt caching changes cost by ~10× on input tokens for at least one major provider `[V]`.
- Speculative decoding is lossless and yields the largest gains at low concurrency `[I]`.
- 4-bit quantised MoE weights are shipped in production by a major vendor `[V]`.

**Plausible**
- A personal, low-concurrency workload is the best case for speculative decoding and the worst case
  for self-hosting economics.
- Separating interactive and long-task capacity is necessary to protect voice/text tail latency.
- Cost control is achieved mainly by controlling attempts and tokens, not by picking a cheaper model.

**Unproven**
- Any specific tokens/sec figure for a given model on given hardware, without measurement.
- That 4-bit quantisation at 27B preserves argument exactness and abstention for J.A.R.V.I.S. tasks.
- That local inference meets Phase 1's latency envelope on the user's hardware (Q8).
- That the reported KV-compression ratios (890 bytes/token, 27%/10% of predecessor FLOPs/KV) hold
  outside the publishing lab.

---

## 14. What was learned, what remains uncertain, and the next research step

**What was learned.** Latency and cost are governed by three things, in order: **how many tokens you
send and generate**, **how many attempts you make**, and **how the tokens are batched/cached** — with
model choice a distant fourth for a single-user workload. The physics explain the market: small-active
MoE models are fast and cheap; 100B-active models are slow without batching; quantisation and
speculative decoding are real but must be measured per model. Self-hosting is a privacy decision, not
a cost decision, at personal volume.

**What remains uncertain.** The true cache-hit rate under a realistic personal workload; whether the
cascade's second attempt erases the router's savings; how much local capacity is needed to keep voice
p95 under 1.4 s while a long task runs; and whether the published efficiency claims from open-weight
labs survive reproduction.

**Next research step.** Phase 8: define the reproducible harness that would produce all of these
numbers — baselines, task sets, scoring, statistics, versioning, rollback, and the definition of
"self-improvement" as proposing and testing changes against a frozen baseline rather than deploying
them.
