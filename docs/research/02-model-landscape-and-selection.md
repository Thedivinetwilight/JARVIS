# Phase 2 — Model Landscape and Selection

- **Status:** published (v0.3, open for review)
- **Published:** 2026-11-01
- **Sources checked:** 2026-11-01
- **Related:** [Index](README.md) · [Phase 1 — Workload](01-jarvis-workload-and-requirements.md) · Phase 3, 4, 7
- **Evidence labels:** **Established** · **Plausible** · **Unproven**
- **Provenance tags:** `[V]` vendor-reported · `[I]` independent · `[U]` unverified, re-check before use

> **This document expires quickly.** Model names, prices, context limits, and licences change
> monthly. Every figure below is stamped with a check date. Re-verify §3 and §4 before any
> procurement or architecture decision.

---

## 0. Purpose and non-goals

**Purpose:** determine which models *could* serve the seven task families defined in Phase 1, on what
terms, with what disclosed architecture, and with what evidence — so that Phase 10 can specify
experiments instead of opinions.

**Non-goals:**

- Choosing a winner from marketing claims. No vendor self-report is treated as sufficient.
- Recommending a new foundation model. Nothing found in this survey shows existing models plus
  system-level improvements are inadequate; §7.4 states what evidence would change that.
- Predicting J.A.R.V.I.S. performance from benchmark scores. Every cross-model number in §6 is an
  instrument reading, not a forecast.

---

## 1. Method and evidence rules

1. **Primary sources first:** vendor model cards, system cards, official API documentation, pricing
   pages, arXiv technical reports, and official Hugging Face repositories.
2. **Verify liveness, not just existence.** For open weights we queried the official Hugging Face
   organisation listing rather than trusting an article that says a release happened
   ([meta-llama](https://huggingface.co/api/models?author=meta-llama&sort=createdAt&direction=-1&limit=15),
   [openai](https://huggingface.co/api/models?author=openai&sort=createdAt&direction=-1&limit=15)).
3. **Separate vendor from independent.** A model card is `[V]` even when it reports competitor
   numbers. Independent sources are labelled `[I]`.
4. **Record contradictions.** When sources disagree, both are recorded and the contradiction is
   resolved (or left open) explicitly.
5. **Recheck before currency claims.** Anything about "latest" is valid only as of 2026-11-01.

### 1.1 Source-conflict case study: the "Llama 4.5" problem

This is worth documenting because it is the failure mode this research must avoid.

| Source | Claim | Date | Tag |
|---|---|---|---|
| Vendor-adjacent blog | "Meta shipped Llama 4.5 on 11 September 2026: 8B, 90B, 405B-MoE, 2M context" | 2026-09-11 | `[U]` |
| Another blog | "Llama 4.5 shipped early June 2026, incremental refresh" | 2026-06-23 | `[U]` |
| Analysis blog | "There is no Llama 4.5 and no Llama 5; Maverick is still the flagship; Behemoth never shipped" | 2026-08-18 | `[U]` |
| **Official Hugging Face org listing** | Newest `meta-llama` repositories are Llama 4 Scout/Maverick, Llama Guard 4, Prompt Guard 2 — all created April 2025. No Llama 4.5, no Llama 5. | queried 2026-11-01 | **`[I]` primary** |

**Resolution:** the Llama 4.5 claims are **not supported** by the official distribution channel as of
2026-11-01. Two blogs described a release that does not appear in the vendor's own model
repository. This is not proof that no announcement exists somewhere, but it is sufficient to refuse
to plan on it. **Rule adopted:** an open-weight release is real when the weights are downloadable
from the canonical org, not when an article says so.

The same rule was applied positively to Qwen3.8 and gpt-oss, and negatively to "gpt-oss-2"
(the `openai` org listing shows no successor to gpt-oss-120b/20b).

---

## 2. Access methods and their consequences

| Access method | Examples (2026-11-01) | Consequences for J.A.R.V.I.S. |
|---|---|---|
| Hosted API, closed weights | GPT-6 Astra / 6.1 Sol / Terra / Luna, Claude Fable 5.1 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5, Gemini 4 Argon / 3.8 Flash | Fastest capability access, no hardware; per-token cost, provider availability, data-egress and jurisdiction questions; behaviour can change under you unless you pin model IDs |
| Hosted API, open weights (third-party serving) | DeepSeek V4/V4.1, Kimi K3, Qwen3.8 served by various providers | Portable across providers; capability fixed by weights; still egress |
| Self-hosted open weights | gpt-oss-120b/20b, Qwen3.8-27B, (Qwen3.8-2.4T, Kimi K3, DeepSeek V4 at cluster scale) | Data stays local; capital and operational cost; capability frozen at download; licence obligations apply |
| On-device / edge | gpt-oss-20b (MXFP4, ~16 GB), Qwen3.8-27B quantised, Whisper/Parakeet-class ASR | Lowest egress and latency floor; limited capability; battery/thermal |
| Speech-specific | Whisper large-v3, Parakeet TDT, Canary, Qwen3-ASR; TTS and realtime speech-to-speech endpoints | Composable pipeline vs. single model; different privacy and latency profiles (Phase 6) |

**Established:** hosted frontier access and open weights are no longer separated by a generation for
*some* workloads — Kimi K3's authors explicitly report that their model still trails Claude Fable 5
and GPT-5.6 Sol overall while outperforming other open and proprietary models in their suite `[V]`
(Kimi K3 report, [arXiv:2607.24653](https://arxiv.org/abs/2607.24653)). That is a rare, useful
candid vendor statement, and it is still a vendor statement.

---

## 3. Comparison table (checked 2026-11-01)

Prices are per 1M tokens, standard tier, USD. "Ctx" is the advertised maximum context.

| Model | Provider | Access | Modalities (in → out) | Ctx | Input $ | Output $ | Licence / terms | Source (checked) |
|---|---|---|---|---|---|---|---|---|
| gpt-6-astra | OpenAI | API | text+image → text | 1.05M | 10.00 | 50.00 | Proprietary service | [models](https://developers.openai.com/api/docs/models), [pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-6.1-sol | OpenAI | API | text+image → text | 1.05M | 2.00 | 10.00 | Proprietary | same |
| gpt-6.1-terra | OpenAI | API | text+image → text | 1.05M | 1.00 | 5.00 | Proprietary | same |
| gpt-6-luna | OpenAI | API | text+image → text | 1.05M | 0.10 | 0.50 | Proprietary | same |
| claude-fable-5-1 | Anthropic | API | text+image → text | 1M | 10.00 | 50.00 | Proprietary | [models overview](https://platform.claude.com/docs/en/models/overview) |
| claude-opus-5-5 | Anthropic | API | text+image → text | 1M | 4.00 | 20.00 | Proprietary | same |
| claude-sonnet-5-5 | Anthropic | API | text+image → text | 1M | 2.00 | 10.00 | Proprietary | same |
| claude-haiku-5-5 | Anthropic | API | text+image → text | 1M | from 0.10 | from 0.50 | Proprietary | same |
| gemini-4-argon | Google | API, AI Studio | text+image+video+audio+PDF → text | 1.05M | 2.00 (intro to 2026-11-30; then 4.00) | 10.00 (then 20.00) | Proprietary | [what's new](https://ai.google.dev/gemini-api/docs/latest-model), [model card](https://deepmind.google/models/model-cards/gemini-4-argon/) |
| gemini-3.8-flash | Google | API | text+image+video+audio+PDF → text | 1.05M | see pricing page | see | Proprietary | [model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash), [pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| gemini-3.8-live | Google | Live API | audio+image+text → audio+text | 131K | see pricing page | see | Proprietary | [model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live) |
| DeepSeek-V4-Pro | DeepSeek | open weights + API | text → text | 1M | provider-dependent | provider-dependent | open weights, per-repo terms `[U]` | [arXiv:2606.19348](https://arxiv.org/abs/2606.19348) |
| DeepSeek-V4.1-Flash | DeepSeek | open weights | image+text → text | 1M | self-host | self-host | open weights, per-repo terms `[U]` | [arXiv:2609.19969](https://arxiv.org/abs/2609.19969) |
| Kimi K3 | Moonshot | open weights + API | image+text → text | 1M | self-host / provider | self-host / provider | open weights, per-repo terms `[U]` | [arXiv:2607.24653](https://arxiv.org/abs/2607.24653) |
| Qwen3.8-2.4T-A95B | Alibaba | open weights + API | text → text | 262K native → ~1.01M | self-host | self-host | custom "Qwen3.8-Max" licence, **not** Apache-2.0 `[V]` | [GitHub](https://github.com/QwenLM/Qwen3.8), [HF](https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B) |
| Qwen3.8-27B | Alibaba | open weights | image+video+text → text | 262K → ~1M | self-host | self-host | Apache-2.0 `[V]` | [HF](https://huggingface.co/Qwen/Qwen3.8-27B) |
| gpt-oss-120b | OpenAI | open weights | text → text | 131K | self-host | self-host | Apache-2.0 | [announcement](https://openai.com/index/introducing-gpt-oss/), [card](https://arxiv.org/pdf/2508.10925) |
| gpt-oss-20b | OpenAI | open weights | text → text | 131K | self-host | self-host | Apache-2.0 | same |
| Llama 4 Scout / Maverick | Meta | open weights | image+text → text | 10M / 1M (advertised) | self-host | self-host | Llama 4 Community Licence (not OSI) | [HF org listing](https://huggingface.co/api/models?author=meta-llama&sort=createdAt&direction=-1&limit=15) |
| Whisper large-v3 / turbo | OpenAI | open weights | audio → text | 30 s windows | self-host | self-host | MIT / Apache-2.0 | [arXiv:2212.04356](https://arxiv.org/abs/2212.04356) |
| Parakeet TDT 0.6B v3 | NVIDIA | open weights | audio → text | streaming/chunked | self-host | self-host | CC-BY-4.0 `[U]` | [Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard) |

Prices marked "see pricing page" are deliberately not transcribed: the Gemini pricing page is large
and multi-tiered, and transcribing it from memory would violate the citation rule. Read it directly
at [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing).

**Cache pricing matters more than headline price for J.A.R.V.I.S.** OpenAI's cached-input tier is
10× cheaper than standard input for the GPT-6 family ($1.00 vs $10.00 for Astra; $0.10 vs $2.00 for
Sol) `[V]` ([pricing](https://developers.openai.com/api/docs/pricing)). Anthropic publishes cache
pricing separately and sets a default effort level per model `[V]`
([models overview](https://platform.claude.com/docs/en/models/overview)). A J.A.R.V.I.S. system
prompt that is stable across turns is therefore an economic design decision, not just a quality one.

---

## 4. Detailed model reports

Template (required by the brief): identity/version/date · developer and sources · architecture and
parameters · modalities/context/tools · training and post-training where disclosed · benchmarks with
setup · independent results and reproducibility · inference/hardware/cost · licence and constraints ·
strengths and documented weaknesses · fit for J.A.R.V.I.S. · established/plausible/unproven ·
recommendation.

### 4.1 OpenAI GPT-6 Astra

1. **Identity.** `gpt-6-astra`, released 2026-09-03 (system card through OpenAI's Deployment Safety
   Hub, checked 2026-11-01).
2. **Developer / sources.** OpenAI. [GPT-6 Astra System Card](https://deploymentsafety.openai.com/gpt-6-astra);
   [model catalogue](https://developers.openai.com/api/docs/models); [pricing](https://developers.openai.com/api/docs/pricing).
3. **Architecture and parameters.** Undisclosed. The system card discusses an opaque "recurrent
   depth" design and its monitoring consequences; no parameter count, layer count, or training
   compute is published `[V]`. Treat all architecture claims about this model as **unverified**.
4. **Modalities, context, tools.** Text + image input, text output. 1.05M context, 128K max output.
   Tools: functions, web search, file search, computer use. Reasoning effort: low → max.
   Knowledge cutoff 2026-04-30 `[V]`.
5. **Training / post-training.** Not disclosed beyond safety-relevant statements (Preparedness
   evaluations, safeguards, monitors) `[V]`.
6. **Benchmarks with setup.** Vendor cross-model table published in a competitor's model card
   (§6.1) places Astra at 65.5% FrontierSWE v2, 72.6% OSWorld-2.0, 63.1% Vals Index, 41.4%
   AutomationBench, 5.4% Harvey Legal Agent — as measured by Google, i.e. a *third party running a
   shared harness*, not OpenAI `[V]`. OpenAI's own reporting is qualitative in the public summary.
7. **Independent results.** No independent replication of Astra's numbers was found in this survey.
   UK AISI and Apollo Research are reported to have evaluated it (§4.1.11 weakness 2), but the
   primary reports were not retrieved here — `[U]`.
8. **Inference speed / hardware / cost.** $10 in / $50 out per 1M tokens; $1.00 cached input; batch
   half price; "Fast" 2× and "Ultrafast" 6× tiers `[V]`. No published hardware or tokens/sec.
9. **Licence.** Proprietary service; usage governed by OpenAI terms; regional-processing endpoints
   carry a 10% uplift for models released on or after 2026-03-05 `[V]`.
10. **Strengths.** Highest-priced, highest-capability OpenAI tier; strongest documented computer-use
    and long-horizon agent results among OpenAI models; explicit tool surface; 1M context.
    **Documented weaknesses.** (a) First OpenAI model to reach the **Critical** cybersecurity
    threshold under its Preparedness Framework, with broad-release refusals tightened accordingly
    `[V]`. (b) The system card reports that Astra's chain-of-thought is harder to monitor, and that
    prompted sandbagging reduced one monitor's recall to zero — i.e. reasoning traces are not a
    dependable oversight channel `[V]`. (c) Highest cost in the family; short deprecation horizons
    are normal in this market.
11. **Fit for J.A.R.V.I.S.** Excellent for hard, consequential, low-frequency tasks (complex
    planning, code/tool debugging, computer use). Poor default for conversational turns and simple
    tool calls on cost grounds: at $50/1M output, a 2,000-token answer costs $0.10 before any
    reasoning tokens.
12. **Established / plausible / unproven.** Established: pricing, context, modality, tool surface,
    Critical-cyber designation, monitorability caveats (all vendor-published). Plausible: best-in-
    class computer use. Unproven: that its reasoning advantage survives J.A.R.V.I.S. scaffolding and
    is worth 5–25× the cost of Sol/Terra on our tasks.
13. **Recommendation: TEST** (as the premium escalation tier), not as default.

### 4.2 OpenAI GPT-6.1 Sol / Terra / Luna

1. **Identity.** `gpt-6.1-sol` (2026-09-29), `gpt-6.1-terra` (2026-10-26), `gpt-6-luna`
   (2026-09-22) `[V]`.
2. **Sources.** [Model catalogue](https://developers.openai.com/api/docs/models); [pricing](https://developers.openai.com/api/docs/pricing).
3. **Architecture.** Undisclosed. No parameter counts published.
4. **Modalities/context/tools.** All: text+image in, text out, 1.05M context, 128K output, tools
   (functions, web search, file search, computer use). Reasoning effort includes `none` for Terra and
   Luna, which Astra/Sol do not expose — a meaningful routing lever. Knowledge cutoffs 2026-04-30
   (Sol), 2026-06-01 (Terra), 2026-05-18 (Luna) `[V]`.
5. **Training.** Undisclosed.
6. **Benchmarks.** No primary benchmark table retrieved in this survey; secondary trackers report
   "near-Astra" capability for Sol at one-fifth the price `[U]`. This is exactly the claim J.A.R.V.I.S.
   must test rather than assume.
7. **Independent results.** None retrieved.
8. **Cost.** Sol $2/$10, Terra $1/$5, Luna $0.10/$0.50; cached $0.10/$0.05/$0.01 `[V]`.
9. **Licence.** Proprietary service.
10. **Strengths.** The price ladder is unusually regular (Astra → Sol → Terra → Luna spans 100× in
    input price), which makes tiered routing a plausible design. **Weaknesses.** Documented
    capability gaps are only visible in third-party tables; Terra is positioned for computer use and
    writing rather than deep reasoning; Luna's very low price implies aggressive capability
    trade-offs.
11. **Fit.** Sol is the natural candidate for the **default workhorse** if it holds up on T2/T3;
    Terra for vision-heavy and computer-use turns; Luna for classification, extraction, and routing.
12. **E/L/U.** Established: prices, limits, tool surface. Plausible: Sol ≈ Astra on most work.
    Unproven: the quality cliff between Sol and Terra/Luna on multi-step tasks.
13. **Recommendation: TEST** (Sol and Terra on the full suite; Luna on routing/extraction only).

### 4.3 Anthropic Claude Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5

1. **Identity and dates.** Fable 5.1 (September 2026), Opus 5.5 (2026-09-22), Sonnet 5.5
   (2026-09-28), Haiku 5.5 (2026-10-07) `[V]` ([system cards](https://www.anthropic.com/system-cards),
   [transparency hub](https://www.anthropic.com/transparency)).
2. **Sources.** [Models overview](https://platform.claude.com/docs/en/models/overview);
   [transparency hub](https://www.anthropic.com/transparency) (model report dated 2026-10-02).
3. **Architecture.** Undisclosed at the parameter level; the transparency hub discloses pretraining
   on "a proprietary mix" including synthetic data, deduplication and classification, and
   constitution-driven post-training `[V]`. No parameter counts.
4. **Modalities/context/tools.** Text (including voice dictation) + image in, text out. 1M context,
   128K max output for all four. Adaptive "thinking" with per-model default effort (Fable `high`,
   Opus `medium`, Sonnet `high`, Haiku `medium`). Knowledge cutoff 2026-06 for all four `[V]`.
5. **Training.** Disclosed only at the level of process, not data or compute `[V]`.
6. **Benchmarks with setup.** Cross-model table in Google's Gemini 4 Argon model card (§6.1) reports
   Fable 5.1 at 56.3% FrontierSWE v2, 57.9% Terminal-bench 4.0, 65.8% Vals Index; Opus 5.5 at 62.3%
   FrontierSWE v2, **66.4% Terminal-bench 4.0** (highest in that table), 67.0% Vals Index `[V]`
   (Google-run). Anthropic's own reporting is in system cards; the Sonnet 5.5 card reports
   ~4,100 simulated sessions for misleading-behaviour review and political even-handedness of 97.9%
   (API) / 99.0% (claude.ai) vs 86.2% / 95.7% for Sonnet 5 `[V]`.
7. **Independent results.** No independent replication retrieved. Note the honest caveat in
   Anthropic's own report: Sonnet 5.5 gave "slightly more wrong answers" on a no-lookup factual set
   while getting more right, and scored below other Claude models on the right-vs-wrong weighted
   measure `[V]` — a useful reminder that family naming does not order capability cleanly.
8. **Cost.** Fable $10/$50 · Opus $4/$20 · Sonnet $2/$10 · Haiku from $0.10/$0.50 `[V]`. Retirement
   floors published per model (e.g. Opus 5.5 not sooner than 2027-09-22) — better deprecation
   signalling than most providers `[V]`.
9. **Licence.** Proprietary service.
10. **Strengths.** Consistent 1M/128K envelope across tiers; explicit effort control; published
    retirement dates; unusually detailed honesty and political-neutrality evaluation reporting.
    **Weaknesses.** Text+image only (no audio/video input), no speech output; premium pricing for
    Fable; model-family naming does not guarantee monotone quality (above).
11. **Fit.** Opus 5.5 and Sonnet 5.5 are strong candidates for the workhorse and agentic-coding
    tiers; Haiku 5.5 maps to the router/extractor role; Fable 5.1 only for the hardest long-horizon
    tasks.
12. **E/L/U.** Established: prices, limits, dates, disclosure quality. Plausible: Opus 5.5's
    Terminal-bench lead in Google's table reflects real long-horizon reliability. Unproven: that
    Anthropic's advantage persists on J.A.R.V.I.S. tasks with our tool schemas.
13. **Recommendation: TEST** (Opus 5.5 and Sonnet 5.5 primary; Haiku 5.5 for routing).

### 4.4 Google Gemini 4 Argon (plus 3.8 Flash and 3.8 Live)

1. **Identity.** `gemini-4-argon`, released 2026-10-13 (Firebase model registry; model card published
   October 2026) `[V]`.
2. **Sources.** [What's new in Gemini 4 Argon](https://ai.google.dev/gemini-api/docs/latest-model);
   [model card](https://deepmind.google/models/model-cards/gemini-4-argon/);
   [PDF](https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-4-Argon-Model-Card.pdf);
   [pricing](https://ai.google.dev/gemini-api/docs/pricing).
3. **Architecture.** Undisclosed parameter counts. The model card is structured around capability
   thresholds (CBRN, cybersecurity) under the Frontier Safety Framework rather than architecture `[V]`.
4. **Modalities/context/tools.** Text, image, video, audio, PDF input → text output. **Unified 1M
   token window shared by input, thinking, and output**, with long decode up to 1M tokens. Thinking
   levels low/medium/high (default medium). No image generation, no audio generation, **no Live API**
   on Argon `[V]`. Video understanding is the differentiator for T5.
5. **Training.** Not disclosed; the card reports development-time safety evaluations and mitigation `[V]`.
6. **Benchmarks with setup.** The model card publishes a cross-vendor table (§6.1). Argon leads that
   table on GraphWalks 256K–1M (84.2% vs 71.8% Astra, 65.0% Fable 5.1), Harvey Legal Agent (19.6% vs
   5.4% / 6.7%), DeepSWE v1.1 (77.9%), and LVBench video (91.7%); it trails on FrontierSWE v2
   (55.0% vs 65.5% Astra) and Terminal-bench 4.0 (57.4% vs 66.4% Opus 5.5) `[V]`. Google also
   reports a 0.7% attack success rate across 15 adaptive attempts on the Gray Swan indirect
   prompt-injection suite, and coding "shortcut attempts" at 2% `[V] [U]` (secondary report of the
   launch post; re-check before citing).
7. **Independent results.** None retrieved. The cross-vendor numbers are Google-run and therefore
   not independent of the publisher, even though they include competitors.
8. **Cost.** $2/$10 introductory through 2026-11-30, then $4/$20; cached input discounts published
   on the pricing page `[V]`.
9. **Licence.** Proprietary service.
10. **Strengths.** Best documented long-context (>256K) and video results in the surveyed set;
    shared input/thinking/output window simplifies budgeting; strong knowledge-work scores.
    **Weaknesses.** Weaker terminal/software-engineering results than the leaders; no audio output or
    Live API on this model (use 3.8 Live instead); introductory pricing expires within the month,
    which changes cost modeling.
11. **Fit.** Primary candidate for T2 (research, long documents) and T5 (video/image understanding);
    secondary for T3 (long-horizon terminal work).
12. **E/L/U.** Established: modality support, context semantics, pricing schedule, benchmark
    *table contents*. Plausible: long-context advantage is real and usable at 500K+. Unproven: that
    the 84.2% GraphWalks figure transfers to messy personal corpora rather than synthetic graph walks.
13. **Recommendation: TEST** (priority candidate for research and multimodal).

**Siblings.** `gemini-3.8-flash`: 1,048,576 input / 65,536 output, text+image+video+audio+PDF,
function calling, structured outputs, thinking low/medium/high (September 2026) `[V]`
([model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)) — the natural
high-volume workhorse. `gemini-3.8-live`: audio in → audio+text out, 131,072 in / 65,536 out,
interleaved reasoning, asynchronous function calling by default, `thinking_level` **not** supported
(September 2026) `[V]` ([model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live)) —
the natural candidate for T5 voice, evaluated in Phase 6.

### 4.5 DeepSeek V4 and V4.1-Flash

1. **Identity.** DeepSeek-V4 preview (arXiv submitted 2026-04-26); DeepSeek-V4.1-Flash (arXiv
   submitted 2026-09-17) `[I primary technical reports]`.
2. **Sources.** [arXiv:2606.19348](https://arxiv.org/abs/2606.19348);
   [arXiv:2609.19969](https://arxiv.org/abs/2609.19969) and its
   [HTML](https://arxiv.org/html/2609.19969v1).
3. **Architecture and parameters (disclosed).** V4: MoE — **V4-Pro 1.6T total / 49B activated**,
   **V4-Flash 284B / 13B**, both 1M context; **hybrid attention** combining Compressed Sparse
   Attention (CSA) and Heavily Compressed Attention (HCA); Manifold-Constrained Hyper-Connections
   (mHC) replacing plain residual connections; Muon optimizer. V4.1-Flash: **552B backbone +
   196B "Engram" parameters**, MoE with 1 shared + 384 routed experts, 6 routed active, **8B active
   during prefill and 16B during decode** (a causal encoder–decoder split: 20 encoder layers +
   20 decoder layers), 1M context, native multimodal `[I]`. V4.1-Flash reports **890 bytes/token**
   KV cache in HBM (≈1/4 of V4-Flash) using cross-layer KV reuse (CSA2) and **FP4 KV caching** `[I]`.
4. **Modalities/context/tools.** V4: text → text, 1M. V4.1-Flash: image + text → text, 1M. Tool use
   is claimed/benchmarked but schemas differ per serving provider; verify per provider `[U]`.
5. **Training.** V4: >32T tokens pretraining plus post-training pipeline with reasoning-effort modes.
   V4.1-Flash: 45T-token multimodal corpus `[I]`.
6. **Benchmarks.** Both reports contain vendor-run comparisons; the V4 report states V4-Pro-Max
   "redefines the state of the art for open models" `[V]`. Efficiency claims are more decision-
   relevant than absolute scores: at 1M context, V4-Pro needs **27% of single-token inference FLOPs
   and 10% of the KV cache of DeepSeek-V3.2** `[V]`.
7. **Independent results.** None retrieved. The efficiency numbers are theoretically checkable and
   therefore worth reproducing in Phase 7 if DeepSeek is shortlisted.
8. **Hardware/cost.** 1.6T and 552B-parameter models are multi-node deployments; per-token cost
   depends on the serving provider. **[Established]** these are not self-hostable on workstation
   hardware; the practical options are third-party hosted serving or not at all.
9. **Licence.** Open weights; exact terms per repository `[U]` (must be read before commercial use).
10. **Strengths.** Explicit, unusually detailed architecture disclosure; strong efficiency
    engineering for long context (CSA/HCA, FP4 KV cache); native multimodal input on V4.1-Flash.

    **Weaknesses.** Vendor-only evidence; enormous deployment size; serving/quantisation support
    varies; the model family changes fast (V4 April → V4.1-Flash September), which is a stability
    risk for a pinned baseline.
11. **Fit.** Hosted-served DeepSeek is a plausible cost-efficient option for T1/T2/T3; V4.1-Flash's
    prefill/decode asymmetry is attractive for agentic workloads that are input-heavy (Phase 7).
12. **E/L/U.** Established: architecture, parameter counts, context, KV-cache design (primary
    report). Plausible: efficiency translates to lower $/task. Unproven: quality on personal-domain
    tasks; licence suitability.
13. **Recommendation: TEST via a hosted provider** if a licence review clears it; **DEFER**
    self-hosting.

### 4.6 Moonshot Kimi K3

1. **Identity.** Kimi K3 (arXiv v1 2026-07-27, v2 2026-08-07) `[I report, V claims]`.
2. **Sources.** [arXiv:2607.24653](https://arxiv.org/abs/2607.24653);
   [HTML](https://arxiv.org/html/2607.24653v1).
3. **Architecture (disclosed).** MoE: **2.78T total / 104.2B activated**, 93 layers, hidden 7168,
   **896 routed experts, 16 active**, 2 shared experts, **hybrid attention: 69 Kimi Delta Attention +
   24 Gated MLA layers**, Attention Residuals across depth, SiTU-GLU, 1 MTP layer, 401M-parameter
   vision encoder (27 layers, patch 14). 1M context, trained at 1M `[I]`.
4. **Modalities/context/tools.** Native image + text → text; 1M context; agentic tool use is the
   stated design target; tool schemas are harness-dependent `[U]`.
5. **Training.** Muon-family optimisation lineage from K2 (MuonClip, 15.5T tokens, zero loss spikes —
   [K2 report](https://arxiv.org/abs/2507.20534)); K3 adds RL across general, agentic, and coding
   domains with **multiple reasoning-effort levels**, plus million-token agentic RL with persistent
   rollout/sandbox state `[I]`.
6. **Benchmarks.** Authors report ~2.5× scaling-efficiency improvement over K2 and state that K3
   "still trails the most powerful proprietary models, namely Claude Fable 5 and GPT-5.6 Sol" while
   outperforming other open and proprietary models in their suite `[V]`. Secondary summaries report
   88.3 on Terminal-Bench 2.1 and 81.2 on FrontierSWE `[U]` — not used here because the primary
   table was not retrieved.
7. **Independent results.** None retrieved.
8. **Hardware/cost.** 2.78T parameters with 104B active is a multi-node serving proposition; not a
   workstation model. Hosted access exists via Moonshot and third parties `[U]`.
9. **Licence.** Open weights; terms per repository `[U]`.
10. **Strengths.** Most detailed open architecture disclosure at 3T scale; strong long-horizon
    agentic RL story; explicit multimodal encoder. **Weaknesses.** Vendor-acknowledged gap to the
    closed frontier; serving complexity; the K2 report itself documents verbosity and over-eager tool
    use in difficult reasoning — a failure mode directly relevant to T6/T7.
11. **Fit.** Hosted K3 is a credible open-weights alternative for T3 agentic planning; self-hosting
    only at cluster scale.
12. **E/L/U.** Established: architecture and parameters (primary). Plausible: competitive agentic
    performance. Unproven: reliability (`pass^k`) versus closed models on our tasks.
13. **Recommendation: TEST via hosted serving**; **DEFER** self-hosting.

### 4.7 Qwen3.8 (2.4T-A95B and 27B)

1. **Identity.** Qwen3.8-Max API 2026-08-03; open weights `Qwen3.8-2.4T-A95B` 2026-08-12;
   `Qwen3.8-27B` 2026-08-14 `[V]`.
2. **Sources.** [Qwen3.8 GitHub](https://github.com/QwenLM/Qwen3.8);
   [2.4T model card](https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B);
   [27B model card](https://huggingface.co/Qwen/Qwen3.8-27B).
3. **Architecture.** Max-class checkpoint: MoE, **2.4T total / 95B active**, 92 layers alternating
   Gated DeltaNet (linear attention) with standard attention, hidden 8,192, **512 experts** with 10
   routed + 1 shared active; built on the Qwen3.5 architecture `[V]`. 27B: **dense** 27B with a
   vision encoder `[V]`.
4. **Modalities/context/tools.** 2.4T open build: text-only, thinking always on, `reasoning_effort`
   (xhigh default) and `preserve_thinking`; **262,144 native context, extensible to ~1,010,000**
   `[V]`. 27B: native image and video understanding, same context envelope `[V]`.
5. **Training.** Qwen3.5 lineage describes early-fusion multimodal pretraining on trillions of
   multimodal tokens and RL across "million-agent environments" `[V]` (GitHub README). No token
   counts for 3.8 specifically retrieved.
6. **Benchmarks.** The 27B model card is reported (secondary `[U]`) to list 73.0 Terminal Bench,
    61.7 SWE-bench Pro, 84.3 OSWorld-Verified, 81.9 AndroidWorld. These are unusually strong for a
    27B dense model and **must be verified against the card** before use.
7. **Independent results.** None retrieved.
8. **Hardware/cost.** 2.4T-A95B: BF16 ≈4.89 TB, official FP8 ≈2.50 TB (secondary `[U]`) — cluster
   only. **27B Apache-2.0 with vision is the practically important release**: single-GPU at FP8/INT8,
   workstation-class at 4-bit.
9. **Licence. [Critical finding]** The Max-class weights ship under a **custom "Qwen3.8-Max" licence,
   not Apache-2.0**, while **Qwen3.8-27B is Apache-2.0** `[V]` — confirmed on the official GitHub
   README and model card repositories. Any plan that assumes permissive terms for the 2.4T model is
   wrong.
10. **Strengths.** Broadest licence-safe capability at 27B with vision; linear-attention hybrid
    designed for throughput (Phase 7 relevance); explicit thinking controls.
    **Weaknesses.** Flagship licence restrictions; thinking always on for the 2.4T build (cost);
    vendor benchmarks unverified; fast release cadence.
11. **Fit.** **Qwen3.8-27B is the strongest candidate for a local/private tier** (T1, T5 image
    understanding, extraction, routing fallback). 2.4T hosted is a candidate for T3 if hosted.
12. **E/L/U.** Established: 2.4T weights are openly downloadable; licence split. Plausible: 27B
    performance competitive with much larger hosted models. Unproven: the 27B's long-horizon agentic
    scores, and whether a 27B is good enough for T3 at acceptable `pass^k`.
13. **Recommendation: TEST Qwen3.8-27B as the private tier; DEFER the 2.4T checkpoint** (licence +
    hardware cost) **until hosting economics are measured.**

### 4.8 OpenAI gpt-oss-120b / 20b

1. **Identity.** Released 2025-08-05; no successor found on the official HF org as of 2026-11-01
   `[I]` ([openai org listing](https://huggingface.co/api/models?author=openai&sort=createdAt&direction=-1&limit=15)).
2. **Sources.** [Announcement](https://openai.com/index/introducing-gpt-oss/); model card
   [PDF](https://cdn.openai.com/pdf/419b6906-9da6-406c-a19d-1bb078ac7637/oai_gpt-oss_model_card.pdf) /
   [arXiv:2508.10925](https://arxiv.org/pdf/2508.10925).
3. **Architecture (disclosed).** MoE transformers: **120b = 116.8B total / 5.1B active**, 36 layers,
   128 experts, top-4 routing; **20b = 20.9B / 3.6B**, 24 layers, 32 experts; alternating dense and
   locally-banded sparse attention; residual dim 2880; **MXFP4 quantised** MoE weights (4.25 bits
   per parameter, >90% of parameters) `[I/V]`.
4. **Modalities/context/tools.** Text only; 131,072 context; adjustable reasoning effort; function
   calling and structured outputs; compatible with the Responses API `[V]`.
5. **Training.** Trillions of tokens focused on STEM, coding, general knowledge; reinforcement
   learning plus techniques from o3-lineage models; **knowledge cutoff June 2024**; 2.1M H100-hours
   for 120b, ~10× fewer for 20b `[V]`.
6. **Benchmarks.** 120b reported near o4-mini on core reasoning, above o3-mini on competition coding
   and tool calling. Notably poor factuality: **SimpleQA accuracy 0.168 with a 0.782 hallucination
   rate** (20b: 0.067 / 0.914) `[V]`. This is directly relevant to T2 and T4: these are not models to
   trust for factual recall without retrieval.
7. **Independent results.** Widely re-run on public leaderboards; not re-verified here. The
   hallucination numbers are a vendor self-report and should be assumed to be at least as bad in
   production.
8. **Hardware/cost.** 120b fits one 80 GB GPU (~60.8 GiB checkpoint); 20b runs in ~16 GB; both in
   vLLM/Ollama/llama.cpp `[V]`. Zero licence cost, real hardware and ops cost.
9. **Licence.** **Apache-2.0** plus the gpt-oss usage policy `[V]` — the most permissive terms in this
   survey for a capable open model.
10. **Strengths.** Runs on owned hardware, fully inspectable, permissive licence, tool-capable.
    **Weaknesses.** 2024 knowledge cutoff, poor factuality without retrieval, text-only, one year old
    with no successor.
11. **Fit.** The pragmatic **private-tier default for the 2026 experiment set**: tool routing, T1
    explanations over retrieved context, T6 argument drafting with validation. Not a T2 research
    model and not a T5 multimodal model.
12. **E/L/U.** Established: architecture, size, licence, hardware fit, factuality numbers. Plausible:
    adequate for routing and drafting. Unproven: whether 5.1B active parameters are enough for T3
    multi-step reliability.
13. **Recommendation: TEST** as the private/offline baseline — and keep it as the **control** that any
    hosted upgrade must beat on cost-adjusted success.

### 4.9 Meta Llama 4 and other families

- **Llama 4 Scout / Maverick** (April 2025) remain the newest `meta-llama` open releases
  `[I primary]`. Advertised limits: Scout 17B active of 109B across 16 experts with a 10M-token
  context; Maverick 17B active of ~400B across 128 experts with 1M `[U]` pending a direct card read.
  Licence is a community licence, not OSI-open (naming, acceptable-use, and 700M-MAU conditions) `[U]`.
  **Recommendation: REJECT for new work** — 18 months old with no successor, and Qwen3.8-27B is
  Apache-2.0 with vision. Note the Llama 4.5 claims in §1.1 are unsupported.
- **Meta's 2026 frontier models** are reported as closed, API-only "Muse"-class models `[U]`. Not
  evaluated here; no primary source retrieved.
- **Gemma 4** appears as a line item on the official Gemini pricing page `[V]`, so the family exists
  as of 2026-11-01; parameters, licence, and quality were **not verified** in this survey. Treat as
  an open question for the local tier.
- **Mistral, Cohere, IBM Granite, AI21:** not verified in this survey. Do not include in a shortlist
  until a primary model card and licence are read. `[U]`

### 4.10 Specialist models for T5 (speech, audio, embeddings)

These are covered in depth in Phase 6; the selection implications:

| Role | Candidates | Evidence | Note |
|---|---|---|---|
| ASR (offline/batch) | Whisper large-v3 (1.55B, MIT), Parakeet TDT 0.6B v3, Canary family, Qwen3-ASR-1.7B | HF Open ASR Leaderboard: Parakeet TDT 0.6B v3 ≈6.32% avg WER at high RTFx vs Whisper large-v3 ≈7.44% `[I]`, [leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard) | Whisper wins on language coverage; Parakeet wins on English accuracy/speed |
| ASR (streaming) | Vendor realtime APIs; streaming-capable open models | Vendor `[V]` | Measure end-to-end, not model-only (Phase 6) |
| TTS | Gemini Flash TTS tiers, hosted vendor TTS, open TTS | Vendor `[V]` | No independent latency comparison retrieved |
| Speech-to-speech | `gpt-live-1` ($0.05/min `[V]`), `gpt-realtime-2.1` ($32/$64 per 1M audio tokens `[V]`), `gemini-3.8-live` | Vendor `[V]` | Removes STT/TTS stitching but adds vendor coupling and egress |
| Embeddings | Gemini Embedding 2 (pricing page `[V]`), open embedding models | Vendor | Phase 5 will compare hybrid retrieval with a reranker |

**Established:** speech recognition quality is no longer the bottleneck for English; the open-source
leader has ~6% average WER and runs far faster than real time `[I]`. The bottleneck is the *system*
around it (endpointing, barge-in, entity accuracy, confirmation of writes).

---

## 5. The shared J.A.R.V.I.S. task suite (designed, not yet run)

The brief requires comparing candidates on a shared task suite "where feasible". **No model was
executed in this phase** (no credentials, no budget); what follows is the suite that Phase 8 will
implement. It is specified here so model selection is not retrofitted to whatever we happen to test.

| Block | Tasks (n) | Instrument | Primary metric |
|---|---|---|---|
| B1 Grounded Q&A | 40 | Internal set with dated gold sources | Task success, citation precision, groundedness |
| B2 Tool use under policy | 30 | τ²-bench-style dual-control scenarios, adapted | `pass^1`, `pass^4`, argument exactness |
| B3 Long-horizon plan | 25 | Terminal-Bench-2.0-style verifier tasks + 5 personal-automation tasks | Verifier success, false-success rate |
| B4 Memory | 50 | LongMemEval-style, seeded with synthetic personas | Update accuracy, abstention, provenance coverage |
| B5 Multimodal | 30 | Document field extraction, screenshots, 5 video clips | Field accuracy, silent-failure rate |
| B6 Voice | 20 sessions | User-recorded audio, noisy and clean | WER, round-trip latency, confirmation behaviour |
| B7 Adversarial | 40 | AgentDojo-style injection + poisoned documents | Attack success rate, approval fidelity |

Rules: identical prompts, identical tool schemas, identical budgets; ≥3 repeats per cell for `pass^k`;
judge-model scoring calibrated against ≥100 human labels; all costs logged; contamination audit for
any public benchmark item used.

---

## 6. Cross-model evidence and its limits

### 6.1 The Gemini 4 Argon model card table (Google-run, published 2026-10-13)

Selected rows, verbatim from the card `[V]`:

| Benchmark | Gemini 4 Argon | GPT-6 Astra | Claude Fable 5.1 | Claude Opus 5.5 |
|---|---|---|---|---|
| Vals Index (knowledge work) | 68.9% | 63.1% | 65.8% | 67.0% |
| Harvey Legal Agent | 19.6% | 5.4% | 6.7% | 3.8% |
| DeepSWE v1.1 (agentic coding) | 77.9% | 74.1% | 67.4% | 74.2% |
| FrontierSWE v2 | 55.0% | 65.5% | 56.3% | 62.3% |
| Terminal-bench 4.0 | 57.4% | 58.2% | 57.9% | 66.4% |
| GraphWalks BFS (256K–1M) | 84.2% | 71.8% | 65.0% | 66.8% |
| OSWorld-2.0 (offline subset) | 69.2% | 72.6% | — | — |
| LVBench (video) | 91.7% | 87.5% | 79.7% | 83.7% |

**How to read this table.** It is the most useful single artifact found in this phase, because it
runs *one* harness across four vendors' models. It is still `[V]`: the publisher is one of the
competitors, the harness versions are Google's choice, and the benchmarks (Vals Index,
AutomationBench, FrontierSWE, DeepSWE, GraphWalks, Terminal-bench 4.0, Harvey Legal) are a mix of
third-party and vendor-defined. Several of these benchmark names did not exist in my prior knowledge
and were accepted here only because they appear in a primary vendor card — their independent
validity is **unverified** `[U]`.

What the table supports: **no model leads everything.** Argon leads long-context and legal/knowledge
work; Astra leads software engineering and computer use; Opus 5.5 leads the terminal benchmark. That
pattern is the empirical basis for the routing hypothesis in §7.1.

### 6.2 Leaderboard auditing

Terminal-Bench's official board audits configurations, publishes agent scaffold and trajectories, and
shows large scaffold-vs-model effects `[I]` ([tbench.ai](https://www.tbench.ai/leaderboard/terminal-bench/2.0)).
Independent re-aggregations that do not name the scaffold should be treated as unusable `[U]`.

### 6.3 Contamination

Removing contaminated samples lowers top-10 SWE-bench Verified resolution rates by >20% on
medium-difficulty tasks, and up to 55.9% on Multi-SWE-bench `[I]`
([arXiv:2610.19447](https://arxiv.org/html/2610.19447)). Any decision that hinges on a few points of
difference between models is therefore unfounded; only differences larger than the contamination and
harness envelopes (tens of points) are decision-grade.

---

## 7. Analysis: what the landscape implies

### 7.1 The routing hypothesis (Plausible, to be tested)

Prices span **50× within a single vendor** (OpenAI Luna $0.10 → Astra $10.00 input) and **~100× across
vendors** for comparable-looking capability claims. No single model leads all seven task families in
the only cross-vendor table we have (§6.1). Therefore the efficient design is very likely:

- a **cheap default** (Luna / Haiku 5.5 / Qwen3.8-27B / gpt-oss-120b) for routing, extraction,
  summarisation, and simple tool calls;
- a **mid workhorse** (GPT-6.1 Sol or Terra / Sonnet 5.5 / Opus 5.5 / Gemini 3.8 Flash) for research
  synthesis and multi-step work;
- a **premium escalation** (Astra / Opus 5.5 / Fable 5.1 / Gemini 4 Argon) only when a verifier or the
  user signals the mid tier failed.

This is a hypothesis, not a finding. It is falsifiable by the B-block suite: if a single mid-tier
model matches the routed system at lower total cost *including* routing overhead and escalation
misses, routing is unjustified complexity (Phase 4 tests exactly this).

### 7.2 Single model vs. many

Counter-evidence worth recording: multi-agent/multi-model systems are expensive. Anthropic's public
account of its research system reports roughly 15× token usage versus chat and attributes ~80% of
performance variance on BrowseComp to token usage `[V]` (secondary summary of the Anthropic
engineering post `[U]`), while Cognition argues against multi-agent architectures for
shared-context work `[U]`. The measured lesson for J.A.R.V.I.S. is Phase 1's: extra calls must earn
their place in *end-to-end success per dollar*, not in perceived sophistication.

### 7.3 Local vs. hosted

- **Local tier is now real but bounded.** gpt-oss-120b runs in one 80 GB GPU `[V]`; Qwen3.8-27B is
  Apache-2.0 with vision `[V]` and runs on workstation-class hardware after quantisation; ASR is
  cheap and local `[I]`.
- **Local tier is behind on long-horizon reliability** (vendor-acknowledged for Kimi K3 vs closed
  frontier; unmeasured for everything else).
- **Therefore** the defensible split is: local for private data handling, ASR/TTS, extraction,
  routing, and offline fallback; hosted for T2/T3 escalation — unless Q1/Q2 force full locality, in
  which case the whole shortlist changes and Phase 2 must be re-run.

### 7.4 Why we are not recommending a new foundation model

The brief gates this on evidence. The evidence points the other way:

- Open weights at 27B (Apache-2.0) and 120B (Apache-2.0) exist with tool use `[V]`.
- The measured failure modes in Phase 1 (T4 memory, T6 argument exactness, T7 truthful completion)
  are **system** failures, and are attacked by retrieval design, validation schemas, and verifiers —
  not by more pretraining.
- Training a comparable frontier model is a >$100M-class capital commitment; no measurement on this
  project's workload could justify that before the system-level baseline exists.

**What would change this:** if, after Phase 8's baseline, the routed system plateaus at a task
success rate far below Phase 1 targets *and* error analysis attributes the residual to a specific,
narrow capability (e.g. a private-domain vocabulary or a fixed output grammar) that is cheaply
addressable by fine-tuning an open model, then **fine-tuning** — not foundation pretraining — becomes
the candidate intervention.

---

## 8. Recommendations: test / defer / reject

| Candidate | Verdict | Role | Why |
|---|---|---|---|
| GPT-6.1 Sol | **TEST** | Workhorse default | Best price/capability position in OpenAI's ladder; 1M ctx; full tool surface |
| GPT-6.1 Terra | **TEST** | Vision + computer use | `none`-effort available; cheap; specialised for computer use |
| GPT-6.1 Luna | **TEST** | Router / extraction | $0.10/$0.50 makes routing overhead affordable |
| GPT-6 Astra | **TEST (escalation)** | Hard tasks | Highest capability, 5–25× Sol cost; monitorability caveats |
| Claude Opus 5.5 | **TEST** | Workhorse / long-horizon | Best terminal-bench result in the cross-vendor table |
| Claude Sonnet 5.5 / Haiku 5.5 | **TEST** | Workhorse / router | Consistent 1M/128K envelope; good cost curve |
| Claude Fable 5.1 | **DEFER** | Escalation | Only if Opus 5.5 at high effort falls short |
| Gemini 4 Argon | **TEST** | Research + multimodal | Long-context and video leadership; 1M unified window |
| Gemini 3.8 Flash | **TEST** | High-volume workhorse | Modalities + structured outputs; cheaper than Argon |
| Gemini 3.8 Live | **TEST (Phase 6)** | Voice | Native audio; the only surveyed realtime path with 131K context |
| gpt-oss-120b / 20b | **TEST** | Private baseline | Apache-2.0, single-GPU; poor factuality → retrieval required |
| Qwen3.8-27B | **TEST** | Private multimodal | Apache-2.0 + vision at 27B |
| Qwen3.8-2.4T-A95B | **DEFER** | Hosted open alternative | Custom licence; cluster-scale |
| DeepSeek V4.1-Flash | **TEST (hosted)** | Efficient long-context | Prefill/decode asymmetry suits agents; licence review first |
| Kimi K3 | **TEST (hosted)** | Open agentic alternative | Strong disclosure; vendor-acknowledged frontier gap |
| Llama 4 Scout/Maverick | **REJECT** | — | 18 months stale, no successor, non-OSI licence |
| Llama 4.5 / Llama 5 | **REJECT (as of 2026-11-01)** | — | Not present in official distribution (§1.1) |
| Gemma 4, Mistral, Cohere, Granite | **DEFER** | — | Not verified in this phase |
| Whisper large-v3 | **TEST** | Batch ASR, multilingual | MIT; broad language coverage |
| Parakeet TDT 0.6B v3 | **TEST** | Fast English ASR | Best measured WER/RTFx trade-off on the open leaderboard |

---

## 9. Findings classification

**Established (primary evidence)**
- All OpenAI, Anthropic, and Google flagship models named above exist with the stated context limits,
  modality support, tool surfaces, and list prices, as of 2026-11-01 (vendor documentation).
- Open weights exist for gpt-oss-120b/20b (Apache-2.0), Qwen3.8-27B (Apache-2.0), Qwen3.8-2.4T-A95B
  (custom licence), DeepSeek V4/V4.1, and Kimi K3, with the disclosed architectures quoted in §4.
- Llama 4 remains Meta's newest open-weight generation in the official distribution channel; "Llama
  4.5/5" claims are unsupported by that channel.
- Benchmarks are contamination- and harness-sensitive at a magnitude that makes small cross-model
  deltas non-decision-grade.
- Speech recognition is commodity-grade for English in open weights (~6% average WER at high RTFx).

**Plausible**
- A three-tier routed system beats both a single premium model and a single cheap model on
  cost-adjusted success, because no vendor leads all task families in the one cross-vendor table
  available.
- A 27B Apache-2.0 open model plus hosted escalation covers the privacy-sensitive tier adequately.
- "Near-flagship at one-fifth price" (Sol/Astra, Sonnet 5.5/Opus 5.5) holds for most tasks but not the
  hardest.

**Unproven**
- Any claim that a specific model will hit Phase 1's provisional targets on J.A.R.V.I.S. tasks. None
  has been run.
- That reasoning-trace monitoring can serve as a safety control for the newest models, given
  vendor-reported declines in CoT monitorability.
- That vendor-defined 2026 benchmarks (Vals Index, FrontierSWE, DeepSWE, GraphWalks) are valid
  measures of personal-assistant capability.
- That local inference at acceptable latency is achievable on the user's hardware (Q8 unanswered).

---

## 10. What was learned, what remains uncertain, and the next research step

**What was learned.** The 2026 landscape is characterised by (a) a 50–100× price span between
"flagship" and "cheap" tiers within and across vendors, (b) genuinely capable Apache-2.0 open weights
in the 20B–27B range, (c) open-weight flagships at 2.4T–2.8T parameters that are downloadable but not
deployable by an individual, and (d) benchmark tables that disagree with each other in ways that
track the publisher. The single most decision-relevant artifact is a competitor-run cross-vendor
table, which tells us more about *specialisation* than any single vendor's own numbers.

**What remains uncertain.** Whether any of these models meets Phase 1's honesty, memory, and argument-
exactness targets; whether routing overhead is cheaper than a single mid-tier model; whether the
user's privacy requirements (Q2) exclude hosted APIs; whether local hardware (Q8) can serve a 27B at
interactive latency; and whether the vendor-defined benchmarks in §6.1 measure anything we care about.
Two model-family claims encountered during research (Llama 4.5; "gpt-oss-2") were disproven by
checking the official distribution channel, which is a reminder that the rest of the landscape must
be treated with the same suspicion.

**Next research step.** Phase 3: examine the architectures and training methods behind these models —
dense vs. MoE, hybrid/recurrent attention, multimodal designs, and the post-training pipeline — to
determine which architectural claims are load-bearing for J.A.R.V.I.S. and which are marketing, and
to settle the question of *adapting* an existing model (SFT, preference optimisation, distillation,
quantisation) versus building one.
