# Phase 6 — Vision, Audio, and Voice

- **Status:** published (v0.2, open for review)
- **Published:** 2026-11-01
- **Sources checked:** 2026-11-01
- **Related:** [Index](README.md) · [Phase 1 — Workload](01-jarvis-workload-and-requirements.md) (T5) · [Phase 2 — Models](02-model-landscape-and-selection.md) §4.10 · Phase 7 (inference/latency)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · **`[V]` vendor** · **`[I]` independent** · **`[U]` unverified**

---

## 0. The requirement

From Phase 1, T5: correct understanding across documents, images, audio, and live voice — with
modality-specific evidence (page, region, timestamp) — inside a latency envelope the user experiences
as conversation.

The single most important rule in this phase:

> **Measure the complete voice path, not model token speed.** A speech-to-speech model that decodes
> audio in 120 ms can still produce a 2.5-second perceived turn because of endpointing, network, and
> playback buffering.

---

## 1. Method

1. Decompose the voice path into measurable stages (§2) and attribute latency per stage.
2. Compare ASR, TTS, and speech-to-speech options on accuracy **and** on system properties (licence,
   local feasibility, egress).
3. Record the conflict between vendor latency claims and independent field measurements rather than
   picking the flattering number.
4. For vision/video, separate *advertised benchmark scores* from *usable behaviour on personal
   content*.

---

## 2. The complete voice path

```
 mic capture ─► VAD/endpointing ─► ASR (or speech-to-speech model) ─► model reasoning
      ─► tool calls (optional) ─► TTS (or model audio out) ─► network ─► playback/barge-in
```

| Stage | Typical contribution | What determines it | Evidence |
|---|---|---|---|
| Capture + jitter buffer | 20–100 ms | Client implementation, buffer size | Engineering [I] |
| VAD / endpointing | 100–700 ms | How long silence must last to be "end of turn" | **Dominant tuning knob**; too short truncates, too long feels laggy |
| ASR first partial | 100–300 ms for streaming ASR; 200 ms–1 s for batch | Model and streaming support | `[U]` per-model; must be measured |
| ASR finalisation | 50–300 ms | Segment length, rescoring | `[U]` |
| Model TTFT | 200 ms–2 s | Model tier, prompt size, effort level | Phase 7 |
| Tool call round trip | 400–800 ms added when a tool responds <200 ms | Tool latency and orchestration | Field report `[U]` |
| First audio out (TTS) | 28–300 ms local (Kokoro-class, hardware dependent); 150–400 ms hosted `[U]` | Model, hardware, chunk size | Metric tables below |
| Network RTT | 5–150 ms | Region, mobile vs wired | `[U]` |

### 2.1 The conflicting evidence, stated plainly

| Source | Claim | Tag |
|---|---|---|
| Vendor documentation | Speech-to-speech can serve in the low hundreds of milliseconds; one vendor reported p95 latency down ≥25% across its Realtime models from improved caching | `[V] [U]` |
| Practitioner write-up | 800 ms voice-to-voice is achievable end-to-end, but requires a ~500 ms API time-to-first-byte budget, leaving ~300 ms for capture, VAD, network, rendering | `[U]` |
| Independent field measurement of a hosted realtime API | Median turn latency **2.24 s** on medium calls (max 4.05 s), **3.4 s** on long calls (max 6.7 s), degrading to ~5–6 s per turn beyond ~5 minutes of conversation | `[U]` (vendor-blog published measurement, single geography) |
| Full-duplex research system | **160 ms theoretical / 200 ms practical** end-to-end for a native duplex speech model (Moshi) | `[I]` ([arXiv:2410.00037](https://arxiv.org/abs/2410.00037)) |

These are not contradictory in principle — they measure different stacks (a research model on a
dedicated GPU vs. a hosted API over consumer broadband vs. a tuned production pipeline). They are,
however, incompatible as planning inputs. **Phase 1's 800 ms p50 / 1.4 s p95 target must therefore be
treated as a hypothesis to be validated locally**, with the 2–3 s field numbers as the realistic
downside.

### 2.2 The measurement protocol (E-6.1)

Record, per turn: `t_end_of_speech`, `t_asr_partial`, `t_asr_final`, `t_model_first_token`,
`t_tool_start/end`, `t_tts_first_audio`, `t_first_audible_frame`. Compute stage totals and the
residual (unattributed). Repeat ≥50 turns per condition, across: clean/noisy, short/long utterance,
with/without a tool call, session length buckets (0–2 min, 5–10 min, 20 min+). Report p50/p95/p99.

**Established:** session-length degradation is real in at least one hosted API `[U]`; our protocol must
therefore include a "20 minutes in" bucket, not just a fresh session.

---

## 3. Speech recognition

### 3.1 Accuracy evidence (HF Open ASR Leaderboard, checked 2026-11-01)

| Model | Params | English avg WER (short-form) | Long-form WER | RTFx (A100, batch 64) | Languages | Licence |
|---|---|---|---|---|---|---|
| NVIDIA Parakeet TDT 0.6B v3 | 0.6B | **6.32%** | 10.7% | ~3,330 | 25 | CC-BY-4.0 `[U]` |
| NVIDIA Parakeet TDT 0.6B v2 | 0.6B | 6.05% | — | ~3,390 | — | CC-BY-4.0 |
| OpenAI Whisper large-v3 | 1.55B | 7.44% | 11.2% | ~146 | 99 | MIT/Apache-2.0 |
| Whisper large-v3-turbo | 809M | 7.83% | 11.0% | ~148 | 99 | MIT |
| Parakeet CTC 1.1B | 1.1B | 7.40% | 12.9% | ~2,730 | — | CC-BY-4.0 |

Sources: [HF Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard);
Whisper paper [arXiv:2212.04356](https://arxiv.org/abs/2212.04356); per-model cards. Figures are
`[I]` for the leaderboard, `[V]` for vendor card claims, and `[U]` where a blog restates them.

### 3.2 What this means, beyond the table

- **English accuracy is commoditised.** ~1 point of WER separates the leaders; that difference is
  smaller than the effect of microphone, room, and accent. Choose on speed, licence, language
  coverage, and deployment constraints.
- **Speed differs by ~20×.** Parakeet-class models run at thousands of times real time; Whisper
  large-v3 at ~146×. For batch transcription of a personal archive, that is the difference between
  minutes and hours.
- **Language coverage is the real discriminator.** Whisper covers 99 languages; Parakeet v3 covers 25.
  Q7 in Phase 1 (which languages/accents) therefore decides the shortlist.
- **Long-form degrades for everyone** (~10.7–12.9% vs ~6–7.8% short-form). Personal voice memos are
  long-form. Do not plan on short-form numbers.

### 3.3 Entity accuracy is the metric that matters most

Phase 1 identified names, numbers, dates, and emails as the failure class that causes downstream
damage. Aggregate WER hides this. **Measure a separate "critical entity error rate"** on a personal
test set: proper nouns (contacts), times/dates, amounts, email addresses, and confirmation codes.

**Rule:** any entity that will be written to a system of record (T6) must be **read back for
confirmation**, regardless of ASR confidence. This is an application-level control that costs one
sentence and removes the highest-impact failure.

### 3.4 Diarisation (who said what)

| System | DER (AMI) | DER (VoxConverse) | DER (DIHARD III) | Notes |
|---|---|---|---|---|
| pyannote.audio 3.1 | ~11–14% | ~9–11% | ~17–20% | Open source, batch-oriented; **streaming adds ~5–10 pp** |
| Commercial APIs | ~7–14% | varies | varies | Streaming 200–500 ms; egress |

Sources: pyannote model card claims restated in [vendor-adjacent guides](https://vexascribe.com/pyannote-audio) `[U]`;
independent benchmark write-ups `[U]`. Treat exact DERs as indicative until reproduced.

**Consequence:** diarisation is good enough to *label* a meeting transcript for the user to review,
and not good enough to attribute a commitment to a person automatically. If J.A.R.V.I.S. ever
attributes "you promised X", it must show the transcript span and the speaker-confidence.

---

## 4. Speech generation

### 4.1 Options and measured properties

| Option | Type | First-audio latency | Quality | Licence / cost | Best for |
|---|---|---|---|---|---|
| Kokoro-82M | open weights | ~28–85 ms on GPU (hardware dependent); ~300 ms via server defaults; <1 s on Apple M-series CPU | Good; 54 preset voices, 8 languages, no cloning | Apache-2.0, self-host | Local, private, low-latency |
| Qwen3-TTS-0.6B | open weights | ~97 ms streaming claimed | Strong; 10 languages; 3-s cloning | Apache-2.0 `[U]` | Local multilingual with cloning |
| Orpheus-class expressive TTS | open weights | ~410 ms | More expressive | Apache-2.0 `[U]` | Narration, not turn-taking |
| Hosted TTS (vendor) | API | ~150–400 ms | Highest naturalness | Per-character | When quality dominates and egress is acceptable |
| Speech-to-speech models | API | Vendor-claimed low hundreds of ms; **field-measured 2–3 s turns** | Preserves prosody/emotion | Per-minute/token | Full conversational agents |

Sources: Kokoro/Qwen3-TTS claims from vendor model cards and community benchmarks `[V] [U]`;
[OpenAI pricing](https://developers.openai.com/api/docs/pricing) for `gpt-live-1` ($0.05/min) and
`gpt-realtime-2.1` (audio $32 in / $64 out per 1M tokens).

### 4.2 The voice-cloning problem (policy, not capability)

Cloning from ~3 seconds of audio is available in open weights. For a personal assistant, cloning the
**user's own voice** is a plausible feature with a sharp edge: consent, misuse, and legal exposure.
Recommendation: cloning is **opt-in, per-voice, revocable, and stored locally**; never clone a third
party's voice; and never ship a cloned voice to a hosted provider that may retain audio without an
explicit retention setting.

---

## 5. Speech-to-speech vs. cascaded pipeline

| Dimension | Cascaded (ASR → LLM → TTS) | Speech-to-speech |
|---|---|---|
| Latency floor | Sum of three stages; each adds 100–400 ms | Lower in principle; full-duplex is ~200 ms in research systems `[I]` |
| Prosody/emotion | Lost through text bottleneck | Preserved by design |
| Interruption handling | Requires explicit barge-in logic and cancellable TTS | Native in duplex designs (parallel streams) |
| Debuggability | High — you can log text at each stage | Low — audio in, audio out |
| Tool use | Natural (text model calls tools) | Vendor-dependent; async function calling is now default in at least one realtime API `[V]` |
| Privacy | Each stage can be local or hosted independently | Usually hosted end-to-end |
| Cost | Composable, cacheable | Per-minute/token, often higher |
| Failure modes | ASR errors, TTS mispronunciation | Hard to audit; harder to correct a mistake |

**Evidence:** Moshi demonstrates that a full-duplex speech-text model removes explicit turn
segmentation and handles overlap/interruption by construction, with 160 ms theoretical / 200 ms
practical latency `[I]` ([arXiv:2410.00037](https://arxiv.org/abs/2410.00037)). This is the design
direction that solves the *real* problem (turn-taking), not just the latency number.

**Recommendation (Plausible):** start with a **cascaded pipeline for privacy and debuggability**, and
adopt speech-to-speech for the interactive surface where turn-taking quality is the product. Do not
adopt speech-to-speech for tasks that mutate the user's data — auditability matters more there.

---

## 6. Interruption handling, turn-taking, and streaming

**Established requirements** (from Phase 1 T5 and general interactive-systems practice):

1. **Barge-in**: the user can speak while the assistant is speaking; playback stops within ~100 ms.
2. **Cancellation**: an interrupted generation must be aborted server-side, not just muted locally
   (otherwise you pay for tokens the user never hears and the transcript diverges).
3. **Partial-intent handling**: "actually, no—" must be treated as a correction, not a new request.
4. **Graceful backchannel**: brief acknowledgements while a tool runs, so silence is not mistaken for
   a hang (Phase 1 §6: progress update ≤10 s).
5. **Reconnect**: session resumption after a network drop without repeating the last sentence.

**Metrics:** barge-in stop latency, false-barge-in rate (assistant stops when it should not),
post-interruption coherence (does the assistant resume the right thread), and abandoned-turn rate.

**Unproven:** that any specific vendor's interruption handling meets these under real network
conditions; the field reports of long-session degradation suggest otherwise.

---

## 7. Image, document, and video understanding

### 7.1 What is established

- Frontier models accept text, image, video, audio, and PDF input (e.g. Gemini 4 Argon:
  text+image+video+audio+PDF → text, 1M unified window `[V]`
  ([docs](https://ai.google.dev/gemini-api/docs/latest-model))).
- Open weights with vision exist at workstation scale: Qwen3.8-27B (dense 27B, vision encoder,
  Apache-2.0) `[V]` ([card](https://huggingface.co/Qwen/Qwen3.8-27B)).
- Document extraction quality is **data- and post-training-dependent**, not implied by "vision
  capability". Table and layout understanding must be tested per document class.

### 7.2 A verification warning: video benchmark numbers

The Gemini 4 Argon model card reports **LVBench 91.7%** `[V]`. The LVBench paper (2024) reports human
annotators at 94.4% and contemporaneous models around 25–33% on the same named benchmark `[I]`
([arXiv:2406.08035](https://arxiv.org/abs/2406.08035) as summarised in
[review coverage](https://pith.science/paper/2406.08035)). Two years of progress could explain a
large gain, but a jump to near-human on "extreme long video understanding" is extraordinary and, in
this survey, **unverified**. Possible explanations include a different metric, subset, frame budget, or
an updated benchmark version — all of which change what the number means.

**Rule adopted:** a benchmark name without its version, harness, and metric is not a measurement.
Before citing LVBench (or Vals Index, FrontierSWE, DeepSWE, GraphWalks) in any decision, obtain the
harness description.

### 7.3 Practical guidance for J.A.R.V.I.S. documents

| Document class | Approach | Why |
|---|---|---|
| Digital PDF (text layer) | Extract text + layout; **do not** use vision | Cheaper, more accurate, page spans for provenance |
| Scanned PDF / photo of a page | Vision model with region-level citation; OCR fallback | Vision handles layout; OCR gives cheap cross-check |
| Tables / statements | Structured extraction with schema + arithmetic cross-check (sums reconcile) | Numbers are the damage vector |
| Screenshots / UI | Vision + computer-use tools | Coordinate groundings are model-specific |
| Long video | Chapterised transcript + selective frame sampling | Full-frame processing is prohibitively expensive |

**Provisional targets** (from Phase 1): ≥98% field accuracy on digital PDFs, ≥90% on scans, with **no
silent failure** — uncertain fields must be flagged for confirmation.

---

## 8. Multimodal tool use

- **Screen understanding / computer use** is now a first-class tool surface (all surveyed frontier
  providers expose "computer use" `[V]`), and is the highest-risk capability: it can click anything.
  Phase 9 requires sandboxing, allowlisted applications, and per-action confirmation.
- **Camera/photo input** for a personal assistant (e.g. "what is this letter?") is a document task in
  disguise; route it to the document pipeline, not to a general vision query.
- **Audio-driven actions** (voicemail → calendar) must always confirm the parsed event fields (T5/T6).
  ASR entity accuracy (§3.3) is never good enough to write silently.

---

## 9. Privacy and hardware implications: local vs hosted, per stage

| Stage | Local option | Local feasibility | Hosted egress | Recommendation |
|---|---|---|---|---|
| Wake word / VAD | Yes (small models) | Trivial | Avoid | **Always local** |
| ASR | Whisper large-v3 (1.55B), Parakeet 0.6B | GPU or fast CPU; Parakeet is ~20× faster | Audio leaves device | **Local by default**; hosted only if quality demands |
| Diarisation | pyannote 3.1 | GPU (T4/A10 class), batch | Audio leaves device | Local for stored audio |
| LLM reasoning | 27B (Qwen3.8) / 117B (gpt-oss-120b) | 27B workstation-class after quantisation; 120B needs 80 GB | Text leaves device | Local for private classes; hosted for hard tasks |
| TTS | Kokoro-82M, Qwen3-TTS-0.6B | CPU (Kokoro) / small GPU | Text leaves device | **Local for private text** |
| Speech-to-speech | Open duplex research models; quality gap to hosted | Real but behind | Audio leaves device | Hosted for interactivity; local for sensitive content |
| Vision | Qwen3.8-27B-class | GPU | Images leave device | Local for identity documents; hosted for general |

**Established trade-off:** local processing removes egress and network latency but costs hardware,
electricity, and maintenance, and the quality ceiling is lower than hosted frontier models (Phase 2).
**Unproven:** that a local stack meets Phase 1's latency envelope on the user's actual hardware (Q8).

**Hard privacy rules regardless of choice:**

1. Audio buffers are never written to disk unless the user opts in.
2. Transient audio is discarded after transcription; retention is a per-user setting.
3. Identity documents (passports, IDs) are processed locally if at all.
4. Every hosted call logs: purpose, byte count, destination, and which data class was included.

---

## 10. Experiment plan (Phase 6)

| ID | Experiment | Metrics | Pass condition |
|---|---|---|---|
| E-6.1 | Full-path voice latency instrumentation | Stage-level p50/p95/p99; residual | ≥90% of turns attributable to a stage |
| E-6.2 | Cascade vs speech-to-speech on 30 scripted dialogues | Round-trip latency, barge-in stop latency, task success, cost | One configuration within 800 ms p50 at ≤1.5× cost |
| E-6.3 | ASR bake-off on the user's own audio (clean/noisy, 2 languages) | WER, critical-entity error rate, RTFx | Best model ≤8% WER clean, ≤15% noisy, entity error ≤1% with confirmation fallback |
| E-6.4 | Long-session drift (20 min continuous) | Turn latency by minute bucket; transcript divergence | No monotone latency growth >50% |
| E-6.5 | Document extraction across 4 classes | Field accuracy, silent-failure rate | ≥98% digital, ≥90% scanned, 0 silent failures |
| E-6.6 | Video chapterisation on 3 long videos | Answer accuracy vs. manual gold; cost/minute | ≥80% accuracy at ≤$0.10/min |
| E-6.7 | Local-only voice stack on user hardware | Round-trip latency, WER, CPU/GPU load, battery | Meets envelope with no egress |
| E-6.8 | Barge-in under realistic noise | False-barge-in rate, stop latency | ≤5% false barge-in, ≤150 ms stop |

---

## 11. Recommended multimodal architecture

```
        ┌──────────── always local ────────────┐
mic ───►│ VAD/wake → ASR (Parakeet/Whisper)    │
        │   → diarisation (if multi-speaker)   │
        └───────────────┬──────────────────────┘
                        ▼
                 text + trust labels + timestamps
                        │
                        ▼
        ┌────────────── coordinator (Phase 4) ──────────────┐
        │  entity confirmation for any write               │
        │  local model for sensitive classes                │
        │  hosted model for hard tasks (logged egress)      │
        └───────────────┬───────────────────────────────────┘
                        ▼
        ┌──────────── local TTS (Kokoro/Qwen3-TTS) ────────┐
        │  streaming, cancellable on barge-in               │
        └───────────────────────────────────────────────────┘
```

Design notes:

- **Start cascaded, local-first.** It is debuggable, private, and cheap; the latency penalty is
  measurable and tunable (§2).
- **Speech-to-speech is a separate, optional interactive surface**, not the substrate for actions.
- **Confirm entities, not intent.** The user's intent was already spoken; the numbers are what get
  corrupted.
- **Keep the audio path cancellable end-to-end** (server-side abort), or you pay for phantom speech.

---

## 12. Findings classification

**Established**
- English ASR is commodity: ~6.3% vs ~7.4% average WER for the leading open models, with ~20×
  throughput difference `[I]`.
- Long-form speech is materially harder than short clips for every surveyed ASR model `[I]`.
- Diarisation is usable for labelling, not for automatic attribution of commitments (≈11–20% DER) `[U]`.
- Full-duplex speech models can reach ~200 ms practical latency and handle overlap by design `[I]`.
- Vision/document capability exists widely, but document accuracy is not implied by "multimodal".

**Plausible**
- A local cascaded voice stack (Parakeet + small LLM + Kokoro) meets an 800 ms p50 target on
  workstation hardware.
- Hosted speech-to-speech, in field conditions, delivers 2–3 s turns rather than sub-second.
- Entity read-back confirmation eliminates the majority of ASR-induced write errors.

**Unproven**
- That any vendor's interruption handling survives 20-minute sessions without degradation.
- That video benchmarks at "near-human" scores (e.g. LVBench 91.7% in a model card) measure the same
  task as the published benchmark.
- That 97 ms TTS first-audio claims hold outside the vendor's hardware and chunking.
- That local voice cloning can be shipped without a consent/abuse framework (policy, not capability).

---

## 13. What was learned, what remains uncertain, and the next research step

**What was learned.** The voice problem is a *systems* problem: endpointing, cancellation, entity
accuracy, and session-length drift dominate the user's perception, while model token speed explains
only part of the path. Recognition is commoditised (choose on language coverage and licence);
generation is cheap and fast locally; full-duplex is the architecturally right answer for turn-taking
but the weakest for auditability. Vision for documents should be routed by document class, with text
extraction preferred wherever a text layer exists.

**What remains uncertain.** Which latency number is real on the user's network and hardware (the
published range spans 200 ms to 6 s for "voice agents"); how much of the hosted realtime advantage
survives privacy constraints; whether diarisation is needed at all for a single-user assistant; and
whether the near-human video benchmark figures can be reproduced.

**Next research step.** Phase 7: turn these latency stages into a serving and compute model —
TTFT/ITL, caching, batching, quantisation, local vs hosted, and cost per successfully completed task —
so that the envelopes in Phase 1 become engineering targets with measured budgets rather than
aspirations.
