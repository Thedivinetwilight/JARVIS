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
| Interfaces | `cli.py`, `api.py`, `__main__.py` | CLI/REPL and loopback JSON API (adapters only) |
| Composition | `core.py` | `Jarvis`: builds store, identity, policy, registry, provider, kernel; start-up recovery |
| Kernel | `kernel.py` | the 10-stage cycle, workspace, planning, responses, cycle records |
| Interpretation | `interpret.py`, `affect.py` | deterministic intents; emotional appraisal and style |
| Contracts | `contracts.py`, `charter.py` | shared typed records; versioned mission/principles |
| Persistence | `db.py`, `migrations.py` | SQLite facade, connections/transactions, schema versions |
| State | `events.py`, `memory.py`, `goals.py`, `state.py`, `operations.py`, `search.py` | experience log, memory, goals/tasks, identity/sessions, operation journal, lexical search |
| Control | `security.py`, `tools.py`, `selfmodel.py` | policy, capability registry/execution, capability report and claims check |
| Models | `provider.py` | provider contract, Gemini, OpenAI-compatible, scripted |
| Evaluation | `evaluation.py`, `evals/` | pinned experiments (EXP-001) |
| Utilities | `util.py`, `redact.py`, `config.py` | time/JSON helpers, secret redaction, settings |

## One interaction
`jarvis chat` / `POST /v1/ask` → `Jarvis.ask` → `CognitiveKernel.process`:
observe → interpret → appraise → retrieve → assess → plan → act → validate → respond →
record. Each stage is recorded in the `cycles` table and linked events share the cycle id
(`jarvis trace`).

## Data model (schema version 2)
`identity`, `runtime_instances`, `sessions`, `state_kv`, `events` (+`episode_fts`),
`memory_items` (+`memory_fts`), `memory_conflicts`, `projects`, `goals`, `tasks`,
`task_dependencies`, `operations`, `cycles`, `schema_migrations`.

## Deliberate non-goals in this milestone
No arbitrary shell execution, browser automation, background self-modification, financial
actions, actuator commands or autonomy levels 4–5.
