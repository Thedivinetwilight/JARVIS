# Development status (authoritative) — updated 2026-10-17

Version `0.3.0` · kernel generation `genesis-0.1` · schema version 2 · branch
`arena/07f8421e-jarvis`: history rebuilt on top of PR #1's unchanged commits (`54b9e6b`).
**Not pushed**; no pull request opened, modified or closed. **Not ready for release** until
the CI matrix has run and passed.

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
| `python -m pytest` (dev venv) | **162 passed** in ~25 s (14 original v0.2, unchanged and byte-identical to PR #1 · 111 Genesis · 27 audit · 9 review regression + 1 review coverage test) |
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
- **Python 3.10, 3.12 and 3.13:** declared in `pyproject.toml`, but only 3.11.2 was executed;
  static checks are clean.
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
- Planning: at most 4 steps mapped from one intent; no autonomous multi-step execution or replanning.
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

## Blocked by environment
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
1. The owner authorises publishing, then:
   - recreate the reviewed commits deterministically and verify their IDs;
   - push `arena/07f8421e-jarvis` and open one replacement PR into `main`;
   - close PR #1 as superseded once authorised.
   Unblock CI (above) and let the 3.10–3.13 matrix pass before the release is called ready.
2. Then R1 in ROADMAP.md: verify 3.10/3.13 and Termux, including a first real-device run of
   `jarvis voice`.
3. Then R3 (EXP-002 semantic retrieval on a held-out retrieval_v2).

## Do not change without reconsideration
`Policy.decide` inputs; the erasure policy (ADR-0011) and journal minimisation; the API
token requirement; append-only events (`forget` content scrubbing is the one deliberate
exception); applied migrations; supersession semantics; evidence rules for completion; the
data-locality rule for providers; pinned eval fixtures.
