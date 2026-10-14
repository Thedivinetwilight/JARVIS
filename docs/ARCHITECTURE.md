# Architecture

## Design principle
JARVIS is a local-first orchestration system. A language model interprets intent and drafts recommendations; deterministic application code owns state, authorization, persistence, and execution. The model is never the source of truth for whether an operation succeeded.

## Runtime
- **CLI/API**: input/output adapters; API defaults to loopback and does not auto-execute proposed tools.
- **Core**: assembles settings, SQLite store, policy, registry, and optional model provider.
- **Provider**: Gemini REST adapter using `urllib`; replaceable without changing business logic.
- **Store**: projects, tasks, memories, audit events in SQLite with foreign keys and WAL.
- **Tool registry**: exact argument validation, risk classification, centralized policy, audit-before/after execution.
- **Policy**: network disabled by default; write tools require explicit user intent; dangerous tools are not registered.
- **Workspace**: read-only directory listing constrained to the configured workspace.

## Assistant turn
1. Validate the user message.
2. Build a bounded context snapshot from the database.
3. Call the provider only when network and credentials are enabled.
4. Treat plain text as a response.
5. Treat JSON tool output as a proposal, not a command.
6. Require application-side authorization and validated arguments before execution.
7. Record outcome in the audit trail.

## Data model
- Project: name, description, lifecycle status, timestamps.
- Task: optional project, title, status, priority, due date, timestamps.
- Memory: category/key/value, confidence, source, timestamps.
- Audit: actor, action, target, decision, JSON details, timestamp.

## Deliberate non-goals in v0.2
No arbitrary shell execution, browser automation, background self-modification, real financial actions, or robot actuator commands. Those require isolated runners, explicit authorization, separate safety architecture, and dedicated verification.
