# Phase 4 — Reasoning, Agents, and Orchestration

- **Status:** published (v0.2, open for review)
- **Published:** 2026-11-01
- **Sources checked:** 2026-11-01
- **Related:** [Index](README.md) · [Phase 1 — Workload](01-jarvis-workload-and-requirements.md) · [Phase 2 — Models](02-model-landscape-and-selection.md) · Phase 8 (evaluation), Phase 9 (security)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · **`[V]` vendor** · **`[I]` independent** · **`[U]` unverified**

> **Revision R1 (2026-11-01) — status of this document.** Retained as published. §9 (minimal architecture
> v0) remains the **control plane** of the revised design (S-2, S-7); planning, coordination and routing are
> now placed inside a five-layer architecture with a capability registry
> ([Phase 12](12-capability-integration-architecture.md)). Coordination stays opt-in and measured (S-9).

---

## 0. The question this phase answers

> **What is the simplest orchestration architecture that meets the measured workload — and what
> evidence would justify adding another agent, another model call, or another reasoning step?**

The default in 2026 tooling is to add complexity (planners, critics, subagents, debate). The default
in the evidence is the opposite: complexity must pay for itself in **end-to-end success per dollar**,
not in plausibility.

Two findings frame the whole document:

1. **Accuracy can always be bought.** Kapoor et al. show that repeatedly calling a model inflates
   accuracy while cost grows without bound, and that simple baselines (retry, warm-up) are
   Pareto-competitive with elaborate agent scaffolds on HumanEval at a fraction of the cost `[I]`
   ([arXiv:2407.01502](https://arxiv.org/abs/2407.01502), 2024-07-01).
2. **Reliability, not peak capability, is the binding constraint.** τ²-bench's `pass^k` (all k trials
   succeed) exposes that one-shot success overstates consistency in dual-control tool use `[I]`
   ([arXiv:2506.07982](https://arxiv.org/abs/2506.07982)).

Everything below is therefore written as "add a layer only if experiment E-x passes".

---

## 1. What "reasoning" is in these systems, and what it costs

### 1.1 Effort is now an explicit product parameter

Every major provider exposes a reasoning effort control, and it changes cost dramatically:

| Provider | Control | Evidence |
|---|---|---|
| OpenAI | `reasoning_effort` low → max (`none` on Terra/Luna) | [model catalogue](https://developers.openai.com/api/docs/models) `[V]` |
| Anthropic | adaptive thinking with per-model default effort (`high`/`medium`) | [models overview](https://platform.claude.com/docs/en/models/overview) `[V]` |
| Google | `thinking_level` low/medium/high (default medium) | [Gemini 4 Argon](https://ai.google.dev/gemini-api/docs/latest-model) `[V]` |
| Alibaba (Qwen3.8) | `reasoning_effort` xhigh/medium/low, xhigh default; `preserve_thinking` | [Qwen3.8 GitHub](https://github.com/QwenLM/Qwen3.8) `[V]` |
| Moonshot (K3) | multiple reasoning-effort levels trained via RL | [arXiv:2607.24653](https://arxiv.org/abs/2607.24653) `[V]` |

**Established:** thinking tokens are billed like output tokens (on the pricing pages surveyed), so
effort directly sets cost. A "max effort" call on a premium model can be 10–100× the cost of the same
question at low effort with a cheap model.

**Plausible:** effort levels are not monotone in *task* value — past some point extra thinking adds
latency and cost without improving verifier-checked success. This is exactly what an effort sweep in
Phase 8 must measure per task family.

### 1.2 Reasoning patterns and their evidence

| Pattern | Mechanism | Evidence | Where it helps | Where it fails |
|---|---|---|---|---|
| Chain-of-thought / extended thinking | More tokens before the answer | **Established** on reasoning benchmarks; effort dials are vendor evidence `[V]` | Math, code, multi-constraint planning | Simple tasks (wasted cost); faithfulness is not guaranteed |
| ReAct (reason+act interleaved) | Thought → tool call → observation loop | **Established** as a strong baseline [I] ([arXiv:2210.03629](https://arxiv.org/abs/2210.03629)) | Tool-using tasks | Loops, error compounding, no global plan |
| Plan-and-execute | Plan first, then run steps | **Plausible** [I] (reduces drift; adds replanning complexity) | Long, decomposable tasks | Plans go stale when reality changes |
| Reflection / self-critique | Model reviews and revises | **Mixed** [I] ([arXiv:2303.11366](https://arxiv.org/abs/2303.11366)) | Verifiable tasks | Self-assessment inflates confidence; no external signal ⇒ little gain |
| Tree/graph search | Explore multiple paths | **Established** but expensive [I] ([arXiv:2305.10601](https://arxiv.org/abs/2305.10601)) | Puzzles, constrained search | Cost blows up; ill-suited to side-effecting tools |
| Plan-and-solve without observation (ReWOO) | Compress observations to save tokens | **Plausible** [I] ([arXiv:2305.18323](https://arxiv.org/abs/2305.18323)) | Token-heavy tool loops | Brittle when a step's output changes the plan |
| Parallel tool calls | Fire independent calls together | **Established** engineering win [V]/[I] | Breadth, latency | Ordering/conflict bugs; rate limits |

**Key inference for J.A.R.V.I.S.:** the patterns that reliably help are those that add an **external
signal** (tool observation, test result, verifier) rather than another model opinion. Reflection
without a verifier is the weakest of the group.

---

## 2. Planning and task decomposition

### 2.1 What is established

- Decomposition improves success on tasks that exceed a single context window or that have
  independent sub-goals — this is the mechanism Anthropic attributes its research-system gains to
  (`[V]`, §5).
- Long-horizon execution fails predictably. Terminal-Bench 2.0 (89 human-written terminal tasks with
  verifier tests) reports frontier agents below 65% and smaller models around 15%, and publishes a
  failure taxonomy rather than only scores `[I]`
  ([ICLR 2026 paper](https://proceedings.iclr.cc/paper_files/paper/2026/file/444a3737adaee10d86ad2ef5f74468e6-Paper-Conference.pdf),
  [audited leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.0)).

### 2.2 The four plan failure modes to design against

1. **Plan drift** — steps continue after the goal has changed (user updates intent mid-run).
2. **Stale assumptions** — a step assumed a fact that a later step invalidated.
3. **Silent skip** — a failed step is skipped and the plan "completes".
4. **Unbounded search** — the agent keeps trying variations with no budget.

Each maps to a mechanical control, not a prompt: a plan object with acceptance criteria per step
(fixes 3), re-plan triggers on observation mismatch (fixes 1–2), and explicit step/time/cost budgets
(fixes 4). Phase 1's T3 metrics measure exactly these.

### 2.3 Plan representation (recommendation)

A durable, serialisable plan artifact — steps with `id`, `intent`, `tool`, `arguments`, `acceptance
check`, `status`, `evidence` — rather than a plan in prose. Rationale:

- It survives process restarts (T3 durability).
- It is auditable (Phase 9).
- It lets deterministic code decide "done", which is the only way to hit a **zero** false-success
  rate (T7).

---

## 3. Tool calling and structured outputs

### 3.1 Evidence

- τ²-bench formalises tool use in **dual-control** environments where the user can also act, and
  reports up to 25-point drops from solo to dual-control mode for frontier models, with `pass^4`
  falling 20–30 points below `pass^1` `[I]` ([arXiv:2506.07982](https://arxiv.org/abs/2506.07982);
  summary figures per the paper's abstract/analysis `[U]` for exact values).
- Agent Security Bench reports prompt-injection style attacks reaching **84.30% average attack
  success** across 13 backbones, with limited defense effectiveness `[I]`
  ([arXiv:2410.02644](https://arxiv.org/abs/2410.02644), ICLR 2025).
- Vendor tool surfaces are consistent across frontier models (functions, web search, file search,
  computer use) `[V]` ([OpenAI models](https://developers.openai.com/api/docs/models)).

### 3.2 Rules that follow (application-level, not model-level)

1. **Model output is untrusted input.** Every tool argument is validated against a schema and against
   business rules (date not in the past, recipient in allowlist, amount under cap) *before* execution.
2. **Separate decision from execution.** The agent proposes a call; a deterministic executor
   authorises, executes, and records it (this is the CaMeL-style split, generalised; CaMeL reports 77%
   of tasks solved with provable security vs 84% undefended in AgentDojo `[I]`
   ([arXiv:2503.18813](https://arxiv.org/abs/2503.18813))).
3. **Idempotency keys** for every side-effecting call, so retries and resumes cannot double-write
   (T6 exactness target).
4. **Dry-run / preview** for consequential tools: show the exact arguments in one sentence and require
   confirmation (T6 approval fidelity).
5. **Small, typed tools beat broad tools.** A `calendar.create_event(start, end, title, tz)` is easier
   to validate and to route than `calendar_api(request_json)`.
6. **Read-then-write discipline:** never write from a stale read; re-read immediately before
   mutation where the tool supports it.

### 3.3 Structured outputs

Structured/JSON-schema constrained decoding is widely available (`[V]` across the surveyed providers)
and reduces parse failures. It does **not** guarantee semantic validity: a schema-valid date can still
be the wrong date. Validation must therefore be layered (schema → business rules → confirmation).

---

## 4. Model routing and adaptive effort

### 4.1 Evidence

- **RouteLLM** trains routers from preference data and reports **>2× cost reduction** at ~95% of
  strong-model quality on MT-Bench, with **3.66×** savings in its best configuration, and router
  overhead below 0.4% of generation cost `[I]` ([arXiv:2406.18665](https://arxiv.org/abs/2406.18665)).
- Provider price ladders make routing feasible within a single vendor: OpenAI's input price spans
  $0.10 → $10 per 1M tokens (Luna → Astra), Anthropic $0.10 → $10 (Haiku → Fable), Google Argon
  $2 → $4 depending on date `[V]`.

### 4.2 Two routing designs, only one of which is likely right

| Design | How it decides | Weakness |
|---|---|---|
| **Query classifier** (RouteLLM-style) | Predicts difficulty from the request before any work | Distribution shift; personal tasks look easy but are high-stakes; cannot see tool results |
| **Cascade with verifier** | Try the cheap tier; escalate only if a verifier or the user says it failed | Needs a trustworthy verifier; latency of two attempts |

**Recommendation (Plausible, to be tested):** use the **cascade with verifier** as the primary
mechanism for consequential tasks (T3, T6), and a **classifier/heuristic** only for cheap,
low-stakes routing (T1, extraction). Rationale: the classifier cannot know that a "simple" calendar
request has a timezone trap, whereas a verifier can catch it after the fact.

### 4.3 Effort routing inside a model

Cheaper than cross-model routing when supported: same provider, fewer thinking tokens. The
experiment: sweep effort ∈ {min, default, max} per task family and plot verifier success vs cost.
Kill criterion for high effort: <2 percentage points success gain for >2× cost.

---

## 5. Sequential vs parallel work

### 5.1 The strongest evidence *for* parallelism (vendor, and honest about cost)

Anthropic's account of its research system reports `[V]`
([anthropic.com/research/multi-agent-research-system](https://www.anthropic.com/research/multi-agent-research-system)):

- A multi-agent system (Opus 4 lead + Sonnet 4 subagents) outperformed single-agent Opus 4 by
  **90.2%** on an internal research evaluation.
- In a BrowseComp analysis, three factors explained **95%** of performance variance; **token usage
  alone explained 80%**, with tool-call count and model choice the rest.
- Agents use ~**4×** chat tokens; multi-agent systems ~**15×**.
- The authors state the technique fits tasks with heavy parallelisation, information exceeding one
  context window, and many complex tools — and **not** tasks needing shared context or many
  inter-agent dependencies (their example: most coding tasks).

### 5.2 The strongest evidence *against* naive parallelism

- Cognition's widely cited argument against multi-agent architectures for shared-context work `[U]`
  (blog, not peer-reviewed) converges with Anthropic's own caveat above.
- The MAST study of multi-agent failures analysed 150+ traces by hand and scaled to 1,642 annotated
  traces, deriving **14 failure modes in 3 categories** — system design issues, inter-agent
  misalignment, task verification — with reported category prevalence of 44.2%, 32.3%, 23.5% and
  inter-annotator agreement κ = 0.88 `[I]`
  ([arXiv:2503.13657](https://arxiv.org/abs/2503.13657), *Why Do Multi-Agent LLM Systems Fail?*).
  Translation: most multi-agent failures are *architectural*, not model-capability, failures.

### 5.3 Decision rule

Parallelise when **all** of the following hold:

1. Sub-tasks are independent (no shared mutable state, no ordering).
2. Each sub-task's output is independently checkable.
3. The information needed exceeds one context window, or latency matters more than cost.
4. You can afford ~15× tokens for that task class (Phase 1 cost envelope).

Otherwise, run sequentially with a single context. For J.A.R.V.I.S., this means: parallel research
(T2) is a candidate; parallel *actions* on the user's accounts are not.

---

## 6. Multi-agent systems and specialist selection

### 6.1 What the failure taxonomy says to avoid

MAST's three categories map directly onto design controls:

| MAST category | Typical failure | Control |
|---|---|---|
| System design issues | Poor role definition, missing termination conditions, unclear handoffs | Typed contracts between agents, explicit stop rules, budget caps |
| Inter-agent misalignment | Agents disagree or duplicate work; information lost in handoff | Shared state store; one owner per artifact; no free-form agent-to-agent chat |
| Task verification | No one checks the result | A single verifier component with authority to fail the run |

### 6.2 Specialist selection vs. one generalist

**Plausible:** specialist *tools* and specialist *prompts* help; specialist *agents* mostly add
coordination cost. Evidence:

- The measured mechanism behind multi-agent gains in §5.1 is token budget and context separation, not
  specialisation.
- MAST attributes most failures to coordination and verification, which more agents worsen.

**Recommendation:** express specialisation as (a) distinct tool sets, (b) distinct prompts, and
(c) distinct model tiers — inside **one** agent loop — before introducing separate agents. Only
introduce a subagent when the sub-task needs its own context window (breadth) or its own tool
sandbox.

### 6.3 Debate and ensembles

Cheap alternatives to multi-agent debate: self-consistency over k samples with a verifier selecting
the answer. Debate has published support `[U]` but also inflates cost and can converge on confident
error. Do not adopt before experiment E-4.4.

---

## 7. Failure detection, retries, replanning, and result verification

### 7.1 Failure taxonomy used by J.A.R.V.I.S.

| Class | Example | Detection | Response |
|---|---|---|---|
| Transport | 5xx, timeout, rate limit | HTTP status, deadline | Retry with backoff + jitter, idempotency key |
| Contract | Tool returned wrong shape | Schema validation | Retry once, then escalate model tier |
| Semantic | Tool succeeded, result contradicts intent | Business-rule validation, cross-check | Replan, ask user |
| Environmental | File missing, calendar conflict | Explicit error code | Replan |
| Epistemic | Model asserts unverified fact | Verifier / retrieval check | Downgrade claim, add citation, or abstain |
| Policy | Action needs approval | Policy engine | Block + request approval |
| Budget | Step/time/cost exceeded | Budget monitor | Stop, report partial, escalate to user |

### 7.2 Verification designs, ranked by trustworthiness

1. **Executable checks** (unit tests, state comparison, schema + business rules) — the gold standard;
   this is why Terminal-Bench and τ²-bench use verifiers rather than model self-report `[I]`.
2. **Cross-source checks** (two independent retrievals agree; the calendar event matches the request
   fields).
3. **Human confirmation** for consequential actions (Phase 1 §7.2).
4. **LLM-as-judge** — useful for open-ended quality, but must be calibrated against human labels.
   MT-Bench popularised the approach and documented its biases `[I]`
   ([arXiv:2306.05685](https://arxiv.org/abs/2306.05685)); treat an uncalibrated judge as a heuristic,
   never as a release gate.
5. **Model self-assessment** — the weakest; known to inflate confidence. Never sufficient alone.

### 7.3 Retry and replan policy

- **Retries are bounded and typed:** at most N per step, only for retryable classes, always with an
  idempotency key.
- **Escalation ladder:** cheap tier → mid tier → premium tier → human. Each rung requires evidence of
  failure from §7.2 levels 1–3.
- **Replan trigger:** an observation that contradicts a plan assumption, or two consecutive failed
  attempts on the same step.
- **Truthful termination:** every run ends in `done | partial | failed` with the evidence attached.
  This is Phase 1's T7 and the only metric with a zero target.

---

## 8. When deterministic software is better than an AI model

| Job | Use software | Use a model |
|---|---|---|
| Arithmetic, currency conversion, date math | ✅ always | Never (models get dates wrong) |
| Scheduling with constraints | ✅ constraint solver | Only to parse the request |
| Policy checks (allowlists, caps, scopes) | ✅ | Never |
| Data transformation/format conversion | ✅ | Only for ambiguous input formats |
| Routing on structured signals (tool availability, cost, time) | ✅ | For difficulty prediction (weakly) |
| Parsing free text into structure | — | ✅ |
| Resolving ambiguous intent | — | ✅ (with confirmation) |
| Summarising/explaining | — | ✅ |
| Deciding which of two user goals matters | — | ✅ (with confirmation) |
| Judging open-ended quality | Partially (rubrics) | ✅ (calibrated) |

**Established practice:** string-matching, rules, and schedulers are cheaper, faster, deterministic,
and auditable. Every job moved from a model to code removes a failure mode and a cost line.

---

## 9. Recommended minimal architecture (v0)

```
User ──► Interface (text/voice)
            │
            ▼
      Coordinator  ── deterministic loop, budgets, plan object
         │   │  │
         │   │  └──► Policy engine (scopes, approvals, allowlists)
         │   │
         │   └──► Model adapter (tiered: cheap / mid / premium, effort control)
         │             │
         │             └──► Tools (typed, validated, idempotent, sandboxed)
         │
         ├──► Memory & retrieval service (Phase 5) — provenance on every fact
         └──► Verifier (executable checks → cross-source → human → judge)
```

Properties:

- **One agent loop.** No subagents in v0. Specialisation lives in tool sets and prompts.
- **Deterministic coordinator.** The model proposes; code disposes. Code owns budgets, retries,
  plan state, and the terminal status.
- **Tiered adapters.** Model choice is configuration, not architecture, so Phase 2's TEST candidates
  can be swapped without redesign.
- **Verifier has veto.** A run cannot report success unless a check passes.

### 9.1 Two alternatives with tradeoffs

| Alternative | Adds | Buys | Costs |
|---|---|---|---|
| **A. Single premium model, no routing** | One provider, max effort | Simplicity, fewer failure modes, better cache hit rates | 5–25× cost per turn; vendor concentration |
| **B. Orchestrator + parallel subagents** | Planner, subagents, aggregation | Measured 90.2% gain on *breadth research* `[V]`; parallel latency | ~15× tokens; coordination failures (MAST); harder evaluation |
| **C. Debate/ensemble of 3** | Self-consistency or debate | Possible robustness on ambiguous questions | 3× cost; no verifier; confident-error risk |

Recommendation: ship **v0**; adopt **A** only as a temporary baseline for cost comparison; adopt **B**
only for the research task family (T2) if experiment E-4.3 passes; do not adopt **C**.

---

## 10. Experiments that would justify more complexity

| ID | Experiment | Metric | Pass condition to adopt complexity |
|---|---|---|---|
| E-4.1 | Verifier authority: run suite with and without verifier veto | False-success rate, task success | Verifier reduces false-success to 0 and costs <10% success |
| E-4.2 | Router vs single mid-tier model | Cost per successful task, `pass^4` | Router ≥ single-model success at ≥30% lower cost per success |
| E-4.3 | Parallel subagents on T2 breadth tasks vs sequential single agent | Verifier success, p95 task time, cost | ≥10 pp success gain or ≥40% latency reduction at ≤3× cost |
| E-4.4 | Self-consistency (k=3) and debate vs single sample | Success, cost per success | ≥5 pp gain at <2× cost |
| E-4.5 | Effort sweep (min/default/max) per family | Success per dollar | Identify a family where max effort is not worth ≥2× cost |
| E-4.6 | Retry/backoff policy under injected failures | Recovery rate, duplicate side effects | Recovery ≥90%, duplicates = 0 |
| E-4.7 | Plan artifact vs prose plan | T3 durability, drift rate | Durable plan improves restart success ≥20 pp |

**Kill criteria are the point.** If E-4.3 fails, the architecture stays single-agent regardless of how
appealing parallel research demos look.

---

## 11. Findings classification

**Established**
- Cost-controlled comparison is necessary; naive extra calls inflate accuracy at unbounded cost `[I]`.
- `pass^k` reliability is materially lower than one-shot accuracy in dual-control tool use `[I]`.
- Multi-agent failures are dominated by design, coordination, and verification issues, not model
  capability `[I]` (MAST, 14 modes, κ = 0.88).
- External verifiers (tests, state checks) are the only trustworthy completion signal `[I]`.
- Prompt injection succeeds at high rates against agent systems and is not solved by better prompting
  `[I]` (ASB up to 84.30% ASR).

**Plausible**
- A cascade-with-verifier beats a query classifier for consequential tasks.
- Specialist tools + prompts inside one agent capture most of the benefit attributed to multi-agent
  systems.
- Parallel subagents are justified for breadth-first research and not for personal actions.

**Unproven**
- That Anthropic's 90.2% research gain transfers to a personal assistant's research tasks at
  acceptable cost (their eval was internal and run on 2025-era models `[V]`).
- That effort dials produce monotone quality on our workload.
- That a single model tier (no routing) can hit Phase 1's cost envelope with acceptable success.

---

## 12. What was learned, what remains uncertain, and the next research step

**What was learned.** Orchestration should be organised around **verification and budgets**, not
around agents. The measured drivers of agentic performance are token budget and external signal;
the measured drivers of agentic failure are coordination, design, and missing verification. Routing
has credible published savings but is only safe when tied to a verifier. Every added model call must
be justified by an experiment with a kill criterion.

**What remains uncertain.** Whether the cascade's verifier can be built cheaply enough for open-ended
tasks (T1/T2) where executable checks do not exist; whether parallel research is worth 15× tokens for
a personal user; how much of the vendor-reported 2026 agentic progress survives our own harness; and
whether effort-dial inflation is the main reason premium models look better than cheap ones.

**Next research step.** Phase 5: with a coordinator, tiers, tools, and a verifier defined, the next
question is what the verifier and the agent *remember* — how context, structured records, retrieval,
write rules, provenance, and forgetting should be designed so that a recalled fact can always be
traced and corrected.
