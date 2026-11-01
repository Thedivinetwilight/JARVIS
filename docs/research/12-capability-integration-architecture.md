# Phase 12 — Capability Integration: Breadth, Skills, Memory, Attention, Planning, Coordination

- **Status:** published (v1.0, revision R1)
- **Published:** 2026-11-01 · **Revision:** R1
- **Revises:** [Phase 10 §2–§3](10-proposed-architecture-and-experiments.md) (amended, see S-1/S-2 in
  [Phase 15 §1](15-revised-roadmap-supersession-and-next-step.md)); extends
  [Phase 4](04-reasoning-agents-and-orchestration.md) and [Phase 5](05-memory-and-knowledge.md)
- **Depends on:** [Phase 11](11-brain-inspired-cognitive-architecture.md) (which analogies are admitted)
- **Evidence labels:** **Established** · **Plausible** · **Unproven** · **Speculative** · `[I]` independent · `[V]` vendor · `[U]` unverified

---

## 0. The integration problem

Phase 10 described a coordinator with adapters, tools, memory and a verifier. That shape is still right,
but it answers "how does one request flow through the system?" It does **not** answer the new
requirement: how do **broad capability, specialist skills, memory, attention, planning and coordination**
fit together so that the system is capable across many task families *and* can keep improving?

Three integration failures are the real risk, and none is fixed by adding components:

1. **Breadth collapse.** A system tuned on one family (say, calendar management) looks capable until a
   second family arrives and the prompt, the retrieval policy and the verification style all need to be
   different.
2. **Coordinator sprawl.** Every new task family adds a branch to the coordinator until it becomes an
   unmaintainable router with unknown interactions — the classic cause of multi-agent failure
   taxonomies `[I]` (MAST, 2503.13657).
3. **Improvement without measurement.** A skill is added because it seemed to help, memory grows because
   nothing expires, and no per-family baseline exists to show either was an improvement.

This document proposes one structure that keeps breadth explicit and measurable: **five layers, one
registry, one envelope, one event log.**

## 1. The five layers

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ L4  GOVERNANCE      policy · approvals · budgets · verifier · evaluation      │
│                     harness · update pipeline (Phase 13)                       │
├───────────────────────────────────────────────────────────────────────────────┤
│ L3  CONTROL         task envelope in → plan object → step scheduler           │
│                     (attention/budget allocation, retries, escalation)        │
├───────────────────────────────────────────────────────────────────────────────┤
│ L2  MEMORY          working (L3 state) · episodic log · semantic facts ·      │
│                     procedural skills, each with its own write rule           │
├───────────────────────────────────────────────────────────────────────────────┤
│ L1  SKILLS          capability registry: tools, specialists, plan templates,  │
│                     each with contract, cost, latency profile and tests       │
├───────────────────────────────────────────────────────────────────────────────┤
│ L0  SUBSTRATE       generalist model tiers (cheap/mid/premium) + local models │
│                     behind provider-agnostic adapters                        │
└───────────────────────────────────────────────────────────────────────────────┘
             every layer emits to ──► EVENT LOG (append-only, hash-chained)
             every layer is read by ──► EVAL HARNESS (per-family baselines, gates)
```

Layer rules that make the diagram mean something:

| Rule | Why |
|---|---|
| **L0 never decides policy**; it proposes text and arguments | Phase 10 D1/D2 retained; model output is untrusted |
| **L1 is data, not code paths**; adding a skill must not require editing L3 | This is what prevents coordinator sprawl |
| **L2 write rules differ per store**; a skill cannot be created by an episode write | Phase 11 §3.1; prevents facts becoming behaviour by accident |
| **L3 owns budgets and termination**, never the model | Phase 4 §7; false-success control |
| **L4 is off-limits to L0–L3**; nothing below can edit gates, policy or the evaluator | Phase 8 §10; the measuring instrument is not modifiable |

## 2. Broad capability versus specialist skills

### 2.1 The composition question, stated as a decision

For a task family *f*, capability can come from: (a) a generalist model plus tools; (b) a generalist plus
a **sharp prompt/skill contract** for *f*; (c) a specialist model (fine-tune or a different provider)
routed for *f*; (d) a multi-agent decomposition for *f*. The evidence we have does not say (c) or (d) win
in general:

- Routing between a strong and a cheap model captures a large share of quality at lower cost, but only
  when the router's predictions are accurate `[I]` (RouteLLM, 2406.18665).
- Cascades compound errors: a wrong early classification propagates, and cascade gains in the literature
  are task- and threshold-specific `[P]`.
- Multi-agent decomposition shows coordination failures as the dominant error class in agent traces `[I]`
  (MAST, 2503.13657) — a reason to keep (d) opt-in and measured.
- Specialist fine-tunes help format/argument reliability more reliably than they add knowledge `[P]`
  (Phase 3 §8), and they go stale when the base model changes.

**Decision (replacing Phase 10 §2's single-path coordinator, S-1):** breadth is served by **one
generalist substrate + a registry of narrow skills**, with specialists and coordination admitted only per
family, on measured evidence, behind the same gates. Default order of preference:

| Preference | Mechanism | Cost | Evidence needed to move up |
|---|---|---|---|
| 1 | Generalist + tool contract | lowest | — (baseline) |
| 2 | Generalist + family skill (prompt + plan template + verifier) | low | +≥5 pp success or ≥20% cost cut, no regression elsewhere |
| 3 | Specialist adapter/model for the family | medium | Beat (2) by ≥5 pp on the family, survive a base-model swap drill |
| 4 | Multi-agent/parallel decomposition | highest | Beat (3) by ≥10 pp at ≤3× cost (Phase 10 X-6 retained) |

This ordering is the operational meaning of "broad capability without a monolith": breadth lives in the
**registry**, and each entry carries the evidence that justifies its existence.

### 2.2 Families, not features

A **task family** is a set of tasks sharing a verifier shape and failure mode — e.g. retrieval-with-citations
(B1), single-tool execution under policy (B2), multi-step file/state mutation with read-back (B3). Families
are the unit of measurement; features are not. Every family gets: a fixture set, a baseline, at least one
mutation that must be caught, a cost/latency envelope, and a named owner (human) in the registry.

## 3. Memory: four stores, four write rules

| Store | Content | Write rule | Retention | Read path |
|---|---|---|---|---|
| **Working** | current plan, open questions, budget left | created per task; evicted by the scheduler | task lifetime | in context |
| **Episodic** | what was asked/planned/done/verified, with timestamps and provenance | automatic append; never edited | 90 days default, user-configurable | recency + similarity |
| **Semantic** | durable facts with bitemporal validity and trust labels | **gated**: needs a source span and a trust class; contradictions superseded, never silently overwritten | until superseded or deleted by user | hybrid retrieval (Phase 5 D3) |
| **Procedural** | skills: plan templates, tool contracts, executable steps, each with tests | **promoted**: only after the skill passes its own tests *and* the family gates (Phase 13 U2) | until demoted or fails nightly tests | capability registry lookup |

### 3.1 Why procedural memory is the important addition

"Evolving intelligence" is, for a software system, mostly *skill acquisition*: a plan that worked is
worth reusing, a plan that failed is worth blacklisting. Voyager demonstrates that an ever-growing library
of executable, verified skills compounds capability and transfers to a new environment `[I]` (2305.16291).
Our version is deliberately more conservative: a skill is **code plus contract plus tests**, stored in
version control, promoted only through gates, and demoted automatically when its tests fail. A skill is
never a free-text "lesson learned" that gets pasted into a prompt — that is how systems accumulate
confident nonsense.

### 3.2 Memory is not a dumping ground

Phase 5's write gate stands. Two additions from the evolution requirement:

- **Every memory write records its cause** (which episode, which source, which verifier result) so that a
  later regression can be traced to a memory change and rolled back (Phase 13 §4.2).
- **Consolidation is scheduled and tested** (Phase 11 §3.4): a nightly job proposes facts from episodes,
  and its output is evaluated like any other change — it is a system update, not housekeeping.

## 4. Attention and working memory as a cost controller

The brain's capacity limit becomes our **context budget**:

```
step budget = {tokens, wall-clock ms, money, tool calls}
scheduler   = deterministic code in L3
inputs      = candidates from memory + tool results + plan state
priority    = relevance(step) × trust × recency ÷ cost
eviction    = drop lowest priority until budget satisfied; summarise the tail
```

Why deterministic code and not the model: budget enforcement must not depend on the component that
benefits from ignoring it (Phase 10 D1 analogue). Why a budget at all: position and length effects are
measured in LLMs `[I]` (2307.03172), and cost is the dominant operational constraint (Phase 7).

**Unproven:** that a priority-based scheduler beats a simple "recent + retrieved top-k" policy at our
scale. This is experiment X-14 (§7) and a Stage 1–2 question, not an assumption.

## 5. Planning: plan objects, not vibes

A **plan object** is a first-class artifact:

```json
{"plan_id":"p_123","family":"B3","goal":"...",
 "steps":[{"id":"s1","action":"files.write","args":{...},
           "expect":{"path_exists":"notes/week.md"},
           "verify":"equals|contains|absent","budget":{"calls":1}}],
 "acceptance":{"all_steps_verified":true},
 "budget":{"tokens":8000,"usd":0.05,"wall_ms":60000},
 "replan_on":["verify_failed","budget_exceeded","new_evidence"]}
```

Rules: every step declares an **expected post-condition** (Phase 11 §3.3); replanning is triggered by a
named event, never by the model deciding to "try something else"; and a plan template that succeeds
repeatedly is a **candidate skill** (U2 in Phase 13), not automatically a skill.

Planning quality is measured, not asserted: step-level verification rate, replanning count per success,
and the fraction of successes that came from a reused template.

## 6. Coordination: a measured, opt-in layer

Phase 10's D12 (single agent loop; no subagents until evidence) is **retained** (S-9). What changes is
that coordination is now a named capability with an interface, so that when evidence arrives it can be
enabled for one family without redesign:

- **Coordination patterns** (in order of preference): sequential pipeline → parallel fan-out with a
  merge step → debate/critic pair → market-style allocation. Each pattern declares its expected cost
  multiplier and its failure mode.
- **Shared state** is the event log plus an explicit blackboard object; no agent reads another's private
  working memory. (A shared blackboard is a software pattern, not a claim about global workspace —
  Phase 11 §7.1.)
- **Admission rule:** a family may use coordination only with X-6-class evidence (≥+10 pp at ≤3× cost),
  and the family's registry entry must record the pattern, its cost multiplier and the evidence id.
- **Concurrency safety:** two agents may not hold write authority on the same resource class in the same
  plan; the policy engine enforces single-writer per resource per plan.

## 7. The integration contract (the artifact that makes breadth manageable)

Four schemas, versioned, in `registry/` when built. They are the concrete deliverable of this phase.

### 7.1 Task envelope

```json
{"envelope_id":"...","family":"B2","instruction":"...","attachments":[],
 "trust":"user","budgets":{"usd":0.10,"wall_ms":120000},
 "approval_policy":"tier2","requested_outputs":["state_change","citations"]}
```

### 7.2 Capability registry entry

| Field | Example | Purpose |
|---|---|---|
| `capability_id` | `files.append` | stable id used by plans |
| `kind` | `tool` \| `specialist` \| `plan_template` | what sort of thing it is |
| `contract` | pre/post-conditions, arg schema | validation is code (Phase 9 §4) |
| `cost_profile` | `usd_per_call`, `p50/p95_ms` | feeds the scheduler |
| `failure_modes` | `timeout_after_effect`, `partial_write` | feeds the verifier and tests |
| `idempotency` | `required_key` | at-most-once semantics |
| `tests` | ids of unit + family tests | promotion requires passing |
| `evidence` | experiment id + date | why this entry exists |
| `owner` | human name/role | accountability |

### 7.3 Skill contract

A skill is admitted only with: goal, family, steps, pre/post-conditions, verification assertions, cost
envelope, at least one **negative test** (a task it must decline or fail safely), and a demotion
condition. Skills are code in version control, never prompt fragments in a database.

### 7.4 Event log entry

`{ts, envelope_id, layer, actor, action, inputs_digest, result, verification, cost, version_ids}` —
hash-chained, append-only, human-readable. This is the same discipline as Stage 0's ledger
(`eval/harness/ledger.py`), extended from evaluation runs to production events.

## 8. Failure modes of integration, and their controls

| Failure | Mechanism | Control | Detected by |
|---|---|---|---|
| Cascade compounding | wrong early routing escalates silently | per-step verification; escalation ladder | false-success rate per family |
| Over-decomposition | plan splits into steps nobody verifies | step count budget; verification required per step | replans per success |
| Memory poisoning | injected content becomes a durable fact or a skill | write gate; trust labels; skills only from verified successes | X-5-class + X-12 |
| Router drift | routing thresholds tuned on stale data | thresholds versioned; re-evaluated per release | per-family scorecard |
| Skill rot | a skill's dependency changed; it now fails | nightly skill tests; auto-demotion | skill test pass rate |
| Registry sprawl | 200 skills, none measured | every entry needs evidence + owner; quarterly prune | registry size vs family coverage |
| Coordination cost spiral | parallel agents multiply calls | admission rule + hard budget in L3 | cost per success |

## 9. What this changes relative to Phase 10

| Phase 10 element | Status | Replacement |
|---|---|---|
| §2 coordinator-centric single pipeline | **amended** (S-1) | five layers + registry (§1) |
| §3 "single agent loop v0" alternative | **retained** as control plane (S-2, S-9) | L3 stays single-loop until X-6 evidence |
| §2 memory component | **extended** (S-8) | four stores with separate write rules (§3) |
| §2 "model adapters, tiered" | **retained** | L0 unchanged |
| §2 verifier | **retained**, sharpened | prediction-vs-observation per step (Phase 11 §3.3) |
| §9 roadmap | **superseded** (S-3) | Phase 15 §2 |

## 10. Findings classification

| Finding | Label | Basis |
|---|---|---|
| A registry plus task-family measurement is necessary to keep breadth honest | **Plausible** | coordinator sprawl and MAST failure classes `[I]`; no direct measurement yet |
| Procedural memory (verified skills) compounds capability | **Plausible** | Voyager `[I]` (2305.16291) in a game world; not measured for personal-assistant tasks |
| Specialist models per family beat generalist + skill | **Unproven** | no per-family comparison exists yet; this is Stage 1's job |
| Multi-agent coordination helps at ≤3× cost | **Unproven** | Phase 10 X-6 unchanged; MAST suggests failure risk `[I]` |
| A budgeted context scheduler beats recency + top-k | **Unproven** | X-14; position/length effects are only indirect evidence |
| Skills must be code + tests, not prompt fragments | **Plausible** | Phase 8 verification discipline; no direct ablation |

---

**What was learned, what remains uncertain, and the next research step.** *Learned:* "broad capability"
is an **inventory and measurement** problem before it is a modelling problem — breadth is carried by a
registry of narrow, tested capabilities plus per-family baselines, and the failure to avoid is a
coordinator that grows branches instead of consulting a registry. *Uncertain:* whether any specialist or
coordination layer beats a generalist plus a sharp skill contract at our scale, and whether the context
scheduler earns its complexity. *Next research step:* specify the update pipeline that lets this
architecture evolve — memory, skills, policy, models — with traceability and rollback
([Phase 13](13-evolving-intelligence-update-pipeline.md)).
