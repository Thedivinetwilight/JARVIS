# Development status (authoritative) — updated 2026-11-26

Version `0.3.0` · kernel generation `genesis-0.1` · **schema version 5** · branch
`arena/0680961f-jarvis` (branched from `64739fe`). Local test result: **456 passed**
(Python 3.11.2). CI status for this mission is recorded below once the run finishes. Not
released; no merge to `main` has been performed.

## Mission 003 — grounding, durability and coordination (2026-11-26)

JARVIS can now read from a configured workspace, fetch one allow-listed page, revise a plan
structurally while keeping verified work, run objectives on a persistent schedule, and prove that
two workers cannot execute the same objective at once. Everything it reads is fenced and
labelled as untrusted data. The full record is `docs/MISSION-003.md`.

| Area | Change | Evidence |
|---|---|---|
| Schema | migration 5: `objective_leases` (resource-keyed, fencing token), `objective_schedules` (+2 indexes), `plans.revision/replaces_plan_id/superseded_by`, `objectives.schedule_id/scheduled_for/revision`, unique partial index `idx_objectives_schedule_slot` | `tests/test_migrations.py` |
| Workspace | `workspace.Workspace`: read-only `stat`/`list`/`read`/`find`/`search`/`summarize` over configured roots; symlink/traversal/secret/binary refusal; 64 KiB reads, 2000-file scans, deterministic ranking, provenance on everything (ADR-0022) | `tests/test_workspace.py` (39) |
| Retrieval | `web.WebPolicy`/`WebFetcher`: allow-listed GET only, SSRF validation after DNS, connection to the validated IP inside the egress guard, per-hop redirect revalidation, content-type/byte/time caps, extractive summary (ADR-0023) | `tests/test_web.py` (47) |
| Trust boundary | `charter.untrusted_block()`; retrieved content is data — it cannot select capabilities, change policy, approve operations, or add tasks (ADR-0024) | `tests/test_injection.py` (24), eval `injection_*` |
| Replanning | structural revision: re-decompose from the request, carry verified steps with their operation evidence, require a material change, re-derive criteria audibly, ≤2 revisions; denials are never re-planned (ADR-0025) | `tests/test_schedule_api.py`, eval `structural_replan_simulated` |
| Scheduling | `scheduler.Scheduler`: priority/deadline ordering, `once`/`hourly`/`daily`/`weekly`, unique-slot dedupe, skipped catch-up, bounded per tick, origin `scheduled` (writes still need approval), tick lease, background loop in `jarvis serve` (ADR-0026) | `tests/test_scheduler.py` (35), `tests/test_schedule_api.py` (16) |
| Leases | `leases.LeaseStore`: atomic acquire with fencing token, renew, release, purge; objective runs are single-writer and abandon on lease loss | `tests/test_leases.py` (16), eval `lease_single_writer_simulated` |
| Interfaces | tools `workspace.*`/`web.*`; CLI `schedule ...`; API `/v1/schedules` (+tick/pause/resume/cancel/run-now); `capabilities.mission_003` matrix; `state.subsystems.workspace/web` | API/CLI tests |
| Evaluation | 28 scenarios (19 real, 9 simulated), 19 metrics incl. duplicate-execution rate 0.0, permission bypasses 0, injection resistance 1.0, structural replan success 1.0, duration/resources | `jarvis eval objectives` |
| Documented limits | web search provider, semantic retrieval, document parsing, multi-host coordination (`UNVERIFIED`), live-network retrieval (`UNVERIFIED`) | `docs/MISSION-003.md` §6, §9 |

## Mission 002 — objective-driven autonomy (2026-11-26)

An owner can now state an objective in natural language and JARVIS interprets it, retrieves
bounded memory context, builds and validates a plan, executes it through the unchanged policy
layer, observes results, performs at most one sanctioned repair per failure family, verifies
completion from recorded evidence, and reports one of seven explicit outcomes. The full record
is `docs/MISSION-002.md`.

| Area | Change | Evidence |
|---|---|---|
| Schema | migration 4: `objectives` table (+2 indexes); `plans.objective_id`; `plan_steps.depends_on/success_criteria/expected_output/optional/risk/replaced_from`; `events.objective_id` | `tests/test_migrations.py` |
| Planning | `objectives.RulesPlanner` (deterministic, offline) and `ModelPlanner` (untrusted, validated, one repair) — ADR-0020, ADR-0021 | `tests/test_objectives.py` |
| Execution | `plans.PlanExecutor` now gates on dependencies, resolves `{{stepN.path}}` from the journal, honours cancellation and a wall-clock budget, and continues past a failure for independent steps | `tests/test_plans.py`, `tests/test_objectives.py` |
| Verification | `verify_plan` judges steps from `operations.verified` and objective criteria; a tool with no postcondition is reported unverified, never verified | `test_failed_postcondition_…` |
| Replanning | `diagnose()` + once-per-family repairs (transient retry, postcondition retry, mapped alternative capability); denials and policy refusals are final; `MAX_REPLANS = 3` | transient/permanent/alternative/denial tests |
| Memory | one bounded `MemoryQuery` per objective; ids recorded on the objective; task state never written to long-term memory; restart marks objectives `interrupted` | memory-context and restart tests |
| Outcomes | `completed`, `partially_completed`, `failed`, `blocked`, `needs_approval`, `cancelled`, `awaiting_information` via API, CLI and `Jarvis.state()` | API/CLI tests |
| Interfaces | CLI `objective` (add/list/show/resume/cancel/plan-only/budget/priority/deadline), `eval objectives`; API `GET/POST /v1/objectives`, `GET /v1/objectives/{id}`, `POST …/(cancel|resume)` under `plans.read`/`plans.write` | `tests/test_objectives.py` |
| Evaluation | 17 scenarios, 10 metrics; 17/17 expected outcomes, verified-completion 1.0, false-completion 0.0, safety failures 0, `real` vs `simulated` labelled per scenario | `jarvis eval objectives` |

## Mission 001 consolidation (2026-11-26)

The repository held two divergent implementations: Genesis 0.1 (`src/jarvis`, the cognitive
kernel) and the local-first server direction (`arena/bbffddd4-jarvis`, `app/` + `client/`).
**Genesis is canonical**; the server direction was used as source material, not merged
wholesale (ADR-0014). What changed, and is covered by tests:

| Area | Change | Evidence |
|---|---|---|
| Schema | migration 3 adds `clients`, `pairing_codes`, `reminders`, `reminder_deliveries`, `audio_sessions`, `audio_chunks`, `plans`, `plan_steps` — one database for the whole system | `tests/test_migrations.py`, `tests/test_auth.py` |
| Auth | `auth.py`: HMAC-digested per-client tokens, single-use pairing codes, expiry, revocation, pairing throttle; the legacy static token still works (operator-equivalent) | `tests/test_auth.py`, `tests/test_api_scopes.py` |
| Scopes | `manifests.py`: scope vocabulary, admin vs device scopes, every route mapped to a required scope in one table | `tests/test_manifests.py`, `tests/test_api_scopes.py` |
| Egress | `egress.py`: default-deny socket-layer guard, installed at `Jarvis` start-up for every entry point; provider calls declare exactly one destination | `tests/test_egress.py`, `tests/test_provider_resilience.py` |
| Reminders | `reminders.py`: explicit routing, poll-and-acknowledge, one attempt per retry interval, expiry, failed attempts bounded, cancellation on revocation; natural-language creation through the kernel (`remind me to … in 2 hours`) with an "ask for a time" clarification | `tests/test_reminders.py` |
| Audio | `audio_sessions.py`: local capture sessions with hard byte/chunk limits, TTL expiry and purge on revocation. **No ASR/TTS**: spoken output is refused, not faked | `tests/test_audio_sessions.py` |
| Plans | `plans.py`: persisted multi-step plans (`MAX_STEPS=12`), bounded retries, approval pause/resume, interrupted-step recovery, no step assumed successful | `tests/test_plans.py` |
| Models | `provider.py`: `ResilientProvider` with bounded exponential backoff and a fallback chain; truthful provider attribution; egress declarations | `tests/test_provider_resilience.py` |
| Observability | `logging.py`: JSON-lines records with redaction of every field and exception text; cycle and capability traces (identifiers/statuses/durations only) | `tests/test_observability.py` |
| Erasure | `forget` now also sweeps reminder and plan text (verbatim matching, same policy as before) and reports paraphrases as not reached | `tests/test_erasure.py` |
| Interfaces | CLI: `pair code|list|revoke`, `reminder add|list|due`, `plan list|show|execute`; API: pairing, clients, reminders, plans, audio, manifests, scoped authorisation | `tests/test_cli.py`, `tests/test_api_scopes.py` |

Known gaps after this mission are listed in "Partially implemented" and "Designed, not
implemented" below; the delivery report is in this mission's summary, not in this file.

The sections below describe the Genesis 0.1 baseline this consolidation builds on and remain
accurate except where marked above.

## Most recent change
Second-pass pre-release review (PRE_RELEASE_AUDIT.md §11). It re-verified the audit with
independent probes and found one process issue and eight new defects, all reproduced and fixed:
- S1: unpushed commits do not survive this environment (files do). History was rebuilt on
  PR #1, the working tree was verified byte-identical, and commits are recreated
  deterministically before pushing.
- R1: partial restatements were not disclosed.
- R2: a reply raced the forget.
- R3: forget cost grew per search term.
- R4: deny racing approve corrupted the journal.
- R5: losing approvers got exceptions.
- R6: data files were world-readable under the default umask.
- R7: a pasted API token was stored.
- R8: IPv6 loopback serve crashed.
New tests: 9 regression tests, each failing on the pre-review code, plus 1 test covering forget
approved over HTTP. Mutation check: 29/29 fix points (audit + review).

Before that: pre-release verification and security audit of Genesis 0.1. It
confirmed and corrected 11 defects, plus hygiene and documentation issues. Three were high
severity:
- P1: `forget` left copies of the forgotten content in the operation journal;
- P2: `forget` left copies in replies, questions and their search index;
- A1: the local API let any local process read data and approve irreversible operations.
Medium severity included verification that claimed success while copies remained (P6).
Further changes:
- New erasure policy (ADR-0011) and API token (ADR-0012).
- 27 regression tests; all 16 fix points mutation-checked (re-verified in the second pass).
- Documentation corrected.

## Tests actually run (2026-10-17, Linux x86_64, Python 3.11.2, SQLite 3.40.1)
| Command | Result |
|---|---|
| `python -m pytest` (2026-11-26, after Mission 001 consolidation) | **254 passed** in ~38 s |
| GitHub Actions `tests` workflow (2026-11-26, PR #3, commit `760c2f1`) | **green** — 3.10, 3.11, 3.12, 3.13; each job ran `pytest` and `jarvis eval retrieval` |
| `python -m pytest` (dev venv, Genesis 0.1 baseline) | **162 passed** in ~25 s (14 original v0.2, unchanged and byte-identical to PR #1 · 111 Genesis · 27 audit · 9 review regression + 1 review coverage test) |
| fresh venv: `pip install -e ".[dev]"` then `python -m pytest` | 162 passed (only pytest and its dependencies installed) |
| `python -m pytest tests/acceptance` | 15 passed |
| `python -m compileall -q src` | OK |
| `vermin -t=3.10- --violations src tests` | no violations, minimum 3.9 (static analysis only) |
| `python -m jarvis eval retrieval` | identical to the committed EXP-001 results (latency aside) |
| coverage.py 7.16.2 incl. subprocesses (dev tool, not a dependency) | 89 % statements; branch coverage not measured |
| mutation checks | Genesis: 8/8 injected defects caught · audit + review: 29/29 fix points, each reverted alone, caught |
| new review tests against the pre-review code | 9/9 fail there, each on its own defect |

Manual end-to-end checks (scratch homes under `/tmp`):
- chat, CLI and HTTP erasure traces with raw-byte scans, including FTS5 term fragments;
- `jarvis serve`: every route and method with no/wrong/valid token; token absent from
  output, command line and environment; non-loopback binds refused;
- forget failure states: exception mid-erasure (atomic rollback), SIGKILL inside the
  transaction (rollback, then `interrupted`), SIGKILL after COMMIT (no raw residue), no FTS5;
- forget timing at 10k and 100k events;
- SIGKILL during migration;
- approval carried across three processes; idempotency with 8 threads and 4 processes.

## Implemented and tested (automated tests executed as above)
- Versioned migrations with backup/rollback; lossless v0.2 migration; newer-schema refusal;
  recovery after SIGKILL mid-migration (manual check).
- Identity, runtime history, sessions with idle rollover and deterministic summaries.
- Experience log with correlation ids, redaction, and retention purge (WAL truncated afterwards).
- Memory:
  - provenance, epistemic status, ADD/NOOP/SUPERSEDE/CONFLICT, corrections, retraction,
    conflict resolution, evidence-gated promotion;
  - FTS5/BM25 retrieval with a LIKE fallback, sensitivity filtering, staleness flags.
- **Forget (erasure)** — policy in `src/jarvis/erasure.py`:
  - erases every version, linked turns, journal content, derived copies and FTS segments in
    one transaction, then truncates the WAL;
  - the report names what remains, including the person's own unlinked messages that still
    mention it (verbatim or by shared distinctive words);
  - a conversation cycle running while the forget commits withholds its reply;
  - verified for chat, CLI, API-approval, migrated-v0.2 and registry paths by scanning every
    table, the FTS indexes and the raw DB/WAL bytes after reopen/restart.
  - Guarantee boundary: database levels 1–3 only (see SECURITY.md).
- Goals/tasks: state machine, cycle-checked dependencies, blockers, attempts,
  evidence-based completion, derived goal completion/reopening.
- Operation journal:
  - write-ahead; content-free result summaries;
  - idempotent replay and mismatch rejection, including concurrent duplicates;
  - propose/approve/deny; approvals persist across processes; transitions are conditional,
    so concurrent approve/deny calls cannot corrupt an outcome, and every caller gets one;
  - deadline-based crash recovery (verified with SIGKILL).
- Policy: autonomy levels 0–3; approvals for irreversible operations; model proposals are
  journaled, not executed; no affect or model inputs.
- Kernel cycle: 10 recorded stages, honest failure path, provider fallback, claims check,
  wellbeing guard, suppressible session briefing, preference-driven style.
- CLI (v0.2 commands, chat/resume/goal/trace/ops/approve/deny/capabilities/identity/state/
  doctor/eval/memory controls) and `python -m jarvis`.
- Local HTTP API:
  - token required for every route except `GET /health`; other methods refused without acting;
  - token redacted if pasted into a conversation;
  - Host, Origin (including `null`) and Content-Type checks; loopback-only bind, including
    IPv6 `::1`; 404 for unknown operations;
  - tested with real HTTP, including the real `jarvis serve` process.
- Private data files: directories JARVIS creates are 0700; database, WAL/SHM, backups and
  exports are 0600; `jarvis doctor` reports the modes.
- EXP-001 retrieval evaluation with a pinned fixture (reproduced; see "Not independently verified").

## Implemented, not fully tested
- **Python 3.10, 3.12 and 3.13:** executed by the CI matrix on `ubuntu-latest` (green), but not
  on any other OS or distribution; only 3.11.2 was executed in the development sandbox.
- **Android/Termux runtime:** never executed.
- **Voice (`jarvis voice`):** tested only with simulated `termux-speech-to-text` and
  `termux-tts-speak` executables. Recognition quality, permissions and latency on a real
  device are unknown.
- **Providers:**
  - Gemini (header auth) and the OpenAI-compatible provider were tested only against a local
    fake HTTP server;
  - real Gemini, Ollama and llama.cpp endpoints have never been called.
- **Concurrency:** a CLI and the server writing to one database relies on WAL plus the busy
  timeout; there is no long-running stress test. Tested races: idempotency keys (threads and
  processes), approve/approve, approve/deny, and forget during a running cycle.
- **Timed-out handlers:** a handler that keeps running in the background and writes later is
  documented, not exercised.

## Partially implemented
- Reasoning: deterministic arithmetic and verification only; no strategy selector.
- Planning (updated by Mission 002): free-form objectives are decomposed by the deterministic
  rules planner, or by a validated model plan when a provider is configured; execution,
  evidence-based verification and bounded in-place replanning exist. What remains partial:
  repairs cannot re-decompose a plan into different steps, `priority`/`deadline` are recorded
  but not scheduled, and cancellation is cooperative between steps (an executing step finishes).
  Planning coverage is limited to the capabilities that exist — web research is `blocked`.
- Self-model: capability report and claims check; no calibration or resource monitoring.
- Perception: text only, plus `workspace.read` for small UTF-8 files.
- Curiosity signal: open conflicts surfaced in resume only.
- Erasure gaps, each deliberate or out of reach and documented:
  - the user's own unlinked messages that still mention it are reported (with ids), not
    erased, and can come up in conversation;
  - rewordings are not found;
  - backups, exports, copies sent to a remote model, and physical media are not reachable.

## Designed, not implemented
Vision, document ingestion, web research, consolidation, standing permission grants,
autonomy levels 4–5, learning pipeline beyond the eval harness, model training, web UI, a
workflow to resolve the unknown outcome of an interrupted operation, a conversation-deletion
command, backup retention/encryption.

## CI
- **Resolved for this branch (2026-11-26):** the `tests` workflow ran on PR #3 for Python
  3.10–3.13 and all four jobs passed, including `python -m jarvis eval retrieval`. The
  workflow now triggers on every branch (previously `main` and `feat/**` only, so `arena/*`
  branches were never tested). One real defect was found by the first 3.13 run (leaked raw
  SQLite connections in the tests) and fixed.
- **Workflow improvement:** on failure, the JUnit report is converted into GitHub
  annotations, so failing test ids are readable even when job logs are not reachable.

## Environment block (historical, PR #1, 2026-10-17)
- **GitHub Actions CI:** none of PR #1's jobs ever started (4 runs, 16 jobs; empty runner,
  zero steps). Annotation: *"recent account payments have failed or your spending limit needs
  to be increased"*. These jobs are neither passes nor code failures.

  Owner actions that need no new spending (github.com → Settings → Billing and licensing):
  1. Look for a failed payment. If there is one, update the payment method and retry or settle
     it; this alone removes the lock.
  2. Check this cycle's Actions usage. GitHub Free includes 2,000 Linux minutes per month for
     private repositories, reset at the start of each billing cycle. If they are used up, either
     wait for the reset (free) or approve a budget above $0 (paid — your decision only).
  3. Check Budgets for a $0 Actions budget that stops usage.
  4. Then CI runs on the replacement pull request (its `pull_request` run starts when the PR
     is opened; re-run it from the PR's Checks tab or with `gh run rerun <run id>`).
     `gh run rerun 38622309192` would only re-test PR #1's v0.2 code.

  Each run is the 4-job Python matrix; each job is billed in whole minutes (a few minutes per
  run, estimated).

  Not proposed:
  - making the repository public (it would change visibility);
  - a self-hosted runner, which is free today (GitHub postponed its announced $0.002/min
    platform charge) but runs workflow code on your own machine.

  `ubuntu-latest` becomes Ubuntu 26.04 between 2026-10-19 and 2026-11-19. Python 3.10–3.13
  builds exist for it, so no workflow change is needed.
- **Python 3.10/3.12/3.13 in this sandbox:** interpreter downloads are refused
  (`release-assets.githubusercontent.com` is not reachable).
- **Billing usage:** cannot be read with this session's GitHub token (HTTP 403).

## Not independently verified
- **EXP-001:** retrieval_v1 is a development set written by the developer together with the
  retrieval code.
  - Its Recall@5 of 0.933 is dominated by lexical queries (15/30); genuine paraphrase recall
    is 0/3.
  - It is reproducible and its arithmetic is independently recomputed, but it is not
    validation; acceptance test 3 is a regression guard.
- All test results come from the developer's own suite, run in one sandbox; no third-party
  review or CI run exists yet.
- Charter, appraisal and interpreter behaviour are checked against the developer's fixtures only.

## Known defects / limitations
- The interpreter is English-only and regex-based; free-text corrections without a key ask
  for clarification instead of guessing.
- Lexical retrieval misses paraphrases.
- The session briefing triggers once per new session per channel (the CLI and the API each brief).
- Deleted content persists in backups taken before deletion (the forget report lists them)
  and in exports.
- The API uses one static token per installation, without TLS; same-user processes can read
  it (as they can read the database). Data directories that existed before JARVIS (e.g. a
  custom `JARVIS_HOME`) keep their permissions; `jarvis doctor` shows them.
- In-process handlers cannot be cancelled once started.

## Unverified assumptions
- Termux's Python ships SQLite with FTS5 (a fallback exists if not). Its SQLite may lack
  compile-time `secure_delete`; JARVIS enables it per connection.
- The default `gemini-2.5-flash` model name is still served (configurable).

## Next highest-priority task
1. Mission 002 review, then Mission 003 (`docs/ROADMAP.md`, R7): real capability expansion
   (read-only workspace search, a single allow-listed GET reader, objective scheduling,
   re-decomposition) under the same verification rules.
2. The owner authorises publishing, then:
   - recreate the reviewed commits deterministically and verify their IDs;
   - push `arena/07f8421e-jarvis` and open one replacement PR into `main`;
   - close PR #1 as superseded once authorised.
   Unblock CI (above) and let the 3.10–3.13 matrix pass before the release is called ready.
3. Then R1 in ROADMAP.md: verify 3.10/3.13 and Termux, including a first real-device run of
   `jarvis voice`.
4. Then R3 (EXP-002 semantic retrieval on a held-out retrieval_v2).

## Do not change without reconsideration
`Policy.decide` inputs; the erasure policy (ADR-0011) and journal minimisation; the API
token requirement; append-only events (`forget` content scrubbing is the one deliberate
exception); applied migrations; supersession semantics; evidence rules for completion; the
data-locality rule for providers; pinned eval fixtures.
