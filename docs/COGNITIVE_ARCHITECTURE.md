# JARVIS cognitive architecture — specification (Genesis 0.1)

Status markers: **[I]** implemented and tested · **[P]** partially implemented ·
**[D]** designed, not implemented. Code references are to `src/jarvis/`.

## 0. Design principles

1. **One intelligence, one state.** Every component reads and writes the same persistent
   store through explicit contracts (`contracts.py`); there are no independent agents
   with private memories.
2. **The kernel owns control.** State transitions, memory writes, permissions,
   persistence and validation are deterministic application code. Language models are
   replaceable components that draft language or suggest actions.
3. **Honesty is structural.** Observations, claims, inferences, hypotheses and verified
   results are different data (`EpistemicStatus`), and action outcomes are reported
   stage by stage (`ActionOutcome`). Text cannot override that.
4. **Useful offline first.** Everything in the cycle works without a model; a model adds
   free-form language. Data does not leave the machine unless configured.
5. **Measure, don't assume.** Mechanisms that compete (retrieval strategies, interpreters,
   appraisal rules) are compared on pinned evaluation sets (EXP-001).
6. **Bounded and reversible.** One pass per input, capped steps, deadlines on every
   operation, backups before migrations, supersession instead of overwrite.

This is a software model of operational continuity and functional cognition. It makes
no claim of consciousness, subjective experience or feelings.

## 1. Identity and continuity [I]

| Layer | Data | Mutability | Where |
|---|---|---|---|
| Enduring identity | `system_id` (random, created once), name, principal, charter version | changes only by recorded `identity.updated` events | `identity` table (singleton by CHECK) |
| Charter | mission + ordered principles + operating rules | versioned (`CHARTER_VERSION`) | `charter.py` |
| Version history | every process start: software + schema version | append-only | `runtime_instances` |
| Changeable knowledge | memories, preferences, goals, tasks | versioned / state machines | `memory_items`, `goals`, `tasks` |
| Experience | events, sessions, cycles, operations | append-only (retention may purge sensitive rows) | `events`, `sessions`, `cycles`, `operations` |
| Working state | workspace for the current cycle | rebuilt every cycle; focus pointer persisted | in memory + `state_kv` |

Recovery after crash [I]: operations left `started` past deadline + grace become
`interrupted`; abandoned cycles become `interrupted`; nothing is assumed to have
succeeded (`core.Jarvis._recover`, `operations.recover_interrupted`).
Migration between versions [I]: `migrations.py` (§4.6). Inspect/correct/export/delete
[I]: `jarvis memory history|correct|export|forget`, `jarvis events`, `jarvis identity`.

## 2. Perception and interpretation [P]

Input types: text via CLI, REPL and HTTP [I]; speech via the optional Termux:API adapter
(`voice.py`) [P — tested with simulated commands only]. Images, documents, system events,
sensors [D] (see `selfmodel.NOT_IMPLEMENTED`; `workspace.read` reads small text files).
Voice is an interface, not a separate mind: utterances enter the same cycle on channel `voice`.

Representation: raw input → `Observation` (redacted text, channel, time, session,
redaction kinds) recorded as an `observation.received` event → `Interpretation` (intent,
slots, confidence label, rule, interpreter id) recorded as `interpretation.made`.
An interpretation is never stored as a fact: only explicit user commands produce memory
writes, and those are stored as `source_claim` (what the user said), not as truth.

Interpreter `rules-v1` [I] (`interpret.py`): ordered regular-expression rules for 18
intents; HIGH confidence for explicit forms, MEDIUM for the question heuristic, LOW for
the chat fallback. Write intents only come from HIGH rules. Limitations: English only,
no coreference, simple keyed-fact grammar.

## 3. Cognitive workspace [I]

Built by `CognitiveKernel._workspace` each cycle, bounded by construction:

| Field | Bound | Source |
|---|---|---|
| identity, session (new?, turns), previous session summary | 1 each | `identity`, `sessions` |
| focus goal + derived progress (next ≤3, blocked, waiting, failed attempts) | 1 goal | `goals`, `tasks`, `task_dependencies` |
| other goals | ≤4 | `goals` |
| retrieved memories (ranked, with provenance, staleness, conflicts) | ≤6 | `memory.search` |
| episodic hits (earlier conversational statements) | ≤3 | `events.search_episodes` |
| pending approvals / interrupted operations | ≤5 each | `operations` |
| open memory conflicts | ≤5 | `memory_conflicts` |
| recent turns | ≤6 events (≤4,000 chars sent to a model) | `events` |
| constraints (autonomy level, network, provider) | — | `Policy`, settings |
| appraisal (affect, urgency, style) | 1 | `affect.appraise` |

Provider context is additionally capped at 12,000 characters (oldest episodes and
lowest-ranked memories dropped first). The full history and the full knowledge store are
never injected. The workspace is discarded after the cycle; it is reconstructable from
persistent state, which is what makes restarts transparent (Acceptance 5, 11).

## 4. Memory [I]

One SQLite database; categories are distinguished by data, not by separate databases.

| Memory type (spec §5.4) | Representation |
|---|---|
| Episodic | `events` (append-only; observations, responses, decisions, outcomes) + `episode_fts` |
| Semantic (memory *type*: facts, in the CoALA sense — retrieval itself is lexical) | `memory_items` kind fact/note/decision with epistemic status |
| Procedural | the registered capabilities (code); writable procedures [D] (`kind=procedure` reserved) |
| Working | the per-cycle workspace (§3) |
| Goal | `goals`, `tasks`, `task_dependencies` |
| Self-model | `selfmodel.capability_report` from registry, provider, DB and operation stats |
| User/relationship | `memory_items` kind preference / personal category, with `sensitivity` |

### 4.1 Lifecycle
acquisition (explicit command) → interpretation (`rules-v1`) → provenance attachment
(`source`, `source_event_id`) → storage (transaction with its event) → indexing (FTS5
triggers) → retrieval (ranked) → application (workspace) → validation/correction
(supersede, retract, conflict resolution, evidence-gated promotion) → consolidation [D] →
retention/deletion (expiry purge, forget with erasure).

### 4.2 Write rules (deterministic)
ADD when no active item has the same (category, key); NOOP for the same normalised value;
SUPERSEDE for a different value from the same source class or an explicit user
correction; CONFLICT for a different value from a different source class (both kept,
`memory_conflicts` row, flagged in retrieval). A model can never supersede a user
memory. Recency alone never decides truth.

### 4.3 Epistemic status transitions (enforced in `memory.promote`)
`hypothesis → belief|verified|inference|unknown`, `source_claim → belief|verified|unknown`,
`inference → belief|verified|hypothesis|unknown`, `observation → verified|unknown`,
`prediction → verified|unknown`, `belief → verified|hypothesis|unknown`,
`verified → belief|unknown`, `unknown → hypothesis|belief|verified`.
`verified` requires a recorded `validation.passed`, `operation.verified` or
`user.confirmed` event; `belief` requires at least one evidence event. New memories cannot
start as belief/verified. Agreement between sources is not treated as independent
confirmation (no automatic promotion exists).

### 4.4 Retrieval
Query tokens (stop words removed) → FTS5 `OR` query, porter stemming, BM25 with column
weights key 2.0 / value 1.0 / category 0.5 → candidates (≤60) → score =
0.70·relevance + 0.15·recency (half-life 30 days) + 0.10·status weight + 0.05·scope
(project/goal/category match). Staleness flags for `volatile` (>7 days unverified) and
`changing` (>90 days) items. Fallback without FTS5: per-token LIKE with weighted scoring.
EXP-001 result: weighting showed **no measurable effect** vs relevance-only on retrieval_v1
(both Recall@5 0.933); it is retained as an unvalidated design hypothesis.

### 4.5 Privacy
`sensitivity` normal/personal/sensitive on memories and events; sensitive affect events
expire (default 30 days); personal data is withheld from remote providers and sensitive
data from all providers unless `JARVIS_ALLOW_SENSITIVE_TO_PROVIDER=true`; credentials are
refused or redacted before storage; the operation journal stores content-free summaries;
`forget` (approval required) tombstones every version and erases the linked turns, journal
content, derived copies and FTS segments in one transaction, then truncates the WAL
(`secure_delete` on) — policy in `src/jarvis/erasure.py`, ADR-0011. Tested by scanning every
table, the FTS indexes and the raw database/WAL bytes. Not guaranteed: backups made before the
deletion, exports, paraphrases, the user's own messages that only mention the subject
(reported), filesystem snapshots, swap or the physical medium (SECURITY.md).

### 4.6 Migrations
`PRAGMA user_version`; backup via the SQLite backup API before migrating a non-empty DB;
each migration + version bump in one `BEGIN IMMEDIATE` transaction; row-count checks for
data-moving steps; refusal to open newer schemas; integrity check on open.

## 5. Goals, planning and motivation [P]

Goals: origin `user` (authorised, active) or `inferred` (unauthorised, `proposed`) [I].
Tasks: state machine (see `goals.py` docstring), dependencies with cycle rejection,
blockers, attempt counts with last error, evidence-based completion, derived goal
completion and reopening [I]. Plans: the kernel maps an interpretation to ≤4 capability
steps [I]. Multi-step autonomous plan execution, replanning after failure, deadlines,
budgets and stopping conditions [D] (Roadmap R4).

## 6. The cognitive cycle [I]

| Stage | Input → output | Skips when | Failure handling |
|---|---|---|---|
| observe | text → redacted `Observation`, event | never | invalid input raises before the cycle (empty, >8000 chars) |
| interpret | observation → `Interpretation`, event | never | fallback intent CHAT |
| appraise | text, preferences, recent failures → `Appraisal`, sensitive event | never (records only if signals) | — |
| retrieve | query → memory hits, episodes, event | intents that cannot use memory | — |
| assess | state → bounded workspace | never | — |
| plan | interpretation + workspace → ≤4 steps or clarification, event | never | clarification when target ambiguous |
| act | steps → `ActionOutcome`s via registry (policy + journal + verify) | no steps | each step independent; failures recorded |
| validate | outcomes → validation summary, `validation.passed/failed` | no steps | — |
| respond | outcomes/workspace → text (deterministic or provider + guards) | — | provider error ⇒ local fallback, `provider_error` mode |
| record | response event, session touch, heartbeat, cycle row | — | — |

Termination: single pass, no loops, ≤1 provider call, ≤4 steps. An unexpected exception
ends the cycle as `failed` with the failing stage recorded and an honest message; actions
already executed stay recorded. Every stage's status/duration/notes are stored in
`cycles.stages_json` (`jarvis trace`).

## 7. Reasoning and verification [P]

Implemented: deterministic arithmetic (never delegated to a model) with independent
recomputation; postcondition verification of every write by re-reading the database;
evidence checks against the operation journal; claims check on model text; contradiction
detection by key and source. Designed: a strategy selector that routes problems to
deterministic tools, search, simulation or a model by risk/cost/verifiability (R6);
causal models and multidisciplinary synthesis (§D of the addendum) remain research.

## 8. Emotional appraisal [I] (functional, not felt)

`affect.appraise` separates stated feelings (HIGH), stated absence (overrides earlier
inferences, recorded as `affect.revised`), inferred possibilities (LOW, or MEDIUM with ≥2
cues), urgency and preferences; it outputs a communication style.

| Signal | Operational definition | Inputs | Allowed influence | Persistence / reset | Evaluation |
|---|---|---|---|---|---|
| Uncertainty | readings are inferred, not stated | cue count | wording hedges; never assert | per cycle | test_10, test_interpret_affect |
| Caution | request is irreversible | intent | appraisal flag; approval is enforced by policy, not by affect | per cycle | test_09 |
| Frustration analogue | ≥2 failed/timed-out operations or failed task attempts in 30 min in the session | operations, events | suggest a different approach | sliding 30-min window | test_interpret_affect |
| Progress | tasks done / total | goals | progress lines in responses | persistent (derived) | test_05 |
| Social sensitivity | stated difficult feeling | stated signals | acknowledgement + supportive tone + one concrete step | per cycle | test_interpret_affect |
| Curiosity | open conflicts, unanswered questions | conflicts | listed in resume | persistent | [P] |

Safety boundaries: `Policy.decide` has no affect parameter (tested); appraisals are
`sensitive` events with expiry; a wellbeing guard removes dependency-inducing sentences
from model output; JARVIS never claims to feel anything.

## 9. Metacognition and self-model [P]

`capability_report` lists registered tools with observed outcome counts, provider status
(configured ≠ reachable; last observed outcome), policy, and `NOT_IMPLEMENTED`
capabilities. `check_claims` flags model text that claims actions when no operation ran.
Designed: calibration of confidence labels against outcomes, per-subsystem error tracking,
resource monitoring (R7).

## 10. Action and control [I]

Capability contract: name, description, risk, exact schema, idempotent, reversible,
requires_approval, timeout, verify. Execution: policy decision → write-ahead journal row
→ handler in a daemon thread with deadline → result/error captured → postcondition →
terminal journal row (+ `operation.verified`). Approval: `propose` → `approve` executes
exactly once → second approval is a no-op. Idempotency keys replay recorded outcomes and
reject reuse with different arguments. Cancellation: proposals can be denied; running
in-process handlers cannot be pre-empted (documented limit; subprocess sandbox in R5).

### Autonomy levels (`security.py`)
0 observe · 1 recommend · 2 prepare (default) · 3 execute-reversible (same as 2 until
standing grants exist) · 4–5 rejected by configuration. User commands are user-directed;
irreversible capabilities always need approval.

## 11. Learning and controlled self-improvement [D, with infrastructure started]

| Kind | Mechanism now | Next |
|---|---|---|
| Knowledge learning | explicit memory writes, corrections, conflicts | source-backed ingestion with freshness checks |
| Procedural learning | outcome stats per capability (`operations.stats`) | procedure records with success rates |
| Strategic learning | — | strategy selector evaluated on fixed sets |
| Architecture experiments | EXP-001 harness with pinned fixture | EXP-002 (embeddings vs lexical) |
| Parameter learning | — | only with a dataset, compute plan and evaluation |

Pipeline (for all kinds): observe → measurable limitation → hypothesis → isolated change
→ fixed evaluations (pinned SHA-256) → compare with baseline → regression + safety check →
human approval → reversible deployment → monitoring. No autonomous code modification.

## 12. Stable boundaries (evolution contract)

| Boundary | Must stay stable | May change | Compatibility rule |
|---|---|---|---|
| Contracts (`contracts.py`) | field meanings, enum values | new optional fields | additive only |
| Persistence | `user_version` lineage, append-only events | new tables/columns via migrations | migrate + backup; never edit applied migrations |
| Retrieval | `MemoryStore.search(MemoryQuery) -> list[MemoryHit]` | scoring, indexes, embeddings | must beat baseline on pinned sets |
| Interpreter | `interpret(text) -> Interpretation` | rules, model-assisted alternatives | evaluated on fixtures |
| Provider | `generate(ModelRequest) -> ModelResponse` / `ProviderError` kinds | adapters | data-locality rule |
| Policy | pure function, no model/affect input | rules, grants | changes require review + tests |
| Capabilities | contract fields, journal semantics | new tools | schema exactness |

## 13. Alternatives considered (summary; details in DECISIONS.md)

FastAPI/pydantic/React (rejected for now: Termux); vector database + embeddings (rejected
until EXP-002 justifies it); LLM-chosen memory operations à la Mem0 (rejected: non-
deterministic control over memory); multi-agent decomposition (rejected: fragmentation);
pid probing for crash detection (rejected: unsafe on Windows); separate audit and episodic
logs (rejected: duplicate abstraction).

## 14. Open research questions

1. When does semantic retrieval beat lexical retrieval enough to justify its cost on a
   phone? (EXP-002 design in ROADMAP.)
2. Can confidence labels be calibrated against observed correction rates?
3. What consolidation produces summaries that never upgrade speculation to fact?
4. How should urgency and wellbeing trade off against literal instructions?
5. What evaluation measures appropriateness of emotional responses without labelling
   people's internal states?
6. Which planning representation survives interruption best (task graph vs HTN vs
   model-generated plans with deterministic validation)?
