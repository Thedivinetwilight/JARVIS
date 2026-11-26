# Architectural decision records

Format per decision: question · context/requirements · alternatives · evidence ·
decision and reasons · trade-offs · migration · tests · revisit when.

---

## ADR-0001 — Build on Core v0.2 (PR #1) instead of starting fresh
- **Question:** what is the "existing implementation" to develop?
- **Context:** `main` contains only a README; the code lives in draft PR #1
  (`feat/jarvis-core-v0.2`). Its CI never ran (billing block). Checkpoint `81c2e8b` does
  not exist.
- **Alternatives:** (a) start from `main`; (b) cherry-pick; (c) fast-forward the session
  branch to the PR head.
- **Evidence:** the PR's 14 tests pass locally; its safety design is sound; defects are
  fixable in place (AUDIT.md).
- **Decision:** (c). Preserves the author's commits, SHAs and history; all new work is
  layered on top.
- **Trade-offs:** the session branch now contains PR #1's commits. Merging this work also
  merges PR #1 (or PR #1 can be closed as superseded).
- **Revisit when:** never for this milestone.

## ADR-0002 — Keep a standard-library-only runtime
- **Question:** adopt FastAPI/pydantic/React (earlier proposal) or keep stdlib?
- **Context:** v0.2 deliberately targets Android/Termux. Compiled dependencies such as
  `pydantic-core` need a Rust toolchain on Termux. One local user; loopback API.
- **Alternatives:** FastAPI + pydantic + uvicorn; Flask; stdlib `http.server` + dataclasses.
- **Decision:** stdlib. Typed contracts use frozen dataclasses with validation
  (`contracts.py`); HTTP stays on `http.server`; pytest is a dev-only dependency.
- **Trade-offs:** no automatic OpenAPI schema; no WebSockets (streaming) yet; more
  hand-written validation.
- **Migration:** a future `[server]` extra can add an ASGI adapter that calls the same
  `Jarvis` API without touching the kernel.
- **Revisit when:** a browser UI needs streaming/WebSockets, or the Termux target is dropped.

## ADR-0003 — One SQLite database, versioned migrations, unified event log
- **Question:** is the v0.2 persistence layer sound, and how should it evolve?
- **Context:** SQLite WAL is appropriate for a local-first single user. Missing:
  versioning, history, atomic audit (AUDIT D2, D3, D7, D8).
- **Alternatives:** add a second database (vector/graph); JSON files; keep SQLite.
- **Decision:** keep one SQLite file. `PRAGMA user_version` migrations; backup before
  migrating; each migration transactional; refuse newer schemas. Merge `audit` into an
  append-only `events` table (one experience log serving audit + episodic memory).
- **Trade-offs:** single-writer concurrency (fine for one user); FTS5 dependency (with
  fallback).
- **Migration:** v0.2 rows are copied with row-count verification; v0.2 Python methods
  (`audit`, `recent_audit`, `remember`, `search_memories`, …) keep working.
- **Tests:** acceptance 13; `test_migrations.py`.
- **Revisit when:** multi-device sync or multi-user access is required.

## ADR-0004 — Deterministic interpreter and appraisal first; models are optional
- **Question:** should a language model interpret every input?
- **Context:** the companion must work offline and reproducibly; writes must not depend
  on stochastic parsing; Termux devices may have no local model.
- **Alternatives:** LLM function-calling for everything; rules only; rules + optional model.
- **Decision:** `rules-v1` interpreter and `affect-rules-v1` appraisal produce
  interpretations with confidence labels; a configured model drafts free-form answers and
  may *propose* actions, which the kernel journals for approval.
- **Trade-offs:** limited language coverage (English, simple grammar); chat without a
  model is minimal.
- **Revisit when:** a model-assisted interpreter beats `rules-v1` on a fixed intent
  fixture without lowering write precision.

## ADR-0005 — Memory: supersession, provenance, epistemic status, lexical retrieval
- **Question:** how to store, correct and retrieve memories without losing history or
  promoting guesses to facts?
- **Evidence:** EXP-001 (v0.2 Recall@5 0.033 → FTS5 0.933 on retrieval_v1); bi-temporal
  invalidation in Zep/Graphiti; ADD/UPDATE/DELETE/NOOP vocabulary in Mem0; recency +
  relevance scoring in Generative Agents (RESEARCH.md).
- **Alternatives:** overwrite (v0.2); LLM-chosen update ops (Mem0); delete contradicted
  memories; embeddings + vector DB now.
- **Decision:** versioned rows with supersession chains and validity intervals;
  deterministic ADD/NOOP/SUPERSEDE/CONFLICT by key and source class; evidence-gated status
  promotion; FTS5/BM25 retrieval with a documented blended score and a LIKE fallback.
  Contradicted memories are never deleted, only superseded or flagged.
- **Trade-offs:** paraphrase recall is poor (≈0/3 genuine on the fixture); keyed
  contradiction detection only covers "X is Y" statements.
- **Revisit when:** EXP-002 shows semantic retrieval improves Recall@5 on paraphrase
  queries without regressions and with acceptable on-device cost.

## ADR-0006 — Write-ahead operation journal with deadline-based recovery
- **Question:** how to avoid assuming interrupted actions succeeded, and prevent unsafe
  duplicates?
- **Evidence:** Stripe idempotency semantics; LangGraph's lesson that resumed nodes
  re-run side effects unless results are persisted (RESEARCH.md).
- **Alternatives:** log-after-execution; pid-liveness probing; no journal.
- **Decision:** commit a `started` row before the handler runs; on startup mark rows
  older than `timeout + 30 s` from other runtimes `interrupted`; never auto-retry; replay
  recorded outcomes for reused idempotency keys; reject key reuse with different args.
  `os.kill(pid, 0)` was rejected because on Windows it terminates processes.
- **Tests:** acceptance 6 (real SIGKILL), 7; `test_tools_policy.py`.
- **Revisit when:** long-running jobs need heartbeats or out-of-process runners.

## ADR-0007 — Policy as a pure function; autonomy levels; approvals
- **Question:** how are permissions enforced independently of model text?
- **Decision:** `Policy.decide(risk, origin, requires_approval, approved, level, config)`
  — no model output or affect input (signature is tested). Model/kernel-originated writes
  become proposals; irreversible capabilities (e.g. `memory.forget`) always need approval;
  levels 4–5 are rejected by configuration. Model-proposed reads are journaled, not run,
  unless the caller explicitly allows read execution (v0.2 rule preserved for the API).
- **Revisit when:** standing grants (scoped, expiring permissions) are implemented.

## ADR-0008 — Provider interface and data locality
- **Decision:** `generate(ModelRequest) -> ModelResponse` with classified errors.
  Gemini moves the key to the `x-goog-api-key` header (Google's documented method).
  New `OpenAICompatibleProvider` targets local servers (Ollama/llama.cpp/vLLM). Endpoints
  off loopback are *remote* and require `JARVIS_NETWORK_ENABLED=true`; plain http is only
  allowed to loopback; remote models never receive personal or sensitive memories unless
  explicitly allowed. `JARVIS_PROVIDER=auto` preserves v0.2's selection.
- **Revisit when:** streaming, tool-calling APIs or multiple simultaneous models are needed.

## ADR-0009 — Emotional appraisal scope and safeguards
- **Decision:** a bounded rules-based appraisal that distinguishes stated, stated-absent
  and inferred signals; inferred readings are never asserted; appraisals are sensitive
  with 30-day default retention; affect may change tone/verbosity/suggestions only; a
  wellbeing guard removes dependency-inducing model text.
- **Alternatives:** a trained emotion classifier (rejected: no dataset/evaluation;
  privacy risk); no appraisal (rejected: core requirement).
- **Revisit when:** an evaluation set of conversations with appropriateness criteria exists.

## ADR-0010 — Evidence-based completion
- **Decision:** a task is done only with `user_confirmation` (actor = user) or
  `operation_verified` (journal row succeeded and verified = 1, checked in the database).
  Goals complete automatically when every non-cancelled task is done, citing the task
  completion events; reopening a task reopens the goal. v0.2 tasks already marked done
  are labelled `legacy_unverified`.
- **Tests:** acceptance 5, 7, 11; `test_goals.py`.

## ADR-0011 — Erasure policy for `forget` (pre-release audit, 2026-10-17)
- **Question:** when the user forgets a memory, which representations are deleted,
  redacted or retained, and what can honestly be guaranteed?
- **Evidence:** the pre-release audit (PRE_RELEASE_AUDIT.md §3–4) found that the original
  `forget` erased the memory rows, source utterance and FTS entry, but left copies in journal
  results/arguments, replies that quoted it, recall questions and forget requests (and their
  FTS entries), session summaries, migrated v0.2 audit targets, and — on SQLite builds without
  compile-time `secure_delete` — free pages of the dropped v0.2 tables. SQLite: `secure_delete`
  zero-fills freed content; FTS5 deletes leave tokens in old segments until merged; FTS5
  `secure-delete` needs ≥ 3.42.
- **Alternatives:** delete rows outright (rejected: breaks history, ids and the audit trail);
  keep v0.1 behaviour and document it (rejected: the content stayed recoverable); wipe every
  turn that mentions any word of the memory (rejected: deletes unrelated parts of the person's
  own history on a keyword match); VACUUM after every forget (rejected: rewrites the whole file,
  slow on phones, and `secure_delete` already overwrites freed content).
- **Decision:** `jarvis/erasure.py`, applied in the forget transaction: tombstone every version;
  erase linked turns (source, replies that used or quoted it, the user's turns about it); strip
  content fields from linked events and journal rows; cancel pending proposals that target it;
  scrub derived copies by verbatim match (multi-word values/keys as phrases; single words only
  as a JSON content field or an exact event target, never as a bare substring; a user turn only
  when it repeats a full value of ≥ 3 words and ≥ 12 characters); merge FTS segments; then
  truncate the WAL with a bounded wait. The journal
  stores content-free result summaries from the start. The report states what remains:
  - remaining mentions in the user's own words, verbatim or sharing ≥ 2 distinctive words,
    with their event ids (the reply says they can still come up in conversation);
  - backups possibly containing the data;
  - an untruncated WAL.
  Conversation cycles record the memories they use at retrieval time. A cycle still running
  when the forget commits withholds its reply: the check runs in the same write transaction as
  the reply insert (second-pass review, R1/R2). Guarantee boundary: levels 1–3 in the
  database; not backups, exports, copies sent to a remote model, or physical media.
- **Trade-offs:** an idempotent replay of a memory write returns ids and status, not the value;
  linked event payloads lose free-text metadata; rewordings are not found; the sweep reads
  each affected table once for the erasure and once for the check (0.9 s at 100k events on
  the test machine), and the forget timeout is 120 s so a large history is not misreported
  as "outcome unknown".
- **Tests:** `tests/test_erasure.py`; mutation-checked.
- **Revisit when:** SQLite ≥ 3.42 is the minimum (enable FTS5 `secure-delete`), a
  conversation-deletion command exists, or backups gain encryption/retention.

## ADR-0012 — Local API requires a per-installation bearer token (pre-release audit)
- **Question:** who may call the loopback API, in particular the approval endpoint?
- **Evidence:** with real HTTP against `jarvis serve`, a client holding no credentials read all memories, the
  conversation log and the journal, and approved an irreversible `memory.forget`
  (PRE_RELEASE_AUDIT.md A1). Loopback does not isolate OS users, and on Android any app can
  connect to 127.0.0.1. Browser forgery was already blocked (Host/Origin, JSON-only, no CORS).
- **Alternatives:** keep loopback-only (rejected: not an access control); Unix domain socket
  with file permissions (rejected for now: not portable to Windows clients, breaks HTTP tools);
  OS keyring (rejected: new dependency); per-request signing (unnecessary for one local user).
- **Decision:** a random 256-bit token in `<JARVIS_HOME>/api_token`, created with mode 0600,
  compared in constant time, required for every route except `GET /health`; 401 with
  `WWW-Authenticate`. Other methods are refused without acting. The token is never printed,
  is redacted if pasted into a conversation, and is rotated by deleting the file.
  Non-loopback binds stay refused (no TLS); IPv6 loopback (::1) is supported.
  The second-pass review found that the boundary was only as strong as the data files, which
  the default umask left world-readable. Therefore directories JARVIS creates are 0700, and
  the database, WAL/SHM, backups and exports are 0600. API access thus requires the same OS
  user access as the data itself.
- **Trade-offs:** clients must read the token file; one static secret per installation; a
  same-user process can still read it (as it can read the database).
- **Tests:** `tests/test_api.py` (including the real `jarvis serve` process).
- **Revisit when:** a browser UI (needs per-client sessions + CSRF) or remote access (TLS) is built.

## ADR-0013 — Operation state transitions are conditional (pre-release review)
- **Question:** what happens when two clients act on the same proposal at once?
- **Evidence:** forced interleaving: deny read `proposed`, an approval then executed the
  operation, and deny's unconditional update turned it into `cancelled`. The journal (and the
  denying user) claimed an executed action never happened. Concurrent approvals executed once,
  but the losers received a raw exception.
- **Decision:** every transition decided from an earlier read is conditional on the expected
  current status, checked inside the same `BEGIN IMMEDIATE` transaction: `start` (proposed →
  started) already was, and `finish(expect_status=...)` now is for deny/cancel/policy-denied
  paths and for forget's cancellation of pending proposals. The loser of a race receives the
  operation's true recorded outcome ("…is succeeded; nothing to deny" / "…not executed again").
  The executing process's own final status (succeeded/failed/timed out) stays unconditional:
  it is the observation of what actually happened.
- **Tests:** `test_tools_policy::test_deny_racing_an_approval_never_reports_an_executed_operation_as_cancelled`,
  `::test_concurrent_approvals_execute_once_and_every_caller_gets_an_outcome`.

## ADR-0014 — Genesis is canonical; the local-first server is source material (Mission 001)
- **Question:** the repository contains two divergent systems — Genesis 0.1 (`src/jarvis`:
  kernel, memory with provenance, goals, journal, erasure, CLI, API, 162 tests) and the
  local-first server direction (`arena/bbffddd4-jarvis`: `app/jarvis` pairing/scopes/reminders/
  audio/migrations/egress, `client/` Windows companion, product spec). Which is JARVIS?
- **Evidence:** the trees are disjoint; `main` is an empty initial commit; neither direction
  had ever been run by anyone else. Genesis carries the cognitive core (state, authorisation,
  recovery, verification) while the server carries the transport and delivery concerns —
  pairing, scopes, reminder delivery, audio capture, an egress deny-list.
- **Alternatives:** (a) adopt `app/` as canonical (rejected: no kernel, no memory model, no
  verification; the cognitive foundation would start again); (b) keep both (rejected: two
  databases, two task systems, two permission models); (c) merge `app/` wholesale into
  `src/jarvis` (rejected: duplicate routers, migrations and ID schemes, and an untested
  Windows client would become part of the runtime).
- **Decision:** Genesis `src/jarvis` is the canonical modular monolith. The server direction is
  ported piece by piece into it, with Genesis conventions (one SQLite store, one permission
  path, frozen contracts, stdlib only): `auth.py`, `manifests.py`, `egress.py`,
  `reminders.py`, `audio_sessions.py`, `plans.py` are adaptations, and the Windows companion
  is **not** imported (it has never run and is not needed for the foundation).
- **Trade-offs:** the product specification and client work in `bbffddd4` are not on the
  canonical branch; they remain in history and can be revisited. Some capabilities
  (capabilities C-01..C-14 in that direction) remain unimplemented here and are listed honestly
  in `manifests.py` rather than claimed.
- **Tests:** the ported modules each have their own suite (`tests/test_auth.py`,
  `test_manifests.py`, `test_egress.py`, `test_reminders.py`, `test_audio_sessions.py`,
  `test_plans.py`) plus API-level scope tests.
- **Revisit when:** a second interface (desktop/web/Android client) needs the server contract.

## ADR-0015 — New tables go into a versioned migration, not lazy `CREATE TABLE`
- **Question:** the ported services need tables; where do they come from?
- **Alternatives:** create them on service construction (as the ported code did); add
  migration 3 to the existing migration chain.
- **Decision:** migration 3. One schema version, one backup before migration, one rollback
  point, and `SchemaTooNewError` protection continue to hold. Services no longer create
  schema at runtime, so a missing table is an immediate, obvious error rather than a silent
  divergence between databases.
- **Consequences:** schema version 3; existing v2 databases migrate with a backup and an
  `events` record naming the tables; tests that asserted version 2 now assert 3.
- **Tests:** `tests/test_migrations.py`, `tests/test_cli.py` (status reports schema 3).

## ADR-0016 — Route scopes are enforced centrally; pairing is the only tokenless write
- **Question:** how does a paired device get exactly the access it was granted?
- **Decision:** one `required_scope(method, path)` table in `api.py` maps every route to a
  scope; `_authorised` checks Host/Origin, then the token, then the scope. `GET /health` and
  `POST /v1/pairing` are public. Unknown paths require `status.read` so probing cannot
  discover routes. Device pairing cannot request admin or `plans.write` scopes (validated on
  creation, not only at use). The legacy static token remains operator-equivalent so existing
  CLI/API workflows are unchanged.
- **Trade-offs:** a scope table must be maintained alongside routes; a missing entry fails
  closed (needs `status.read`) rather than open.
- **Tests:** `tests/test_api_scopes.py` (401/403/200 per scope, revocation, audio, plans).

## ADR-0017 — The egress guard is installed at start-up, not at `serve`
- **Question:** where should default-deny egress apply?
- **Evidence:** the ported guard was installed only by the server, so `jarvis ask` or the REPL
  with a configured remote provider could reach the network without any policy check.
  Additionally, installing it in `serve` alone would have broken remote model calls, because
  the provider was not inside a declaration.
- **Decision:** `Jarvis.__init__` installs the guard for every entry point. Only loopback is
  allowed unconditionally; `provider._post_json` wraps each call in a single-destination
  declaration (`model_provider`, host, port), and a blocked call becomes `ProviderRefused`.
  Declarations are thread-local so a concurrent request cannot inherit another's permission.
- **Trade-offs:** any future adapter that needs the network must add an explicit declaration;
  that is the intended cost.
- **Tests:** `tests/test_egress.py`, `tests/test_provider_resilience.py` (real HTTP retry and
  fallback through the guard).

## ADR-0018 — Plan execution is origin-aware and pauses for approval instead of failing
- **Question:** what may a multi-step plan do, and what happens when a step needs consent?
- **Evidence:** the first implementation marked a step that produced a proposal as *failed*
  with `needs_approval`, which reported a plan as failed when it was only waiting; and its
  retry classification compared error types against a set that the registry never emits, so
  retries never happened.
- **Decision:** `PlanExecutor.execute(..., origin=…)` executes under the authority of the
  caller: an owner-asked plan is `user_command`, a model-proposed plan is `model_proposal` and
  therefore pauses on writes. A proposing step moves the plan to `awaiting_approval` with the
  operation id; calling `execute` again re-reads that operation (still proposed → stay paused;
  approved → step recorded as succeeded and the plan continues; denied/cancelled → step fails).
  Retryability is decided from status (`TIMED_OUT` is always transient) plus an explicit set of
  transient error names; denials and validation errors are never retried. Retries remain
  bounded (2 per step) and steps interrupted by a crash are marked `interrupted`, never
  succeeded, then resumed.
- **Tests:** `tests/test_plans.py` (pause/resume, denial, bounded retries, recovery, restart).

## ADR-0019 — Natural-language reminders are parsed narrowly and ask when unsure
- **Question:** should `remind me to …` create a reminder directly through the kernel?
- **Decision:** yes, via a deterministic interpreter rule plus `reminders.parse_due`, which
  understands durations (`in 2 hours`, `3 days`), `today`/`tomorrow`/weekdays with optional
  clock times, bare clock times and ISO-8601 — and returns `None` for anything else. When the
  time is missing or unparseable the kernel asks for it (clarification) instead of inventing a
  due time or defaulting silently. Creation goes through the `reminder.create` capability, so
  a model-proposed reminder is journaled as a proposal and needs approval.
- **Tests:** `tests/test_reminders.py` (parser table, kernel creation, clarification,
  model-proposal approval).
