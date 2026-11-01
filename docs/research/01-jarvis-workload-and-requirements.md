# Phase 1 — J.A.R.V.I.S. Target Workload and Requirements

- **Status:** published (v0.3, open for review)
- **Published:** 2026-11-01
- **Sources checked:** 2026-11-01
- **Related:** [Index](README.md) · [Phase 2 — Model landscape](02-model-landscape-and-selection.md)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** (defined in the [index](README.md))
- **Provenance tags:** `[V]` vendor-reported · `[I]` independent · `[U]` unverified, re-check before use

---

## 0. Why this document exists

Every later phase (model choice, orchestration, memory, voice, inference, security) is only
defensible relative to a workload. Without a fixed target, "better model" and "more agents" are
unfalsifiable claims, and benchmark tables become a substitute for engineering.

This document therefore does three things:

1. Defines **representative task families** for J.A.R.V.I.S. (§4).
2. Defines **measurable success criteria** for each family across quality, completion, latency,
   reliability, privacy, and cost (§5–§7).
3. Records what we **do not know about the user's requirements** instead of inventing them (§9).

It deliberately does **not** choose a model, size, provider, or hardware. Those are Phase 2 and
Phase 7 decisions that must be driven by the measurements defined here.

### 0.1 Conventions used throughout

- An **[ASSUMPTION]** tag marks a requirement we invented for planning purposes. Assumptions are
  expected to be wrong somewhere; §9 lists them as questions for the user.
- A number labelled **provisional target** is a starting threshold for an experiment, not a promise
  to the user.
- Metric definitions are normalized in §5 so that Phase 8 can implement them literally.

---

## 1. Scope of the system under study

J.A.R.V.I.S. is scoped as a **personal AI system** with these observable behaviours, taken from the
project mission:

| Capability | Observable behaviour |
|---|---|
| Understand | Parse an ambiguous personal goal into an explicit, checkable statement of intent |
| Research | Retrieve current external information and attribute it to sources |
| Plan | Decompose a goal into ordered steps and track execution state |
| Remember | Persist, update, contradict, and forget personal context on request |
| Perceive | Work with text, documents, images, audio, and live voice |
| Act | Use approved applications and APIs under explicit permission |
| Verify | Check outcomes before claiming completion; recover from errors |
| Improve | Propose changes and test them against a measured baseline |

**Established** (from the mission brief, i.e. project intent rather than external evidence):
the system is expected to work in a personal, single-principal context, where the user is the
authority on their own data and approvals.

**Out of scope for the research phase:** multi-tenant SaaS operation, autonomous unattended
deployment of untested changes, and training a new foundation model. The last is explicitly gated by
the brief: it may only be recommended if evidence shows existing models plus system-level
improvements are inadequate.

---

## 2. Method

1. **Enumerate task families** from the mission statement, then check them against published task
   suites to make sure each family has a measurement instrument that is not invented by us.
2. **For each family**, state: the user-visible outcome, a concrete example, the inputs and outputs,
   the dominant failure modes observed in the literature, and metrics with provisional targets.
3. **Prefer existing instruments** where they measure the same construct, and state their known
   limitations. Instruments used in this document:
   - τ²-bench for tool-using conversational agents in dual-control settings, including the `pass^k`
     reliability metric [I] — Barres et al., *τ²-Bench: Evaluating Conversational Agents in a
     Dual-Control Environment*, [arXiv:2506.07982](https://arxiv.org/abs/2506.07982) (submitted
     2025-06-09; checked 2026-11-01).
   - Terminal-Bench 2.0 for long-horizon terminal/agent work, 89 human-written tasks with verifier
     tests; the paper reports frontier agents below 65% and smaller models near 15% [I] — ICLR 2026
     proceedings paper, [PDF](https://proceedings.iclr.cc/paper_files/paper/2026/file/444a3737adaee10d86ad2ef5f74468e6-Paper-Conference.pdf)
     and the audited leaderboard at [tbench.ai](https://www.tbench.ai/leaderboard/terminal-bench/2.0)
     (checked 2026-11-01).
   - LongMemEval for long-term memory (extraction, multi-session reasoning, temporal reasoning,
     knowledge updates, abstention), where the paper reports ~30% accuracy drops for commercial
     assistants and long-context LLMs on sustained interactions [I] —
     [arXiv:2410.10813](https://arxiv.org/abs/2410.10813) (submitted 2024-10-14; checked 2026-11-01).
   - AgentDojo / Agent Security Bench for adversarial robustness of tool-using agents [I] —
     [arXiv:2503.18813](https://arxiv.org/abs/2503.18813) (CaMeL, v2 2025-06-24) and
     [arXiv:2410.02644](https://arxiv.org/abs/2410.02644) (ASB, ICLR 2025; highest average attack
     success rate 84.30% across 13 backbones, defenses limited).
   - Cost-controlled agent evaluation as the default comparison rule [I] — Kapoor et al.,
     *AI Agents That Matter*, [arXiv:2407.01502](https://arxiv.org/abs/2407.01502) (2024-07-01).
4. **Flag every requirement we cannot ground** in either the mission brief or a cited source.

### 2.1 A note on what benchmarks are for here

Benchmark scores are **instruments, not predictions**. Three reasons, all documented:

- **Contamination is structural and biased upward.** The GSM1K study showed the worst-overfitting
  model family losing 13 percentage points on fresh, matched problems [I]
  ([arXiv:2405.00332](https://arxiv.org/abs/2405.00332)); a 2026 framework for auditing bug-fixing
  benchmarks reports that removing contaminated samples drops top-10 resolution rates by more than
  20% on medium-difficulty tasks on SWE-bench Verified, and by up to 55.9% on Multi-SWE-bench [I]
  ([arXiv:2610.19447](https://arxiv.org/abs/2610.19447), submitted 2026).
- **Harness dominates the score.** Terminal-Bench 2.0 results differ substantially by agent scaffold
  for the same model, which is why the official board audits configurations and publishes
  trajectories [I] ([tbench.ai](https://www.tbench.ai/leaderboard/terminal-bench/2.0)).
- **Reliability is not captured by single-shot accuracy.** τ²-bench's `pass^k` shows one-shot
  success overstating consistency; a task family J.A.R.V.I.S. cares about (repeatable execution)
  requires repeated trials, not one.

Therefore every requirement in this document is expressed as a metric **J.A.R.V.I.S. must measure on
its own workload**, with the public benchmark used only to (a) sanity-check the harness and (b)
compare candidate configurations under identical conditions.

---

## 3. Reference user scenarios

These scenarios are the concrete anchor for all metrics. They are written as a user would speak
them, then given a checkable outcome. [ASSUMPTION] tags mark invented details.

| ID | Scenario (user-voice) | Checkable outcome | Failure that matters most |
|---|---|---|---|
| S1 | "Why did my laptop battery drop 40% overnight, and what should I change?" | Explanation grounded in retrieved evidence + a reversible recommended action | Confident wrong diagnosis; unexplained action taken |
| S2 | "Find current evidence on X, summarise both sides, cite sources" | Every claim traces to a fetched source; contradictions surfaced | Fabricated or stale citation |
| S3 | "Plan my move to a new city over six weeks" | Ordered plan with dependencies, dates, and progress state that survives restarts | Lost state; plan silently diverges from reality |
| S4 | "Remember that I dislike phone calls before 10am" | Preference stored, applied later, correctable, deletable | Stale or wrongly generalised preference |
| S5 | "Read this 60-page PDF and tell me the payment terms" | Correct extraction with page-level provenance | Missing clause presented as absent |
| S6 | "Take this voicemail and put the appointment in my calendar" | Transcription → structured event → approved write | Wrong date/time written without confirmation |
| S7 | "Draft and send the reply, but ask me before sending" | Draft prepared, approval requested, send only after approval | Send without approval, or claim sent when not sent |

[ASSUMPTION] A1: The user has one primary account/persona and one personal data store. Multi-user
sharing is not required in v1.
[ASSUMPTION] A2: The user is willing to grant scoped access to at least calendar, email, and file
storage, but not blanket account credentials.
[ASSUMPTION] A3: Voice is a first-class interface (not a transcription accessory), used in short
interactive turns and occasionally for long dictation.
[ASSUMPTION] A4: Some tasks legitimately run for minutes to hours; interactive chat latency is not
acceptable for those, and progress reporting is expected instead.
[ASSUMPTION] A5: The user cares about cost but treats it as a budget, not a minimand; a predictable
monthly ceiling matters more than the cheapest possible per-task cost.

---

## 4. Task families

Seven families, each with a distinct success construct. Cross-cutting requirements (§7) apply to all.

### T1 — Answer and explain decisions

- **Outcome:** a correct, appropriately-hedged explanation the user can act on, including *why* the
  system proposed something.
- **Inputs:** conversational text, attached context, memory records, prior tool results.
- **Outputs:** prose, optionally with structured rationale ("I chose A because B; I did not check C").
- **Dominant failure modes:** confident wrong answers; unstated assumptions presented as fact;
  explanations that rationalise a decision made by a different component; sycophancy under pressure.
  The last is measured by vendor honesty evaluations (e.g. MASK-style pressure tests reported in
  Anthropic's model report [V]) and must be re-tested on our workload.
- **Metrics:** accuracy on a held-out personal Q&A set; *calibration* (does stated confidence track
  correctness); rationale faithfulness (human spot-check against the actual tool trace); refusal
  appropriateness.
- **Provisional targets:** ≥90% correct on the internal set where the answer is verifiable;
  ≤2% "confidently wrong" rate; rationale faithfulness ≥95% on 50 audited samples.

### T2 — Research current information with citations

- **Outcome:** a cited synthesis whose claims survive spot-checking, with recency stated.
- **Inputs:** query, retrieval budget (time and cost), allowed sources.
- **Outputs:** answer + claim→source mapping + retrieval trace (queries issued, pages fetched).
- **Dominant failure modes:** fabricated citations; citing a page that does not contain the claim;
  stale information presented as current; over-reliance on one source; injection from fetched
  content (see T7/§7).
- **Metrics:** *citation precision* (claims with a supporting span ÷ claims with a citation);
  *citation recall* (claims requiring support that have one ÷ all such claims); *freshness*
  (distribution of source dates vs. the question's recency requirement); *answer accuracy* judged
  against a gold set; *cost per accepted answer*.
- **Instruments:** no single accepted benchmark; build an internal set with dated gold sources, and
  borrow the practice of auditing trajectories from Terminal-Bench's published-trajectory model [I].
- **Provisional targets:** citation precision ≥95%; citation recall ≥85%; zero uncited
  time-sensitive claims; median end-to-end ≤60 s for a 5-source synthesis.

### T3 — Plan a multi-step goal and track progress

- **Outcome:** an executable plan whose state is durable, whose steps are verifiable, and whose
  completion claim is true.
- **Inputs:** goal, constraints, deadline, available tools, prior plan state.
- **Outputs:** plan artifact (steps, dependencies, acceptance checks) + execution log + status.
- **Dominant failure modes:** plan drift (executing steps that no longer serve the goal); unbounded
  loops; silently skipping failed steps; "done" claimed without verification; context loss across
  restarts.
- **Metrics:** *end-to-end task success* (verifier-checked, not self-reported); *step-verification
  coverage* (% of steps with an executed check); *state durability* (success after a forced restart
  mid-task); *loop bound* (steps executed ≤ declared budget); *truthfulness of completion*
  (§5.4).
- **Instruments:** Terminal-Bench 2.0-style verifier tasks [I]; τ²-bench `pass^k` for reliability [I].
- **Provisional targets:** verifier-checked success ≥70% on a 30-task internal suite; **zero**
  false completion claims on the audited subset; 100% of completed tasks have a check per declared
  acceptance criterion.

### T4 — Remember, update, and retrieve personal context

- **Outcome:** the right fact, at the right time, with provenance, correctable and deletable.
- **Inputs:** conversation turns, explicit "remember this" commands, documents, calendar/email events.
- **Outputs:** recalled statements with source reference; memory diffs on update; deletion
  confirmations.
- **Dominant failure modes:** recall of a superseded fact; over-generalisation ("prefers silence"
  from "was busy once"); retrieval that cannot show provenance; silent memory poisoning from
  untrusted content; inability to forget.
- **Metrics:** LongMemEval-style five abilities — extraction, multi-session reasoning, temporal
  reasoning, **knowledge update**, **abstention** [I]; plus *provenance coverage* (% of recalled
  facts with a resolvable source) and *deletion completeness* (recalled-after-delete = 0).
- **Provisional targets:** knowledge-update accuracy ≥90%; abstention (correctly saying "I don't
  have that") ≥90%; provenance coverage 100%; deletion completeness 100% verified within 24 h.

### T5 — Work with documents, images, audio, and voice

- **Outcome:** correct understanding across modalities, with modality-specific evidence.
- **Inputs:** PDFs, screenshots, photos, recorded audio, live microphone.
- **Outputs:** extracted facts with page/region/timestamp provenance; transcripts; spoken responses.
- **Dominant failure modes:** OCR/handwriting errors silently accepted; table/layout misreads
  (numbers swapped); hallucinated content in images; ASR errors on names/numbers; latency in voice
  perceived as rudeness; missing barge-in handling.
- **Metrics:** field-level extraction accuracy by document class; ASR word error rate (WER) on the
  user's own audio, not on leaderboard averages; name/number accuracy separately (because those
  cause downstream damage); voice round-trip latency (see §6); interruption recovery rate.
- **Provisional targets:** ≥98% field accuracy on digital PDFs, ≥90% on scans, with *no* silent
  failure (uncertain fields must be flagged); WER ≤8% on the user's clean speech and ≤15% on noisy;
  zero unconfirmed writes derived from audio.

### T6 — Use approved applications and APIs

- **Outcome:** the intended side effect occurs exactly once, with the intended arguments, under the
  granted permission, and can be undone or explained.
- **Inputs:** tool catalogue, user intent, credentials scoped by the app layer (never by the model).
- **Outputs:** tool calls, results, audit record.
- **Dominant failure modes:** wrong tool selected; correct tool, wrong argument (date off by one,
  wrong recipient); duplicate side effects on retry; over-broad permission use; trusting
  model-produced arguments without validation. The last is a design requirement, not a model
  property: model-generated tool arguments are **untrusted input** that application code must
  validate (see Phase 9).
- **Metrics:** tool-selection accuracy; argument exactness (incl. unit/format/timezone); *side-effect
  idempotency* (exactly-once under retry and resume); authorization violations (must be 0);
  time-to-approval for gated actions.
- **Instruments:** τ²-bench for policy-constrained tool use and dual control [I]; internal golden
  tool tests per integration.
- **Provisional targets:** argument exactness ≥99% on the golden set; duplicates 0; authorization
  violations 0; ≥95% of gated actions have a human decision logged.

### T7 — Recover from errors and report completion truthfully

- **Outcome:** when something fails, the user learns that it failed, what was attempted, and what
  remains — with no optimistic inference.
- **Inputs:** tool errors, timeouts, partial results, validation failures, contradictory evidence.
- **Outputs:** structured status (done / partially done / not done), error explanation, retry or
  replan decision, and the evidence used to decide.
- **Dominant failure modes:** claiming success because a call returned HTTP 200; retry storms;
  hiding partial failure; "I've updated your calendar" when only a draft was created; treating
  untrusted tool output as instructions.
- **Metrics:** *completion truthfulness* = (claims verified as true) ÷ (all completion claims);
  *false-success rate* (claims of success where the verifier disagrees) — target **0** on audited
  runs; recovery rate (success after injecting a known fault); mean retries per task; p99 task
  duration under fault injection.
- **Instruments:** fault injection harness (Phase 8) plus AgentDojo-style adversarial tool outputs [I].

---

## 5. Metric definitions (normative)

These definitions are the contract between this document and Phase 8's harness.

### 5.1 Quality and groundedness

| Metric | Definition | Why it is defined this way |
|---|---|---|
| Task success (end-to-end) | Verifier-checked outcome ÷ tasks attempted. "Verifier" means code, state comparison, or a human-labelled gold answer — never the model's own claim. | Self-reported success is the failure mode in T7. τ²-bench uses world-state comparison for the same reason [I]. |
| Citation precision | Claims with a supporting span in a retrieved source ÷ claims carrying a citation. | Detects fabricated or misplaced citations, the most damaging research error. |
| Groundedness | Claims supported by retrieved evidence ÷ all verifiable claims, regardless of citation. | A model can be accurate and still ungrounded (parametric recall); we want evidence, not memory. |
| Abstention correctness | Cases where "I don't know / I don't have that" is correct ÷ cases where abstention was offered. | LongMemEval treats abstention as a first-class ability [I]. |
| Rationale faithfulness | Audited samples where the stated reason matches the actual trace ÷ audited samples. | Explanations must describe the system, not a reconstruction. |

### 5.2 Completion and reliability

| Metric | Definition |
|---|---|
| Completion rate | Tasks reaching a verified terminal state (success or declared failure) ÷ tasks started. |
| `pass^k` | All k independent attempts succeed (reliability), as in τ-bench/τ²-bench [I]. Report k=1 and k=4. |
| Durability | Success rate when the process is killed and restarted at a random step. |
| Fault recovery | Success rate with injected faults (tool error, timeout, malformed output, stale data). |
| Loop bound adherence | Runs that stay within their declared step/time/cost budget ÷ runs. |

`pass^k` and `pass@k` are not interchangeable: `pass@k` is best-of-k, `pass^k` is all-of-k. For a
personal assistant, `pass^4` is the honest number.

### 5.3 Latency

Latency is reported as a distribution, never a mean alone.

| Metric | Definition | Where it applies |
|---|---|---|
| TTFT | Request sent → first useful token or first audio frame | All interactive paths |
| ITL | Inter-token latency distribution during decode | Text streaming |
| Voice round-trip | End of user speech → audible response onset | T5 voice |
| Task time p50/p95/p99 | Start → verified terminal state | T2, T3, T6 |
| Perceived progress | Time to first user-visible status update | Long tasks (A4) |

### 5.4 Truthfulness (T7)

- **Completion truthfulness** = verified-true claims ÷ completion claims.
- **False-success rate** = claims of success where the verifier disagrees ÷ all audited claims.
  Target: **0**. Any nonzero value is a release blocker (Phase 8), because the user cannot calibrate
  trust if the system lies about what it did.

### 5.5 Privacy and security metrics

| Metric | Definition |
|---|---|
| Data egress events | Count of bytes/records leaving the user's trust boundary per task, by destination class. |
| Unauthorized egress | Egress to a destination not permitted by policy. Target 0. |
| Secret exposure | Secrets visible in model context, logs, or outputs. Target 0. |
| Injection resistance | Attack success rate on a fixed adversarial suite (AgentDojo-style tasks) [I]. |
| Approval fidelity | Consequential actions executed without required approval. Target 0. |

### 5.6 Cost

- **Cost per successfully completed task** = (model + tool + infrastructure cost) ÷ verified
  successes. Failed attempts are charged to the numerator; this is the definition that makes
  retries and multi-agent fan-out honest.
- Also report the **cost distribution** (p50/p95) and the **budget adherence** (tasks whose cost
  stayed under the declared cap).
- Rationale: cost-controlled comparison is the standing recommendation for agent evaluation [I]
  (Kapoor et al., [arXiv:2407.01502](https://arxiv.org/abs/2407.01502)), because accuracy can always
  be bought with more calls.

---

## 6. Latency and cost envelopes

[ASSUMPTION] A6: The envelopes below are starting hypotheses. They must be ratified by the user,
because they determine architecture more strongly than model choice does.

| Interaction class | Envelope (provisional) | Consequence if missed |
|---|---|---|
| Conversational turn (text) | TTFT p50 ≤ 1.0 s, p95 ≤ 2.5 s | User stops waiting; turn-taking breaks |
| Conversational turn (voice) | Round-trip p50 ≤ 800 ms, p95 ≤ 1.4 s | Speech overlap, perceived rudeness |
| Simple tool action (calendar read/write) | ≤ 5 s including validation | Approval flow feels unusable |
| Research synthesis (5 sources) | ≤ 60 s with progress updates | User assumes failure |
| Long task (plan execution) | Progress update ≤ 10 s, then ≤ 60 s cadence | Silent long tasks are indistinguishable from hangs |
| Cost per simple turn | ≤ $0.02 | Budget ceiling (A5) becomes meaningless |
| Cost per research task | ≤ $0.50 | Same |
| Monthly ceiling | User-declared | — |

Two cautions attached to these numbers:

- **Voice latency evidence is inconsistent in public reports.** Vendor material describes
  speech-to-speech serving in the low hundreds of milliseconds, while independent field
  measurements of the same classes of system report median turn latencies of seconds and rising with
  session length. This discrepancy is exactly why Phase 6 and Phase 7 must measure the whole voice
  path on our hardware and network rather than quote a vendor figure. (Reported ranges are `[U]`
  until we measure them; see Phase 6.)
- **Provider-side latency improvements are version-specific.** For example, an OpenAI Realtime
  release note claims p95 latency reductions from caching `[V] [U]`; that claim does not transfer to
  a different provider, region, or payload shape.

---

## 7. Cross-cutting requirements

### 7.1 Privacy and trust boundaries

- **Local-first where feasible.** Transient audio and images should be processed on-device when
  quality permits; anything leaving the device must be an explicit, logged decision. (Feasibility is
  a Phase 6/7 question, not an assumption to assert here.)
- **Data classification:** `public` · `personal` · `sensitive` (identity documents, health, financial)
  · `secret` (credentials). Policies are attached to classes, not to individual prompts.
- **Minimisation:** send the smallest sufficient excerpt. A 60-page PDF question should not ship 60
  pages to a provider by default.
- **Retention:** every stored memory record has a source, a timestamp, and a retention rule.
- **Deletion and export:** deletion must be verifiable (post-delete retrieval test); export must be
  machine-readable.

### 7.2 Permissions and approvals

- Tools are allowed per-tool, per-scope, per-duration, not globally.
- Consequential actions (send, pay, delete, share, book) require explicit confirmation unless the
  user has pre-authorised a narrow class with limits.
- The system must be able to *explain what it is about to do* before doing it, in one sentence,
  using the actual arguments.

### 7.3 Honesty contract

The system never states completion without a check, never silently drops a failed step, and never
presents a guess as a retrieved fact. This is a behavioural requirement measured in §5.4, not a
character trait attributed to a model.

### 7.4 Improvement discipline

"Self-improvement" means: propose a change, define a metric, test against a frozen baseline, keep it
only if the improvement is statistically credible and does not regress safety/latency/cost. No
autonomous deployment of untested changes (Phase 8 defines the mechanics).

---

## 8. What "done" looks like for this phase

Phase 1 is complete when:

- [x] Seven task families with user-visible outcomes and dominant failure modes.
- [x] Normative metric definitions for quality, completion, latency, reliability, privacy, cost.
- [x] Provisional acceptance targets, clearly labelled as targets.
- [x] Cross-cutting privacy, permission, honesty, and improvement requirements.
- [x] An explicit list of unresolved requirements (§9) rather than invented answers.
- [ ] **User ratification of §9** (blocking for Phase 8's baseline design).

---

## 9. Unresolved requirements (questions, not assumptions)

These must be answered by the user. Until then, the corresponding behaviour is a guess.

| # | Question | Why it blocks a decision |
|---|---|---|
| Q1 | Which surface is primary in v1: desktop app, mobile, or voice-first device? | Determines local compute budget and whether streaming voice is a hard requirement. |
| Q2 | Is fully local operation required for any data class? Which classes? | Decides whether hosted frontier models are admissible at all, and changes the whole Phase 2 shortlist. |
| Q3 | What monthly cost ceiling is acceptable, and is it fixed or usage-proportional? | Sets the routing strategy (single premium model vs. tiered routing) in Phase 4/7. |
| Q4 | Which third-party accounts must be writable in v1 (calendar, email, files, payments)? | Drives the security model and the approval design; payment authority changes the risk class entirely. |
| Q5 | Must the system act while the user is offline or asleep? | Determines autonomy level, audit requirements, and whether approval can be asynchronous. |
| Q6 | What is the user's tolerance for being asked to approve actions (approval fatigue)? | Balances safety against completion rate; changes T6 targets. |
| Q7 | Which languages and accents must voice handle? | Changes the ASR shortlist (Whisper-family multilingual vs. English-optimised models). |
| Q8 | Is on-device inference on existing hardware a requirement, or is a dedicated machine acceptable? | Decides local vs. hosted split in Phase 7. |
| Q9 | What retention is expected for recordings and transcripts? | Memory and privacy design. |
| Q10 | Are there regulated data classes (health, legal, financial) that require a specific jurisdiction or processor? | May disqualify providers on legal grounds regardless of capability. |
| Q11 | What is the acceptable failure mode for irreversible actions — block, or ask? | Policy layer semantics. |
| Q12 | Does the user want to see reasoning traces, or only results and evidence? | Affects UX and how much we invest in monitorability (see Phase 3/9). |

**Established** (about the state of knowledge, not the user): none of Q1–Q12 has a defensible default
that can be inferred from public benchmarks. Every architecture document that answers them without
asking is guessing.

---

## 10. How this document can be falsified

This workload definition should change if:

- The user's actual tasks turn out to be dominated by one family (e.g. 80% short factual Q&A). Then
  the latency/cost envelopes for long tasks are over-engineered and should be de-emphasised.
- Voice proves to be a minority interface, which would remove the hardest latency constraint.
- Approval turns out to be the dominant cost (in user time), which would push design toward
  pre-authorised bounded autonomy and much heavier verification.
- Measured `pass^4` on simple tasks is already low, making reliability—not capability—the binding
  constraint, and reordering the whole roadmap.

Each of these is testable with the Phase 8 harness before any architecture is fixed.

---

## 11. Findings summary

**Established**
- Public instruments exist for tool use under policy constraints (τ²-bench, `pass^k`), long-horizon
  terminal work (Terminal-Bench 2.0), long-term memory (LongMemEval), and agent security
  (AgentDojo / ASB) — all with primary papers and, for Terminal-Bench, an audited leaderboard.
- Contamination and harness effects are large enough that leaderboard deltas cannot be read as
  capability differences for our workload [I].
- Cost-controlled, verifier-based evaluation is the only defensible comparison method for agent
  systems [I].
- Agent security is not solved by model quality: ASB reports up to 84.30% average attack success
  across 13 backbones with limited defense effectiveness [I].

**Plausible**
- That a single strong model plus a thin, verifiable tool layer meets T1–T7 for a personal user;
  supported by vendor claims about agentic tool use, not by independent evaluation on personal
  workloads.
- That the interactive latency envelope (800 ms voice) is achievable on hosted speech-to-speech
  within a normal home network, conditional on region and payload size.

**Unproven**
- That long tasks can be made *truthfully* completable at ≥70% by scaffolding alone, without
  verifier-specific engineering per task family.
- That memory requirements (updates, abstention, deletion) can be met by a single retrieval design
  across document, conversation, and event sources.
- That the provisional cost envelope is compatible with frontier-model pricing for the user's
  realistic daily volume.

---

## 12. What was learned, what remains uncertain, and the next research step

**What was learned.** The workload decomposes into seven families with genuinely different success
constructs. The binding constraints are honesty (T7), memory correctness (T4), and tool-argument
exactness (T6) — not raw reasoning quality. Public benchmarks can validate our harness but cannot
substitute for it, because contamination and scaffold effects are large and directional.

**What remains uncertain.** Twelve user requirements are unanswered (Q1–Q12), and at least six of
them change architecture more than any model choice would. Latency and cost envelopes are provisional
and untested on real hardware. Nothing in this document has yet been measured on J.A.R.V.I.S.;
it is a specification, not a result.

**Next research step.** Phase 2: survey the candidate model landscape against these seven families,
recording version, access method, modalities, context limits, cost, licence, documented weaknesses,
and independent evidence — and explicitly separating vendor-reported results from independent ones,
so that Phase 10 can choose experiments rather than favourites.
