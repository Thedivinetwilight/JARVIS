# Pre-release verification and security audit — Genesis 0.1 (2026-10-17)

Purpose: decide whether the persistence, privacy, security and reliability foundations of
the unpushed Genesis 0.1 release are fit to publish, by testing the implementation (not
its documentation). Everything below was executed; nothing is inferred from docs.

## 1. Release state at the start of the audit
- Branch `arena/07f8421e-jarvis`, local Genesis 0.1 commits on top of draft PR #1, working tree
  clean, nothing pushed. Remote: `main` = `210379a`; `feat/jarvis-core-v0.2` = `54b9e6b` = PR #1.
- Over PR #1: 56 files, +9396/−435, no deletions; `tests/test_core.py` and
  `.github/workflows/tests.yml` unchanged.
- Correction (second pass, §11): the local commit IDs recorded during this audit did not
  survive the development environment, which persists working files but not unpushed git
  history. They are therefore not cited here. The publishable history is rebuilt on top of
  PR #1's unchanged commits `8fb69be`, `57fa036` and `54b9e6b`.
- Dependencies: runtime `dependencies = []` (unchanged); version 0.2.0 → 0.3.0.
- Hygiene: no credentials, databases, logs or temporary files tracked. The only key-shaped
  strings were synthetic test fixtures that matched the real Google/AWS key formats; they
  are now assembled at runtime (identical values). `.gitignore` now also excludes
  `*.sqlite3*`, `api_token` and `.jarvis/`.
- Environment: Linux x86_64, Python 3.11.2, SQLite 3.40.1 (compiled with
  `SQLITE_SECURE_DELETE`; FTS5 `secure-delete` needs ≥ 3.42 and is unavailable).

## 2. Method
Isolated databases under `/tmp` with unique marker strings; only real entry points (kernel
cycle, CLI subprocesses, `jarvis serve` over real HTTP, the tool registry, migrations);
inspection of every table including FTS5 shadow tables (`*_data`, `*_idx`, `*_content`,
`*_docsize`, `*_config`), FTS `MATCH` and `fts5vocab`, and the raw bytes of the database,
`-wal`, `-shm`, backups and exports; reopen and process restart; SIGKILL crash tests; and a
mutation check of every fix (each fix reverted alone must make its regression test fail).

## 3. Defects confirmed and corrected

| ID | Sev. | Defect (as reproduced) | Fix | Regression test |
|---|---|---|---|---|
| P1 | High | `forget` left memory content in the operation journal: `result_json` of every registry write (chat, CLI `memory correct`, API) held the full value and key; `arguments_json` and `operation.*` payloads kept the key; `memory.search` and `workspace.read` results journaled memory values and file text — contrary to the journal's own contract. | Tools declare `journal_result` (content-free summaries: ids, status, counts); `key` and search `query` are redacted arguments; `forget` scrubs linked journal rows. | `test_erasure::test_journal_never_stores_memory_or_file_content`, `::test_forget_through_cli_erases_the_journal_copy` |
| P2 | High | Linked conversation copies survived: replies quoting the value (recall answer, correction confirmation, the forget proposal itself), the user's recall question and forget request (the key), and their `episode_fts` entries (still searchable). | `jarvis/erasure.py`: linked turns are erased (see §4). | `test_erasure::test_forget_through_chat_leaves_no_copy_in_tables_indexes_or_file` |
| P3 | Med. | Derived summaries kept text: `sessions.summary_json.last_user_message` and the `session.ended` payload. | Verbatim sweep of derived records. | `::test_session_summaries_lose_the_forgotten_text` |
| P4 | Med. | Migrated v0.2 audit rows kept the memory key in `events.target`. | Sweep covers event targets. | `::test_migrated_v02_memory_is_erasable_even_without_compiled_in_secure_delete` |
| P5 | Med. | Migration connections did not enable `secure_delete` (a per-connection setting). On SQLite builds without the compile-time default the dropped v0.2 tables kept rows in free pages: the value stayed in the file after `forget` (reproduced by simulating such a build). | `PRAGMA secure_delete=ON` in `migrations._open`. | same test |
| P6 | Med. | Verification overstated success: the verifier checked only `memory_items` and `memory_fts` and reported "verified" while copies remained; the WAL checkpoint result was discarded. While fixing: a TRUNCATE checkpoint waits for readers for the full 10 s busy timeout, which pushed `memory.forget` past its 10 s tool timeout and would misreport a committed erasure as "outcome unknown". | Verifier re-reads every version and the index and requires zero residue; report states `wal_truncated`; the reply states remaining caveats; checkpoint wait bounded (250 ms). | `::test_forget_reports_a_wal_it_could_not_truncate` |
| P7 | Low | "forget my X" and "delete memory 1" were interpreted as chat: nothing was forgotten and the request was stored as a recallable episode (and could reach a model). | Interpreter patterns (statements such as "I always forget my keys" stay chat). | `test_interpret_affect::test_interpreter_intents`, `::test_forget_commands_do_not_capture_statements` |
| P8 | Low | The start-up retention purge deleted expired appraisals without truncating the WAL. | Checkpoint after a purge. | `test_erasure::test_retention_purge_of_expired_appraisals_truncates_the_wal` |
| A1 | High | The HTTP API had no client authorisation. With real HTTP against `jarvis serve`, a client holding no credentials read every memory (`/v1/memory/search`), the conversation log (`/v1/events`) and the journal (`/v1/operations`), and **approved an irreversible `memory.forget`**, which executed and was recorded as approved by `user:api`. Loopback is not an access control: other OS users and, on Android, other apps can connect to 127.0.0.1. | Bearer token stored in `<JARVIS_HOME>/api_token` (created 0600, constant-time comparison), required for every route except `GET /health`; 401 + `WWW-Authenticate`. API access now requires the same OS-level access as the database file. | `test_api::test_requests_without_the_token_cannot_read_or_approve` (6 variants), `::test_jarvis_serve_process_requires_the_token`, `::test_token_file_is_private_stable_and_regenerated_when_invalid` |
| A2 | Low | Approving/denying an unknown operation returned HTTP 500. | `UnknownOperation` → 404. | `test_api::test_unknown_operation_is_404_not_500` |
| O1 | Low | Two concurrent requests with one idempotency key executed once (safe), but the second caller got a raw `sqlite3.IntegrityError`; `propose()` raised on any reused key. | The recorded outcome is returned (`_journal_or_replay`). | `test_erasure::test_concurrent_requests_with_one_idempotency_key_execute_once_and_both_get_an_outcome` |

Mutation check: the fixes, together with the erasure policy's over-deletion guards, comprise
16 code points. Each point was reverted alone in a scratch copy, and its regression test failed
every time (16/16). The unmutated control run passed.

Over-deletion guard (found while building P2–P4, before anything was committed): a first
version of the verbatim sweep matched single words as substrings. Forgetting a one-word value
such as the verbosity preference "detailed" would then have erased every message containing
that word. Single words are now matched only structurally, and user turns only for long,
multi-word restatements. Tests: `test_erasure::test_single_word_values_never_erase_unrelated_history`
and `::test_single_word_v02_key_is_scrubbed_from_both_v02_audit_formats`. The latter uses both
real v0.2 audit formats: `memory.upsert` with target `<category>:<key>`, and `tool.memory.save`
with `"key": …` in its details.

## 4. Memory deletion: what `forget` does now
Policy, defined in `src/jarvis/erasure.py` and applied in one transaction:

| Representation | Decision |
|---|---|
| `memory_items`, every version (correction history) | content overwritten (value, key, evidence); tombstone row kept |
| `memory_fts` and its shadow tables | row deleted by trigger; segments merged (`optimize`) in the same transaction |
| source utterances (create / restate / correct) | text erased |
| linked turns: JARVIS replies in cycles that created, corrected, retrieved or proposed forgetting it; the user's turn when it was *about* the memory (recall question answered with it, correction, forget request) | text erased |
| `episode_fts` and its shadow tables | updated by trigger; segments merged |
| payloads of events about it and of journal events for operations that touched it | content fields removed; ids, kinds, statuses, times kept |
| journal arguments / results | content-free when written; linked rows scrubbed; pending proposals that target it cancelled |
| session summaries, `session.ended` payloads, migrated v0.2 audit rows, JARVIS-generated text and journal rows containing a multi-word value or key | scrubbed (verbatim phrase match) |
| single-word values or keys | scrubbed only as a JSON content field (`"key": "<word>"`) or an exact event target (`<word>`, v0.2 `<category>:<key>`); never as a bare substring, which would erase unrelated history |
| user turns repeating a full value verbatim (≥ 3 words and ≥ 12 characters) | text erased |
| user turns that only mention it — verbatim key/phrase, or sharing ≥ 2 distinctive words (e.g. a partial restatement) | **kept**, counted and reported with their event ids (`unlinked_user_mentions`, `possible_user_mentions`); the reply says they can still come up in conversation — the person's own words are not deleted on a word match |
| restatements in different words | **not detectable**; the reply says so |
| replies of conversation cycles still running when the forget commits | **withheld and never stored**: cycles record the memories they use as soon as they retrieve them, and re-check them in the same transaction that stores the reply (§11) |
| copies already sent to a remote model provider (only with network mode on; personal/sensitive memories are withheld by default) | **out of reach** |
| `memory.forgotten` event | kept: ids and counts only |
| `-wal` file | truncated after commit; if another connection blocks it the report says so and SQLite removes the WAL when the last connection closes |
| free pages / freed cells | zeroed by `secure_delete` (Store and migration connections) |
| `backups/` (pre-migration copies), user exports | **not touched**; backups written after the memory existed are listed in the report |
| filesystem journals, SSD remapping, swap, snapshots | **out of reach** |

Deletion levels (tested on isolated databases with unique markers, after reopen and restart):

1. **App-visible memory — erased.** Search, recall, episodic recall, resume and the API return nothing.
2. **Active records and indexes — erased.** No table cell (including FTS5 shadow tables), `MATCH` or `fts5vocab` term contains a marker.
3. **Recoverable content in database files and journals — erased in the tested cases.** Raw bytes of
   the database and WAL contain no marker after `forget`, provided the WAL checkpoint succeeds
   (otherwise reported). Relies on `secure_delete` plus FTS `optimize`.
4. **Backups and external copies — not erased.** Reported, never claimed.
5. **Physical media — not guaranteed.** Deletion is logical plus in-file overwrite; it is not a
   secure-erase of the storage device.

## 5. Local API security (real HTTP)

| Request | Before | After |
|---|---|---|
| no credentials: `GET /v1/memory/search`, `/v1/events`, `/v1/operations` | 200 with private content | 401 |
| no credentials: `POST /v1/operations/<id>/approve` (irreversible forget) | 200 — executed | 401 — still `proposed`, nothing recorded |
| wrong token / Basic auth / token without `Bearer ` | n/a | 401 |
| `Origin: https://evil.example`, `Origin: null` | 403 | 403 |
| `Host: evil.example` (DNS rebinding), missing Host | 403 | 403 |
| `text/plain` or form-encoded POST (CSRF "simple request") | 415 | 415 (401 first without a token) |
| `OPTIONS` preflight | 405, no `Access-Control-*` headers | unchanged |
| non-loopback bind (`serve --host 0.0.0.0`) | refused | refused |
| approve unknown operation | 500 | 404 |
| model-proposed operations via `/v1/ask` | journaled, never executed | unchanged; approval needs the token |

Browser-originated forgery was already blocked by the Host/Origin checks and the
`application/json` requirement (which forces a CORS preflight that is never granted); CORS
configuration alone was not relied on. A browser page cannot read the token file, and a
cross-origin `Authorization` header also requires a preflight.

## 6. Operational safety (real processes)

| Behaviour | Result |
|---|---|
| Interrupted operation (child SIGKILLed mid-run) | marked `interrupted`, outcome unknown; not re-run by restart, replay or approval (acceptance 6) |
| Unknown outcome | stays unknown; nothing turns it into success or failure. There is no user workflow to *resolve* it (gap, see §9) |
| Failures | reported with attempted/executed/observed/verified separated (acceptance 7) |
| Idempotency | replay returns the recorded outcome; mismatched arguments rejected; concurrent duplicates now return an outcome (O1) |
| Approvals | persist across processes: proposed in process 1, approved in process 2, process 3 sees "not executed again"; exactly one `operation.started` |
| Unauthorised operations | rejected by policy (acceptance 9) and, for the API, by the token (A1) |
| Secrets | a fake provider echoing the API key in a 401 body: key redacted in answer, events and on disk; `repr(Settings)` clean |
| Migrations | v0.2 data preserved; failed migration rolled back with backup (acceptance 13); SIGKILL in the middle of migration 2 left version 1 with v0.2 rows intact and migrated cleanly on reopen |
| Existing behaviour | original 14 tests and all 125 pre-audit tests pass unchanged |

## 7. Retrieval: EXP-001 reproduced
- Fixture `evals/retrieval_v1.json` SHA-256 `2bc43a6d…603dfd` matches the pin; re-run results are
  identical to `docs/experiments/EXP-001-results.json` except latency, before and after the fixes.
- Metrics recomputed independently from the per-query rankings (not with `evaluation.py`): all
  match. FTS5: Recall@5 = MRR@5 = Hit@1 = 0.933 (28/30); misses are q22 and q25 (paraphrases);
  q24's paraphrase "hit" overlaps only on the word "take"; superseded records in the top 5: 0/30.
- Limitations now stated in the EXP-001 report: development set written by the developer in the
  same commit as the retrieval code (not independent validation); 15 of 30 queries are lexical;
  genuine paraphrase recall is 0/3; the earlier statement that labels were written before any
  run cannot be verified from history. Retrieval is lexical, not semantic.

## 8. Environment and CI
Commands run (Python 3.11.2): `python -m pytest -o addopts= -q` → **152 passed** at the end of the
audit (**162** after the second pass, §11);
`python -m pytest tests/acceptance` → 15 passed; `python -m compileall -q src` → OK;
`vermin -t=3.10- --violations src tests` → no violations (minimum 3.9);
statement coverage with coverage.py 7.16.2 including subprocesses → 89 % (branch coverage not
measured; coverage is not a correctness claim). Clean install into a fresh virtual environment:
see STATUS.md.

Python 3.10 / 3.12 / 3.13 were **not run**: interpreter downloads redirect from github.com to
`release-assets.githubusercontent.com`, which this sandbox refuses (uv and npm portable-python
both depend on it). Static review found no 3.11+-only APIs.

GitHub Actions: 4 workflow runs (push/pull_request for `57fa036` and `54b9e6b`), 16 jobs, every
job with no runner and zero steps; annotation: *"The job was not started because recent account
payments have failed or your spending limit needs to be increased."* These jobs never ran: they
are neither passes nor code failures. Billing usage cannot be read with this session's token
(HTTP 403). Owner actions that do not require new spending: see STATUS.md "Blocked".
`ubuntu-latest` moves to Ubuntu 26.04 between 2026-10-19 and 2026-11-19 (runner-images #14748);
`actions/python-versions` already publishes 3.10.22, 3.11.17, 3.12.15 and 3.13.16 for 26.04 x64,
so no workflow change is needed.

## 9. Remaining risks
- Erasure cannot reach backups, exports, copies sent to a remote model, rewordings, or the
  physical medium (§4).
- Unlinked mentions in the person's own messages are reported, not erased, and can still
  resurface through episodic recall; there is no "delete conversation turns" command yet.
- No workflow to resolve an `interrupted` operation's unknown outcome.
- API token is a single static secret per installation (rotate by deleting the file); no TLS.
- Same-user malware can read the database directly; the token does not change that.
- Interpreter and redaction are rule-based (English only; pattern-limited).
- Untested environments: Python 3.10/3.12/3.13, Android/Termux, real Gemini/Ollama endpoints,
  a real voice device, concurrent multi-process stress.

## 10. Release decision
No critical defect remains after the corrections above and those of the second pass (§11).
The branch is **ready for review**, but it is **not ready for release**. The release requires
the GitHub Actions matrix (Python 3.10–3.13) to run and pass, which it never has (billing).
Nothing has been pushed, and no pull request has been opened, modified or closed, pending
explicit authorisation.

## 11. Pre-release review — second pass (2026-10-17)
An independent re-verification of the audited branch, with new probes (not the audit's own
tests) on isolated databases and real processes.

### State and PR findings
- **S1 (High, process; reproduced).** The audit's local commits did not exist in the
  environment. HEAD was `210379a` (= `main`), all Genesis + audit work was uncommitted in the
  working tree, and PR #1's commits were absent locally. Cause: the environment persists
  working files between sessions, not unpushed git history. Action:
  - captured a SHA-256 manifest of all 62 files;
  - fetched PR #1 (read-only) and moved the branch to `54b9e6b` with a mixed reset (index and
    ref only);
  - verified all 62 files byte-identical; `tests/test_core.py` and the CI workflow are
    byte-identical to PR #1.
  Publishable commits must be recreated immediately before pushing. This is done
  deterministically (fixed metadata) so that the reviewed commit IDs can be re-verified.
- **PR decision.** PR #1 (draft, head `feat/jarvis-core-v0.2` = `54b9e6b`, 3 commits, no
  reviews or comments) cannot be updated in place from this session: a PR's head branch cannot
  be changed, and this session may push only to `arena/07f8421e-jarvis`. Proposed:
  - one replacement PR `arena/07f8421e-jarvis → main`, containing PR #1's three commits
    unchanged plus the new commits;
  - then close PR #1 as superseded (not merged), so that no duplicate stays open.

### Findings (all reproduced before fixing; each fix has a regression test that fails on the pre-review code)
| ID | Sev. | Finding | Fix | Test |
|---|---|---|---|---|
| R1 | Med. | A later partial restatement of a forgotten memory survived (by design), resurfaced through episodic recall ("Earlier you said: …"), and the report claimed `unlinked_user_mentions: 0`. | Remaining user turns sharing ≥ 2 distinctive words are counted (`possible_user_mentions`) with their ids. The reply discloses them, says they can still come up, and says that rewordings cannot be detected. Nothing is deleted on a word match. | `test_erasure::test_partial_restatements_are_kept_but_disclosed` |
| R2 | Med. | Race: a second runtime mid-cycle (waiting for its model) with the memory in context answered with it and stored the reply after the forget had reported success. | Cycles record the memories they use at retrieval time. The RECORD stage re-checks memories and episodes in the same write transaction as the reply; if any was forgotten meanwhile, the answer is withheld and not stored. | `test_erasure::test_reply_of_a_cycle_running_during_forget_is_withheld_and_never_stored` |
| R3 | Low–Med. | Forget cost grew with the number of search terms: 109 full event-table scans; 6.2 s at 100k events against a 10 s tool timeout, so a large history could be misreported as "outcome unknown". | One streaming pass per table for the sweep and one for the check (0.9 s at 100k events); forget timeout 120 s. | `test_erasure::test_forget_cost_does_not_grow_with_the_number_of_search_terms` |
| R4 | Med. | Deny racing an approval: the operation executed, but deny's unconditional journal update turned it into `cancelled`. The denier was told "cancelled", and the event log read proposed → started → succeeded → cancelled. | Terminal transitions decided from an earlier read are conditional (`OperationJournal.finish(expect_status=...)`), checked inside the write transaction; the loser receives the true status. | `test_tools_policy::test_deny_racing_an_approval_never_reports_an_executed_operation_as_cancelled` |
| R5 | Low | Concurrent approvals executed once, but the losers received a raw `ValueError` (a traceback through `jarvis approve`). | The losers receive the recorded outcome ("…it was not executed again"). | `test_tools_policy::test_concurrent_approvals_execute_once_and_every_caller_gets_an_outcome` |
| R6 | Med. | Under the default umask 022 the data directory was created 0755 and the database 0644. Any other account on a shared machine could read every memory directly, bypassing the API token; backups and exports were 0644 too. | Directories JARVIS creates are 0700. The database, WAL/SHM (which inherit its mode), backups and exports are 0600, and an existing world-readable database is tightened. Directories the user created are reported by `jarvis doctor`, not changed. | `test_migrations::test_personal_data_files_are_owner_only_even_with_a_permissive_umask`, `test_cli::test_export_is_owner_only_and_doctor_reports_permissions` |
| R7 | Low | The API token pasted into a conversation was stored verbatim (configured secrets are documented as redacted). | The token is registered for redaction at start-up and by `serve`. | `test_api::test_an_existing_api_token_is_redacted_from_conversation` |
| R8 | Low | `serve --host ::1` passed the loopback check, then crashed (IPv4-only server; fail-closed). | IPv6 server class for IPv6 loopback. | `test_api::test_jarvis_serve_binds_the_ipv6_loopback` |
| D1 | Low | Docs: "every request except /health needs the token" was imprecise: HEAD/PATCH return 501 and PUT/DELETE/OPTIONS 405 without one, and none of them returns data or acts. Stale commit IDs in docs. | Wording corrected; IDs removed. | — |

Mutation check (second pass): all 16 audit fix points and all 13 review fix points (29 mutants)
were reverted one at a time; each made its regression test fail; the unmutated control passed.
One audit test needed a stronger assertion. Raising the forget timeout had hidden the
"checkpoint waits for the full busy timeout" regression behind a slower but passing run, so the
test now also asserts that a forget blocked by a reader returns within 5 s.

### Verified and holding (new probes)
- Erasure catalogue: before forget, a memory lived in ~14 representations (memory rows, both
  FTS5 indexes with their content/data shadow tables and vocabulary, events, a session
  summary, journal rows). After forget, every linked and derived copy is gone, including
  marker *fragments* in raw bytes (FTS5 prefix-compresses index terms). No `-wal`/`-shm` file
  remained; the only survivor was the deliberately unlinked partial restatement (now disclosed).
- Failure states:
  - an exception injected mid-erasure rolled back atomically (memory, FTS row and source turn
    intact; outcome `failed`);
  - SIGKILL inside the forget transaction rolled back, the operation became `interrupted` after
    restart, and a fresh forget then succeeded;
  - SIGKILL after COMMIT left no raw residue: the committing connection's close checkpointed
    and removed the WAL. The operation is honestly `interrupted` (outcome unknown);
  - forget works without FTS5.
- API boundary (real `jarvis serve`):
  - all 13 data/action routes (10 GET, 3 POST): 401 without a token or with a wrong one, OK
    with the right one;
  - unknown paths are also 401 (no enumeration);
  - token: 32 random bytes, 0600 file, absent from stdout/stderr, the process command line and
    its environment;
  - non-loopback binds (flag or `JARVIS_HOST`) are refused.
- Idempotency: 8 threads and 4 separate processes with one key, exactly one execution; every
  other caller gets a replayed outcome, none an exception.
- Approval persistence across processes, and `approve` re-checking policy, unchanged and passing.
