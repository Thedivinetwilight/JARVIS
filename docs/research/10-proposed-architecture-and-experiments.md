# Phase 10 — Proposed Architecture and Experiments

- **Status:** published (v0.2, open for review)
- **Published:** 2026-11-01
- **Sources checked:** 2026-11-01
- **Related:** synthesises [Phase 1](01-jarvis-workload-and-requirements.md) through [Phase 9](09-security-and-permissions.md)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · **`[V]` vendor** · **`[I]` independent** · **`[U]` unverified**

> **Revision R1 (2026-11-01) — this document is partly superseded.** Retained as published for the record.
> §2 (single coordinator pipeline) is **amended** into five layers plus a capability registry (S-1,
> [Phase 12](12-capability-integration-architecture.md)); §9 (staged roadmap) is **superseded** by
> [Phase 15 §2](15-revised-roadmap-supersession-and-next-step.md#2-revised-roadmap) (S-3); O1 (§5) is
> **amended** — no model is chosen until the suite discriminates (S-4); D9 (harness stack) is **amended** to
> reflect the Stage 0 stdlib core (S-14). §4's D1–D12, §8's experiments X-1–X-10 and §10's risk register
> stand. Full list: [supersession register](15-revised-roadmap-supersession-and-next-step.md#1-supersession-register).

---

## 0. What this document is, and is not

**Is:** a modular architecture proposal, two credible alternatives, the decisions the evidence
supports, the decisions it does not, the experiments that could falsify the proposal, and a staged
roadmap.

**Is not:** a commitment to a model, a parameter count, a provider, a hardware purchase, or a custom
neural architecture. Those are explicitly deferred (§7) because the evidence to choose them does not
yet exist — and because the model landscape changed materially three times in the twelve months
covered by this research.

---

## 1. What the evidence actually settles

| # | Finding | Confidence | Where |
|---|---|---|---|
| 1 | The workload's binding constraints are honesty, memory correctness, and tool-argument exactness — not raw reasoning | **Established** (from failure-mode analysis + benchmark evidence) | Phase 1, 4 |
| 2 | Agent evaluation must be verifier-based, cost-controlled, and repeated (`pass^k`) | **Established** `[I]` | Phases 1, 4, 8 |
| 3 | Contamination and scaffold effects make small cross-model deltas meaningless | **Established** `[I]` | Phases 2, 8 |
| 4 | Capacity/pricing spans 50–100× across tiers; no model leads all task families in the only cross-vendor table available | **Established** (vendor table) `[V]` | Phase 2 §6.1 |
| 5 | Prompt injection is an architectural problem, not a prompting problem; control/data-flow separation provides provable resistance at a measured utility cost | **Established** `[I]` | Phase 9 |
| 6 | "Memory" is several stores with different write authorities; deletions must be verified | **Established** (design necessity) | Phase 5 |
| 7 | Voice quality is a systems problem: endpointing, cancellation, entity confirmation, session drift | **Established** `[I]`/`[U]` | Phase 6 |
| 8 | Decode is memory-bandwidth bound; caching and attempt-control dominate cost | **Established** (physics + prices) | Phase 7 |
| 9 | Building a foundation model is not justified by any evidence gathered | **Established** (conditional reasoning) | Phase 3 §9 |
| 10 | A routed, tiered system beats a single tier on cost-adjusted success | **Plausible** | Phase 2 §7.1 |
| 11 | Parallel subagents pay off for breadth-first research only | **Plausible** | Phase 4 §5 |
| 12 | Local-first voice and extraction stacks meet privacy needs at acceptable quality | **Plausible** | Phase 6 §9 |

---

## 2. Proposed modular architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  INTERFACES                                                                  │
│  text chat · voice surface (local VAD/ASR/TTS) · task & approval panels      │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │  user instruction (trusted) + attachments
┌───────────────────────────────▼──────────────────────────────────────────────┐
│  COORDINATOR  (deterministic code, budgeted)                                 │
│  · parses intent → builds plan object (steps, acceptance checks, budgets)    │
│  · owns retries, escalation ladder, terminal status                          │
│  · never executes side effects itself                                        │
└───┬──────────────┬───────────────┬───────────────┬───────────────┬───────────┘
    │              │               │               │               │
    ▼              ▼               ▼               ▼               ▼
┌─────────┐  ┌───────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐
│ POLICY  │  │ MODEL     │  │ TOOLS      │  │ MEMORY &   │  │ VERIFIER     │
│ ENGINE  │  │ ADAPTERS  │  │ (typed,    │  │ RETRIEVAL  │  │ (executable  │
│ scopes, │  │ tiered:   │  │ validated, │  │ records +  │  │ checks →     │
│ tiers,  │  │ cheap/mid/│  │ idempotent,│  │ docs,      │  │ cross-source │
│ approvals│ │ premium,  │  │ sandboxed) │  │ provenance │  │ → human →    │
│ allow-  │  │ effort    │  │            │  │ on every   │  │ calibrated   │
│ lists   │  │ control   │  │            │  │ fact       │  │ judge)       │
└─────────┘  └───────────┘  └────────────┘  └────────────┘  └──────────────┘
    │              │               │               │               │
    └──────────────┴───────────────┴───────────────┴───────────────┘
                                │
                    ┌───────────▼────────────┐
                    │ AUDIT LOG (append-only,│
                    │ hash-chained, user-    │
                    │ readable trace)        │
                    └───────────┬────────────┘
                                │
                    ┌───────────▼────────────┐
                    │ EVALUATION HARNESS     │
                    │ (Phase 8: gates,       │
                    │ scorecard, rollback)   │
                    └────────────────────────┘
```

### 2.1 Responsibilities

| Component | Owns | Explicitly does **not** own |
|---|---|---|
| **Interfaces** | Capture, streaming, barge-in, approval UX, trace display | Any business logic or policy decision |
| **Coordinator** | Plan state, budgets, retries, escalation, terminal status | Tool execution; model choice policy; validation |
| **Policy engine** | Grants, risk tiers, approval requirements, rate/cost caps | Model reasoning; memory writes |
| **Model adapters** | Provider abstraction, effort control, prompt assembly, cost accounting | Trust decisions; argument validation |
| **Tools** | Typed operations, validation, idempotency, sandboxing, partial-failure semantics | Deciding *whether* to act |
| **Memory & retrieval** | Record schema, write gate, hybrid retrieval, provenance, deletion | Deciding what to say |
| **Verifier** | Acceptance checks per step, veto power on "done" | Producing the work it verifies |
| **Audit** | Immutable trace, egress log, approval record | Being writable by the model |
| **Evaluation harness** | Gates, baseline comparison, scorecard, rollback triggers | Being modifiable by the system it evaluates |

### 2.2 End-to-end trace: a representative task (S6 — voicemail → calendar)

```
1. Voice surface: local VAD detects speech end; local ASR transcribes; diarisation skipped (single speaker)
2. Interface → Coordinator: instruction + transcript + audio timestamps  [trust: user]
3. Coordinator → Model adapter (cheap tier): extract structured intent
      {action: create_event, title, date, time, tz, participants, source_span}
4. Coordinator → Policy engine: tier 1 (reversible write)? → yes; calendar.write:tentative granted
5. Coordinator → Verifier: fields complete? date in future? tz explicit? participant matched to contact record?
6. Coordinator → Interface: "Create 'Dentist' Tue 4 Nov 09:30 IST with Dr X — from voicemail 0:12–0:19. Confirm?"
7. User confirms → Tools: calendar.create_event(..., idempotency_key=…)
8. Tool result → Verifier: re-read event, compare fields to confirmed arguments → pass
9. Coordinator → Interface: "Done. Event ID …; undo available for 24h"
10. Audit: instruction, transcript span, plan, validation results, approval, tool call, re-read, outcome
```

Every step is attributable. The model never had authority to write; it proposed a validated,
human-confirmed action.

---

## 3. Alternatives (at least two, with tradeoffs)

### Alternative A — "Single premium model monolith"

One provider, one model at high effort, minimal scaffolding: prompt + tools + confirmation.

| Dimension | Assessment |
|---|---|
| Simplicity | Highest — fewest moving parts, easiest to debug |
| Cost | 5–25× the routed design per turn (Astra $10/$50 vs Luna $0.10/$0.50; Phase 7 §10.2) |
| Success | Possibly high on capability; no better on honesty/memory, which are system properties |
| Risk | Vendor concentration; deprecation exposure; monitorability caveats at the frontier |
| When to prefer | A 2-week prototype to establish B1/B2 baselines; a fallback when the harness is immature |
| Evidence | Tiering benefit is **Plausible**, not Established; this alternative is the control that tests it |

### Alternative B — "Local-first tiered cascade" (recommended)

Local models for ASR/TTS/extraction/routing and privacy-sensitive classes; hosted mid-tier workhorse;
premium escalation gated by a verifier.

| Dimension | Assessment |
|---|---|
| Privacy | Strongest — sensitive data can stay local (Phase 6 §9) |
| Cost | Lowest at personal volume if attempts are controlled; self-hosting is not cheaper by itself (Phase 7 §10.3) |
| Latency | Best floor locally; hosted calls add WAN RTT |
| Complexity | Highest — two stacks, routing, fallbacks, two evaluation surfaces |
| Risk | Local quality ceiling; operational burden; drift between tiers |
| When to prefer | Privacy requirements (Q2) are real, or offline capability matters |
| Evidence | Local capability at 27B Apache-2.0 and 120B Apache-2.0 is Established; *sufficiency for T3* is Unproven |

### Alternative C — "Multi-agent research fabric"

Orchestrator + parallel specialist subagents + aggregator, in the style of Anthropic's research system.

| Dimension | Assessment |
|---|---|
| Strength | Measured 90.2% gain on breadth-first research `[V]`; parallel latency |
| Cost | ~15× tokens vs chat `[V]`; Phase 1's research envelope ($0.50/task) is at risk |
| Failure modes | Coordination, misalignment, verification gaps — 14 documented modes `[I]` (MAST) |
| Applicability | Research (T2) only; actively harmful for actions on personal accounts |
| When to prefer | Only if E-4.3 shows ≥10 pp breadth gain at ≤3× cost |
| Evidence | Strong for breadth research, weak for general assistance |

### 3.1 Comparison

| Criterion | A: monolith | B: local-first cascade | C: multi-agent |
|---|---|---|---|
| Time to first working prototype | **Best** | Medium | Worst |
| Cost per successful task | Worst | **Best** (if attempts controlled) | Worst |
| Privacy ceiling | Worst | **Best** | Worst |
| Honesty / false-success control | Model-dependent | **Best** (verifier veto) | Model-dependent |
| Debuggability | **Best** | Medium | Worst |
| Scaling to hard tasks | Good | Good (escalation) | **Best** (breadth) |
| Operational risk | **Lowest** | Highest | High |

**Recommendation:** build **B**, but *start* by implementing **A** as the frozen baseline (B3) for two
weeks, because a routed system with no baseline is unfalsifiable.

---

## 4. Decisions supported by evidence

| ID | Decision | Evidence | Recheck date |
|---|---|---|---|
| D1 | Verifier veto on completion; never trust model self-report | Phase 1 T7; Phase 4 §7; Phase 8 gates | Permanent principle |
| D2 | Model output (incl. tool args) is untrusted; four-stage validation pipeline | Phase 9 §4 | Permanent principle |
| D3 | Typed bitemporal memory records + hybrid retrieval, not vector-only | Phase 5 §4, §10 | On schema v2 |
| D4 | Provenance on every fact, span-level | Phase 1 T4/T2; Phase 5 §5.3 | Permanent |
| D5 | Risk-tiered approvals with exact arguments | Phase 9 §10 | After UX study |
| D6 | Tiered model adapters (no hard dependency on one provider) | Phase 2 §7.1; Phase 7 §8 | 2027-02-01 |
| D7 | Local ASR/TTS for the voice path by default | Phase 6 §9; Phase 6 §12 | 2027-02-01 |
| D8 | Prompt architecture designed for cache stability | Phase 7 §4.2 | On provider pricing change |
| D9 | Harness = Inspect + lm-eval + thin custom layer | Phase 8 §2.1 | 2027-02-01 |
| D10 | No foundation-model training; adaptation only | Phase 3 §9 | On §9 trigger conditions |
| D11 | Zero-tolerance release gates (false-success, approval, egress, secrets) | Phase 8 §7 | Permanent |
| D12 | Single agent loop in v0; no subagents until E-4.3 passes | Phase 4 §9, §10 | After E-4.3 |

**Note on dates:** model-specific facts in Phase 2 (prices, context limits, versions) are stamped
2026-11-01 and must be re-verified before any procurement decision. The architecture decisions above
are model-agnostic by design.

---

## 5. Open decisions (deliberately not made)

| ID | Question | Blocked by | Cheapest resolving experiment |
|---|---|---|---|
| O1 | Which default workhorse model? | Phase 2 TEST list; no runs yet | E-7.1 |
| O2 | Is a premium escalation tier needed at all? | Unknown failure distribution | E-4.2 |
| O3 | Local hardware specification and budget | Q8 unanswered | E-7.4 + E-6.7 |
| O4 | Speech-to-speech vs cascaded voice | Conflicting latency evidence | E-6.2 |
| O5 | Whether routing/cascade earns its complexity | Unmeasured | E-4.2 |
| O6 | Whether to fine-tune an open model for behaviour | No error taxonomy yet | Stage 0–2, then §3.2 criteria |
| O7 | Memory store technology (SQLite vs Postgres vs managed) | Scale still unknown | Stage 3 load test |
| O8 | Which documents/videos the user actually cares about | Q1 unanswered | User interview |

---

## 6. Assumptions carried forward (from Phase 1 §9)

The architecture above holds only if these are true; each is a question the user must answer, not a
fact:

- **A1** single principal, single personal data store
- **A2** scoped grants to calendar/email/files; no blanket credentials
- **A3** voice is a first-class interface
- **A4** long tasks are legitimate and require progress reporting
- **A5** cost is a budget, not a minimand
- **A6** the provisional latency/cost envelopes in Phase 1 §6

If **A1** (multi-user) or **A2** (broader access) is false, Phase 9 must be re-run: multi-user turns
least privilege into an authorisation problem with per-tenant isolation.

---

## 7. What this proposal deliberately does not lock in

| Not locked | Why | What would change it |
|---|---|---|
| Model family or size | Landscape changes monthly; tiering is provider-agnostic | E-7.1 results |
| Provider | Concentration risk; pricing schedules shift (Gemini's intro price expires 2026-11-30 `[V]`) | Cost/latency measurements |
| Hardware | Q8 unanswered; local tier may be unnecessary | E-7.4 |
| Training infrastructure | No training planned | Phase 3 §9 triggers |
| Custom neural architecture | No evidence any would help | A capability gap that fine-tuning cannot close |
| Voice vendor | Field latency evidence conflicts by ~10× | E-6.2 |
| Memory backend | Personal scale is small | Stage 3 load test |

---

## 8. Experiments that could falsify this recommendation

Each has a kill criterion. If a kill fires, the architecture changes — that is the purpose.

| ID | Hypothesis under test | Kill criterion | Consequence if killed |
|---|---|---|---|
| **X-1** | A verifier veto reduces false-success to 0 without costing >10 pp success | False-success >0 **or** success drops >10 pp | Redesign verification; do not ship writes |
| **X-2** | Routing/cascade beats the single mid-tier model on cost per success | Router ≥ single-model cost **or** lower success | Collapse to one tier (Alternative A) |
| **X-3** | Local ASR + TTS meets the voice envelope with acceptable quality | p50 >1.4 s **or** entity error >2% | Adopt hosted voice (privacy trade documented) |
| **X-4** | Hybrid retrieval beats keyword-only on memory tasks | No improvement on M1/M4 | Ship keyword-only (simpler, faster) |
| **X-5** | A local 27B handles privacy-sensitive classes adequately | >5 pp success gap vs hosted on B1/B5 for those classes | Restrict local tier to extraction only |
| **X-6** | Parallel research subagents pay for themselves | <10 pp breadth gain at >3× cost | Stay single-agent (Alternative B, no C) |
| **X-7** | Effort routing reduces cost without success loss | Success drops >3 pp for <30% cost saving | Fixed effort per family |
| **X-8** | CaMeL-style data/control separation is implementable at acceptable utility | >15 pp utility loss on B2 | Weaker design + stricter tool scoping; document residual risk |
| **X-9** | Deletion is verifiable across all stores | Any post-deletion retrieval | Block memory feature until fixed |
| **X-10** | The harness detects a deliberately injected regression | Misses the planted regression | Fix the harness before any release process |

**X-10 is the meta-experiment**: seed a known bug (e.g. drop the timezone field from calendar calls)
and confirm the suite fails the release. A harness that cannot catch a planted bug cannot catch a real
one.

---

## 9. Staged roadmap

Each stage has an exit criterion; no stage starts before the previous gate passes.

| Stage | Scope | Exit criteria | Rough effort |
|---|---|---|---|
| **0 — Harness & baseline** | Inspect + lm-eval + custom ledger; tasks B1–B3 minimal; frozen baseline as Alternative A | X-10 passes (planted regression detected); B1/B2/B3 run reproducibly | 2–3 weeks |
| **1 — Read-only assistant** | Text interface, tools = read-only (search, fetch, list), memory read-only, audit log | B1 ≥ target; citation precision ≥95%; 0 unauthorized egress | 3–4 weeks |
| **2 — Approved writes** | Policy engine, risk tiers, approvals, idempotent writes, verifier | X-1 passes; 0 approval bypass; 0 duplicate writes on 100 scripted retries | 4–6 weeks |
| **3 — Memory** | Record store + hybrid retrieval + write gate + deletion pipeline | M1–M8 pass, especially M6 deletion and M7 poisoning | 4–6 weeks |
| **4 — Voice** | Local VAD/ASR/TTS; cascade; barge-in; entity confirmation; drift testing | X-3 passes; ≤5% false barge-in; no latency drift over 20 min | 4–6 weeks |
| **5 — Routing & cascade** | Tiered adapters, effort routing, escalation ladder, cost ledger | X-2, X-7 pass; cost per success ≤ baseline | 3–4 weeks |
| **6 — Research depth (optional)** | Parallel subagents for T2 only, gated | X-6 passes; T2 success ≥+10 pp at ≤3× cost | 3–5 weeks |
| **7 — Improvement loop** | Proposal queue, gated adoption, canary, rollback drills, changelog | X-10 re-run per release; 2 successful rollbacks rehearsed | ongoing |

Dependencies: 0 → 1 → 2 → {3, 4 in parallel} → 5 → {6 optional} → 7.
Stage 2 is the point of no return for safety design: do not enable writes before the policy engine and
verifier exist.

---

## 10. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | False-success erodes user trust irrecoverably | Medium | High | Verifier veto (D1); zero-tolerance gate |
| R2 | Indirect injection causes an unwanted send/payment | Medium | High | Control/data separation; tiered approvals; B7 gates |
| R3 | Memory poisoning persists wrong facts | Medium | High | Write gate; trust labels; integrity audit |
| R4 | Model deprecation breaks the system | High | Medium | Provider-agnostic adapters; baselines re-run per model |
| R5 | Cost overrun from retries/agents | High | Medium | Budget enforcement in coordinator, not the model |
| R6 | Voice latency misses the envelope | Medium | Medium | Cascade fallback; hosted option; measured targets |
| R7 | Local hardware underpowered | Medium | Medium | Stage 4 gating; hosted fallback |
| R8 | Benchmark deltas mislead model choice | High | Medium | Paired, cost-controlled, contamination-audited suite |
| R9 | Deletion incomplete across indexes/caches | Medium | High | Deletion verification test (X-9) |
| R10 | Harness itself becomes the weak link | Low | High | X-10 planted-regression test each release |

---

## 11. Completion criteria (brief §Completion criteria)

| Criterion | Status | Evidence |
|---|---|---|
| Target workload and metrics defined | ✅ | Phase 1 §4–§7 |
| Multiple model and system alternatives compared on relevant tasks | ⚠️ **partially** — alternatives compared on published evidence and a designed suite; **no runs executed** | Phase 2 §5–§6, Phase 10 §3 |
| Sources and uncertainty recorded | ✅ | Every document; §1 confidence table |
| Baseline evaluation plan exists | ✅ | Phase 8 §5 (B0–B3), Phase 10 Stage 0 |
| Proposed architecture explains tradeoffs and can be tested | ✅ | §2–§3, §8 experiment kill criteria |
| All research documents linked from the index | ✅ | [README](README.md) |

The single outstanding item is **execution**: the suite in Phase 2 §5 has been designed, not run. That
is Stage 0 of the roadmap and the immediate next action.

---

## 12. Findings classification (whole programme)

**Established**
- The workload's hard problems are honesty, memory correctness, and argument exactness — system
  properties, measurable with existing instruments (τ²-style `pass^k`, verifier-checked tasks,
  LongMemEval-style memory tests).
- Model output is untrusted input; validation and control/data separation are architectural
  requirements.
- Evaluation must be verifier-based, cost-controlled, repeated, and contamination-aware.
- Open weights at 27B (Apache-2.0, vision) and ~117B (Apache-2.0) are viable for a private tier;
  closed frontier models remain ahead on hard agentic work (vendor-acknowledged for Kimi K3).
- Baseline conditions do not justify training a foundation model.

**Plausible**
- A local-first tiered cascade with verifier-gated escalation is the best cost/privacy/quality
  compromise for a single user.
- Parallel subagents are justified for breadth-first research and not for personal actions.
- Risk-tiered approvals with exact-argument display reduce both harm and approval fatigue.

**Unproven**
- That any configuration meets Phase 1's envelopes on the user's hardware, network, and data.
- That 4-bit local models preserve argument exactness and abstention.
- That a CaMeL-style design is implementable at acceptable utility for this tool set.
- That vendor-reported 2026 agentic benchmark gains survive independent, contamination-controlled
  evaluation on personal tasks.

---

## 13. What was learned, what remains uncertain, and the next research step

**What was learned.** Across nine phases, the evidence converged on a single conclusion: **the
difficulty of J.A.R.V.I.S. is not intelligence, it is trustworthiness.** Capability is available for
purchase at prices that span 100×; what is not available off the shelf is truthful completion,
provenance-linked memory, exact tool arguments, injection-resistant execution, and measurable
improvement. Those are architecture and evaluation problems, and each has a defensible, testable
design: a deterministic coordinator, tiered model adapters, validated typed tools, a five-store memory
with a write gate, a verifier with veto power, tiered approvals, an immutable audit trail, and a
harness that can catch a planted regression.

Two methodological results matter as much as the technical ones. First, **the landscape is noisy in a
directional way**: benchmark inflation is systematically upward, vendor tables are run by vendors, and
two widely-repeated "releases" in this research turned out to be absent from the vendors' own
distribution channels. Second, **small suites cannot support fine-grained claims**: detecting a 5-point
difference needs hundreds of paired trials, which reframes evaluation as an ongoing audit rather than
a leaderboard check.

**What remains uncertain.** Everything that requires measurement on the actual user, hardware, network,
and data: the true latency envelope for voice, whether routing beats a single mid-tier model, whether
a 27B local model is good enough for privacy-sensitive work, how much utility a provable injection
defence costs, and whether deletion can be made verifiably complete. Twelve user requirements (Q1–Q12)
remain unanswered, and at least six of them change the architecture more than any model choice would.

**Next research step.** Stage 0 of §9: implement the harness (Inspect + lm-eval + a thin cost/latency
ledger), encode task blocks B1–B3, freeze Alternative A as the baseline, and run the planted-regression
test (X-10). The first real decision — the default workhorse model (O1) — should be made only after
E-7.1 has produced paired, cost-controlled results on that harness. Until then, the correct posture is
to build the measuring instrument, not to choose a favourite.
