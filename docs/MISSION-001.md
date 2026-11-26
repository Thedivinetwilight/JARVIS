# Mission 001 — consolidation delivery record

Date: 2026-11-26 · branch `arena/0680961f-jarvis` (base `64739fe`) · pull request #3.
This file records **what was done and what was verified**, not what is intended.

## A. Executive summary

Two divergent implementations existed in the repository: Genesis 0.1 (`src/jarvis`) and the
local-first server direction (`arena/bbffddd4-jarvis`, `app/` + `client/`), with disjoint trees
and no shared history beyond an empty `main`. Mission 001 made Genesis canonical and ported the
server direction into it under Genesis conventions: one SQLite schema, one permission path,
frozen contracts, standard library only.

Concretely: schema 3 (one migration, eight tables), scoped per-client authentication with
pairing and revocation, a single route→scope table, default-deny egress installed at start-up,
reminder delivery with natural-language creation, local audio sessions with hard bounds,
persisted plans with bounded retries and approval pauses, JSON structured logging, provider
retry/fallback, `forget` coverage for the new content tables, and CLI/API surfaces for all of it.
The test suite went from 162 to 253 passing tests, and the CI matrix now runs on every branch.

## B. Architecture as implemented

```
CLI / REPL / loopback HTTP  ──►  Jarvis (core.py, composition root)
                                    │
        ┌───────────────────────────┼───────────────────────────────┐
        │                           │                               │
   Store (schema 3)          Policy (security.py)            provider gateway
   memory · events ·         decide(risk, origin,             Gemini · OpenAI-compatible
   goals · tasks ·           approval, autonomy)              scripted · ResilientProvider
   operations · cycles ·            │                               │
   clients · reminders ·            │                               │
   audio · plans                    ▼                               ▼
                            ToolRegistry (tools.py) ──► CognitiveKernel 10-stage cycle
                            authorise → journal →          observe · interpret · appraise ·
                            execute → observe →            retrieve · assess · plan · act ·
                            verify                         validate · respond · record
        │                                                          │
        └────────────── PlanStore / PlanExecutor ───────────────────┘
                         bounded steps, retries, approval pause

Cross-cutting: egress.GUARD (socket level), logging (JSON+redaction),
auth/manifests (transport scopes), reminders (delivery), audio (capture),
erasure (forget sweep).
```

Three independent authorisation layers, in order: transport scope → execution policy → egress.
None of them can be influenced by model output.

## C. Files

New (7 modules): `logging.py`, `manifests.py`, `auth.py`, `egress.py`, `reminders.py`,
`audio_sessions.py`, `plans.py`.
Modified: `migrations.py` (v3), `provider.py`, `core.py`, `api.py`, `cli.py`, `tools.py`,
`kernel.py`, `interpret.py`, `contracts.py`, `erasure.py`.
New tests (8 files): `test_auth.py`, `test_manifests.py`, `test_egress.py`, `test_reminders.py`,
`test_audio_sessions.py`, `test_plans.py`, `test_provider_resilience.py`,
`test_observability.py`, plus `test_api_scopes.py`; `test_erasure.py`, `test_migrations.py` and
`test_cli.py` extended.

## D. Branch consolidation

| Source | Decision | Why |
|---|---|---|
| `64739fe` Genesis 0.1 (this branch's base) | retained as canonical | cognitive kernel, memory provenance, evidence-based completion, journal, erasure, 162 tests |
| `arena/bbffddd4-jarvis` `app/jarvis/auth.py`, `manifests.py` | adapted → `auth.py`, `manifests.py` | best-in-repo scoped token model; rewritten for the Genesis store and model |
| `app/jarvis/egress.py` | adapted → `egress.py` | default-deny is architecturally useful; now installed for all entry points |
| `app/jarvis/reminders.py` | adapted → `reminders.py` | honest delivery semantics (explicit routing, ack, expiry) |
| `app/jarvis/audio.py` | adapted → `audio_sessions.py` | capture bounds; ASR/TTS remain unavailable and are declared so |
| `app/jarvis/tasks.py` partial plan/task ideas | adapted → `plans.py` | persisted plans with bounded execution, not a second task system |
| `app/jarvis/migrations/0001-0003` | adapted → migration 3 | one schema, one version, one backup/rollback path |
| `app/jarvis/app.py` router, `server.py` | not imported | Genesis `api.py` already had stricter Host/Origin/content-type rules |
| `client/` Windows companion | not imported | never executed anywhere; not needed for the foundation |
| `app/bench`, `eval/` | not imported | Genesis `evals/` + EXP-001 already pinned and reproducible |
| `feat/jarvis-core-v0.2` (`54b9e6b`) | retained in history | ancestor of Genesis; no code changes needed |

## E. Implemented capabilities

* Scoped clients: pairing codes (single use, TTL, throttled), per-client tokens stored as
  HMAC-SHA256 digests, expiry, immediate revocation, purge of that client's reminders/audio.
* Route scopes: `status.read`, `manifests.read`, `memory.read/write`, `goals.read/write`,
  `tasks.read/write`, `reminders.read/write/receive`, `audio.capture`, `plans.read/write`,
  `operations.read/write`, `clients.read/revoke/pair`, `audit.read`, `converse`; admin scopes
  are unreachable for device pairing.
* Reminders: create/list/due/inbox/acknowledge/cancel; delivery counts one attempt per retry
  interval, expires after the delivery window, fails after `max_attempts`.
* Natural-language reminders: `remind me to … (in N units | today/tomorrow/weekday [at clock] |
  at clock | ISO-8601)`; unparseable or missing time → clarification question.
* Audio: session open/append/close, per-chunk 256 KiB, per-session 10 MiB, TTL expiry, purge.
* Plans: create/list/show/execute, 12 steps max, 2 retries per step, approval pause/resume,
  interrupted-step recovery, restart persistence.
* Models: retry of transient errors with exponential backoff, fallback chain, truthful
  attribution, keys never in URLs, blocked egress refused.
* Observability: JSON lines with level filtering and redaction; cycle and capability traces;
  `/v1/state` reports clients, pending reminders, plan counts and egress counters.
* Erasure: forget also sweeps reminder/plan text verbatim.

## F. Verification

Command: `python -m pytest` — **253 passed** in ~38 s.
Environment: Linux x86_64, Python 3.11.2, SQLite 3.40.1, stdlib only (dev extra: `pytest`).
Skips: none in the executed run.
Integration exercised for real: SQLite files, threads, a loopback HTTP server, a fake model
HTTP server, subprocess CLI runs, socket-layer egress patch.
Manual end-to-end: fresh home → `ask` → `remind` → `reminder list` → `pair code` → `serve` →
real HTTP checks (health, 401 without token, 403 cross-origin, pairing, device 403 on
`/v1/clients`, reminder delivery, revocation → 401).
Not verified here: Python 3.10/3.12/3.13, Termux, real Gemini/Ollama endpoints, real audio
hardware, Windows client.

## G. Security

Enforced in code, not prompts: route scopes at the transport boundary, `Policy` at every
capability execution, egress at the socket layer. Pairing cannot mint admin scopes. Tokens and
the server secret are 0600 files, never printed, registered with the redactor; logs redact
message, structured fields and exception text. Audio is bounded and purged. Remaining risks are
listed in `docs/SECURITY.md` (same-user file access, no TLS, in-process throttle reset, audio
retention).

## H. Runtime

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env                 # optional; defaults are local and offline
python -m jarvis chat                # or: ask "…" / serve / doctor / eval retrieval
python -m pytest                     # 253 tests
```

## I. Remaining work

* Automatic decomposition of a free-form objective into a persisted plan, and replanning after
  partial failure (the executor exists; the planner does not call it).
* Conversation deletion command; audio retention policy beyond TTL; reminder push channels.
* Multi-user or multi-profile separation; backups encryption/retention.
* Real-provider verification, other Python versions, Termux, and the Windows client direction.

## J. Mission 002 proposal

**Mission 002 — objective-driven execution.** Make the plan layer the kernel's default path for
multi-step objectives: decompose an objective into a plan with success criteria, execute with
checkpoints and observation, replan once when a step fails for a diagnosable reason, and report
`Completed / Incomplete / Needs approval / Blocked / Failed / Cancelled / Awaiting information`
from recorded evidence. It builds directly on `plans.py`, the journal and the approval pause,
and is the smallest next step that turns the current foundation into an outcome engine.

## K. Capability dashboard

| Capability | State | Evidence |
|---|---|---|
| Cognitive orchestrator | VERIFIED | 10-stage cycle, `tests/test_kernel.py`, `tests/acceptance` |
| Intent | VERIFIED (narrow) | rule interpreter incl. `remind`; English only |
| Context | VERIFIED | workspace assembly, `tests/test_kernel.py` |
| Reasoning | PARTIAL | deterministic arithmetic + verification only |
| Planning | PARTIAL | persisted plans execute; no automatic decomposition |
| Memory | VERIFIED | provenance, supersession, FTS5 retrieval, erasure tests |
| Model routing | IMPLEMENTED BUT UNVERIFIED against real providers | fake HTTP server only |
| Tools | VERIFIED | registry, validation, journal, verification |
| Execution | VERIFIED | bounded retries, approval pause, recovery |
| Recovery | VERIFIED (bounded) | interrupted steps/operations never assumed successful |
| Permissions | VERIFIED | scopes + policy + egress, tested over HTTP and at the registry |
| Persistence | VERIFIED | migrations v1→v3 with backup/rollback, restart tests |
| Observability | VERIFIED | JSON logs, cycle/capability traces, `/v1/state` |
| Testing | VERIFIED locally (253), CI matrix runs but has not yet reported a result | see PR checks |
| Interface | VERIFIED | CLI and loopback API incl. pairing/reminders/plans/audio |
| Reliability | PARTIAL | no long-running concurrency soak; in-process handlers cannot be cancelled |

## Definition of Done

| Item | Status |
|---|---|
| Existing repository inspected | VERIFIED |
| Existing branches assessed | VERIFIED |
| Architectural conflicts identified | VERIFIED (`docs/DECISIONS.md` ADR-0014) |
| Unified architecture established | VERIFIED |
| Valuable existing implementations preserved | VERIFIED (ported, not rewritten) |
| Consolidated development baseline established | VERIFIED (`arena/0680961f-jarvis`, PR #3) |
| Application runs | VERIFIED (CLI + server smoke test) |
| Cognitive orchestrator integrated | VERIFIED |
| Memory integrated | VERIFIED |
| Model abstraction integrated | IMPLEMENTED BUT UNVERIFIED (real providers) |
| Planning foundations integrated | VERIFIED (executor) / PARTIAL (decomposition) |
| Tool interface integrated | VERIFIED |
| Execution foundation integrated | VERIFIED |
| Permission enforcement integrated | VERIFIED |
| Persistence verified | VERIFIED |
| Recovery implemented where feasible | VERIFIED |
| Structured logging implemented | VERIFIED |
| Automated tests executed | VERIFIED (253 passed) |
| Critical test failures addressed | VERIFIED (6 defects found and fixed during the mission) |
| CI established or improved | IMPLEMENTED BUT UNVERIFIED (runs triggered; result pending) |
| Documentation updated | VERIFIED |
| Actual limitations identified | VERIFIED (STATUS.md, SECURITY.md, this file) |
| Delivery report produced | VERIFIED (this file) |
