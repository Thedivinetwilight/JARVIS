# Architecture (runtime overview)

The detailed specification is [COGNITIVE_ARCHITECTURE.md](COGNITIVE_ARCHITECTURE.md);
decisions are in [DECISIONS.md](DECISIONS.md).

## Design principle
JARVIS is one local-first cognitive system. Deterministic application code — the
cognitive kernel — owns state, authorization, persistence, validation and the cycle.
Language models are optional, replaceable components that draft language or propose
actions; they are never the source of truth for whether something happened.

## Modules (`src/jarvis/`)

| Layer | Module | Responsibility |
|---|---|---|
| Interfaces | `cli.py`, `api.py`, `__main__.py` | CLI/REPL and loopback JSON API (adapters only), including pairing, clients, reminders, plans and audio routes |
| Composition | `core.py` | `Jarvis`: builds store, identity, policy, registry, provider, kernel; start-up recovery |
| Kernel | `kernel.py` | the 10-stage cycle, workspace, planning, responses, cycle records |
| Interpretation | `interpret.py`, `affect.py` | deterministic intents; emotional appraisal and style |
| Contracts | `contracts.py`, `charter.py` | shared typed records; versioned mission/principles |
| Persistence | `db.py`, `migrations.py` | SQLite facade, connections/transactions, schema versions |
| State | `events.py`, `memory.py`, `goals.py`, `state.py`, `operations.py`, `search.py` | experience log, memory, goals/tasks, identity/sessions, operation journal, lexical search |
| Control | `security.py`, `tools.py`, `selfmodel.py` | policy, capability registry/execution, capability report and claims check |
| Models | `provider.py` | provider contract, Gemini, OpenAI-compatible, scripted, `ResilientProvider` (bounded retry + fallback) |
| Access control | `auth.py`, `manifests.py` | scoped per-client tokens (pairing, expiry, revocation) and the scope vocabulary routes are checked against |
| Network | `egress.py` | default-deny socket-layer egress guard, installed at start-up |
| Delivery | `reminders.py` | reminder queue with poll-and-acknowledge delivery, retry interval, expiry and revocation cancellation |
| Capture | `audio_sessions.py` | local audio capture sessions with hard limits, TTL and purge (no ASR/TTS) |
| Planning | `plans.py` | persisted multi-step plans, dependency gating, result references, bounded retries, cancellation/budget hooks, approval pause/resume, interrupted-step recovery |
| Objectives | `objectives.py` | objective records and criteria, rules planner, validated model planner, execution service, failure diagnosis, structural revision, evidence-based verification, outcome reporting |
| Grounding | `workspace.py`, `web.py` | read-only workspace intelligence over configured roots; allow-listed GET with SSRF checks, per-hop redirect revalidation and extraction |
| Durability | `scheduler.py`, `leases.py` | persistent priority/deadline/recurrence schedules with unique-slot dedupe; single-writer leases with fencing tokens |
| Evaluation | `objective_eval.py` | 28-scenario objective suite (real vs simulated execution) with nineteen metrics |
| Diagnostics | `logging.py` | JSON-lines structured logging with redaction |
| Evaluation | `evaluation.py`, `evals/` | pinned experiments (EXP-001), retrieval fixture |
| Utilities | `util.py`, `redact.py`, `config.py` | time/JSON helpers, secret redaction, settings |

## One interaction
`jarvis chat` / `POST /v1/ask` → `Jarvis.ask` → `CognitiveKernel.process`:
observe → interpret → appraise → retrieve → assess → plan → act → validate → respond →
record. Each stage is recorded in the `cycles` table and linked events share the cycle id
(`jarvis trace`).

## Data model (schema version 5)
`identity`, `runtime_instances`, `sessions`, `state_kv`, `events` (+`episode_fts`),
`memory_items` (+`memory_fts`), `memory_conflicts`, `projects`, `goals`, `tasks`,
`task_dependencies`, `operations`, `cycles`, `schema_migrations`; from migration 3:
`clients`, `pairing_codes`, `reminders`, `reminder_deliveries`, `audio_sessions`,
`audio_chunks`, `plans`, `plan_steps`; from migration 4: `objectives`, plus `plans.objective_id`,
plan-step dependency/criteria/risk/`replaced_from` columns and `events.objective_id`; from
migration 5: `objective_leases` (resource-keyed lease with fencing token), `objective_schedules`,
`plans.revision/replaces_plan_id/superseded_by`, `objectives.schedule_id/scheduled_for/revision`,
and the unique partial index `idx_objectives_schedule_slot` that makes a duplicate scheduled
objective impossible.

## Objective-driven autonomy (schema 4, extended in 5)

`Jarvis.objectives` (`objectives.ObjectiveService`) is the owner-facing path from a sentence to
a verified outcome. It reuses everything below it:

1. **Interpret and plan.** `RulesPlanner` decomposes the request deterministically; when no rule
   applies and a provider is configured, `ModelPlanner` may propose a plan (ADR-0021). Every
   plan is validated against the registry before it is persisted.
2. **Persist.** The objective row carries the request, criteria, constraints, memory ids,
   budget, origin and planner; steps are persisted through `PlanStore` with dependencies,
   criteria and registry-derived risk.
3. **Execute.** `PlanExecutor` runs steps through `ToolRegistry.run` under the objective's
   origin, so `Policy` decides per capability exactly as for any other action. Dependencies gate
   execution; `{{stepN.path}}` placeholders are resolved from the operation journal.
4. **Verify and report.** `verify_plan` judges each step from `operations.verified` and the
   objective from its criteria. The outcome (seven values) and a deterministic answer are stored
   and exposed through the API, the CLI and `Jarvis.state()`. Model prose is never evidence.

A model-authored plan always executes with origin `model_proposal`, even when the owner typed
the objective, so its writes pause for approval. Approvals, denials, policy refusals and scopes
are the same mechanisms as every other action; this layer adds no new authority.

### Grounding and durability (schema 5)

* `workspace.Workspace` is built once from the configured roots and handed to the registry; the
  tools are read-only and refuse anything outside a root, anything secret-looking, and anything
  that is not text.
* `web.WebPolicy`/`WebFetcher` are constructed only when an allow-list exists; the tools are
  registered only then, so an unconfigured build cannot even plan a fetch. Every connection is
  declared to `GUARD` by validated IP.
* `Scheduler` never executes a capability: it submits through `ObjectiveService.submit` with
  origin `scheduled`, so a scheduled write pauses for approval exactly like a model proposal.
  Dispatch relies on the unique `(schedule_id, scheduled_for)` index instead of a check.
* `LeaseStore` makes an objective single-writer; `run()` renews before every iteration and
  abandons the run if the lease is lost, so a superseded worker cannot write an outcome.
* `objectives._structural_replan` revises a plan only when the revision is *material*, carries
  already-succeeded steps with their operation evidence, re-derives criteria (audited), and never
  re-plans a policy or approval denial.

## Two authorisation layers, one execution path
1. **Transport**: `auth.py` authenticates a bearer token (legacy operator token or a
   scoped `jvs_…` client token) and `api.py` checks the route's scope from a single table.
2. **Execution**: `security.Policy` decides per capability, at the registry, from risk,
   origin, approval state and autonomy level. Plans and tools go through the registry, so a
   scope or a model proposal can never skip the policy check.

Egress is a third, independent layer: `egress.GUARD` refuses every non-loopback socket
connection unless the calling code is inside an explicit declaration for that host and port
(the model provider is the only such path in this build).

## Deliberate non-goals in this milestone
No arbitrary shell execution, browser automation, background self-modification, financial
actions, actuator commands or autonomy levels 4–5. In the objective layer specifically: no web
research (it ends `blocked`), no deadline scheduling, no automatic resumption of interrupted
objectives, and no structural re-planning (repairs are in-place).
