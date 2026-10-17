# Genesis 0.1 acceptance suite — results

Suite: `tests/acceptance/test_acceptance.py`. Executed 2026-10-17 on Linux x86_64,
Python 3.11.2, SQLite 3.40.1: `python -m pytest tests/acceptance -v` → **15 passed**.
Full suite (`python -m pytest`): **162 passed** after the pre-release audit and its second-pass
review: 14 original + 111 Genesis + 27 audit + 9 review regression tests + 1 review coverage test
(forget approved over HTTP). The 125 pre-audit tests pass unchanged.

Mutation check: eight deliberate defects were injected one at a time in a scratch copy;
each was caught by the listed tests (marked †), and the unmutated control run passed. The
pre-release audit added a second mutation check: each of its 16 fix points, reverted alone,
made its regression test fail (see `docs/PRE_RELEASE_AUDIT.md` §3). The second-pass review
re-ran it together with its own 13 fix points: 29/29 (§11).

| # | Test | Precondition / fixture | Operation | Expected outcome | Failure condition | Result | Evidence |
|---|---|---|---|---|---|---|---|
| 1 | Persistent identity | empty home | `jarvis identity` in two separate processes | same `system_id`; 1 identity row; 2 runtime rows, both ended cleanly | new id or duplicate identity | PASS | `test_01_…` |
| 2 | Cross-session memory | empty home | process A: `ask "remember that …"`; process B: `chat` asks about it | B's answer contains the fact + "you told me"; 2 distinct sessions | fact missing or same session | PASS | `test_02_…` |
| 3 | Relevant retrieval | pinned `evals/retrieval_v1.json` | EXP-001 harness + provenance check | Recall@5 ≥ 0.9 (measured 0.933) > baseline 0.033; stale hits 0; hits carry source, status, time | lower recall, stale hit, missing provenance | PASS (regression guard on a development set; threshold set by the developer — not independent validation) | `test_03_…`, EXP-001 |
| 4 | Correction † | app | remember 12V; "actually … 24V" | only 24V retrieved; history keeps both with sources; "12V" no longer findable | overwrite, stale retrieval, lost history | PASS | `test_04_…` |
| 5 | Goal continuity | empty home | 3-step goal; done step 1; restart; `resume` | 1/3 done with evidence label; next step named | lost or invented progress | PASS | `test_05_…` |
| 6 | Interruption recovery † | real child process | child starts a non-idempotent op; SIGKILL mid-run; new process after deadline | op `interrupted`, outcome unknown; not re-run by replay or approval; 1 execution total | `started`/`succeeded`, or 2nd execution | PASS | `test_06_…` |
| 7 | Execution outcome integrity † | failing, unverifiable, slow and good test tools | run each; try a failed op as evidence | requested/attempted/executed/observed/verified correct per case; failed op rejected as evidence | any stage misreported | PASS | `test_07_…` |
| 8 | Provider failure | local fake model server | timeout, HTTP 500, malformed JSON, server down | classified outcomes; local fallback answer; observations kept; key only in header, never in DB/URL | crash, silent failure, leaked key | PASS | `test_08_…` |
| 9 | Permission enforcement † | scripted model proposing a write with "override" text; dangerous tool; autonomy 0 | ask; run tools | proposal only (nothing written); dangerous denied; unknown tool denied; level 0 denies | any execution | PASS | `test_09_…` |
| 10 | Emotional uncertainty | ambiguous and frustrated wording | ask; then "I'm not frustrated" | no stated emotion; inferred readings low/medium; no "you are/you feel …" assertions; revision recorded | asserted emotion | PASS | `test_10_…` |
| 11 | Task resumption † | 3-stage goal; external tool fails then succeeds | attempt, restart, resume, retry | failed attempt visible after restart; no completion without succeeded+verified op | fabricated completion | PASS | `test_11_…` |
| 12 | Regression | original `tests/test_core.py` (unchanged) | run in a subprocess | 14 passed | any failure | PASS | `test_12_…` |
| 13 | Data integrity | v0.2 DB; broken migration; newer schema; corrupt file | open each | rollback with data intact + backup; lossless v0.2 migration; refusal without modification; clear error | data loss or modification | PASS | `test_13_…` |
| 14 | Traceability | app | one "remember that …" cycle | observation → interpretation → retrieval → plan → operation → memory (source_event_id) → validation → response share one correlation id | broken links | PASS | `test_14_…` |
| 15 | Capability accuracy | app | capability report + "what can you do?" | registered tools = reported tools; vision/web search reported not implemented; voice adapter reported *unavailable* on a host without Termux:API; provider unavailable; real outcome counts | over-claiming | PASS | `test_15_…` |

Additional mutation-checked tests: claims guard (`test_kernel::test_unsupported_action_claims_are_flagged`),
API Host check (`test_api::test_cross_site_and_rebinding_requests_are_rejected`), episodic
leak regression, on-disk erasure (`test_memory::test_forget_erases_content_from_disk_…`).

Pre-release audit regression tests (2026-10-17): `tests/test_erasure.py` (erasure through the
chat, CLI and migration paths; journal content; session summaries; restatements and unlinked
mentions; over-deletion guards for single-word values; cancelled proposals; WAL honesty;
retention purge; concurrent idempotency),
`tests/test_api.py` (token required for every endpoint but `/health`, including the real
`jarvis serve` process; 404 for unknown operations; token file permissions) and new
interpreter cases. Note: the earlier on-disk erasure test covered only the direct `Store`
API; the realistic chat/CLI paths left copies until the audit fixes.

Second-pass review tests (the 9 regression tests each fail on the pre-review code; the HTTP
forget test covers an existing guarantee on one more path):
- partial-restatement disclosure and forget during a running cycle (`tests/test_erasure.py`);
- forget cost bounded per table (`tests/test_erasure.py`);
- deny racing an approval, and concurrent approvals (`tests/test_tools_policy.py`);
- owner-only data files (`tests/test_migrations.py`, `tests/test_cli.py`);
- API token redaction, IPv6 loopback `serve`, and forget approved over HTTP (`tests/test_api.py`).

Change log: when the voice adapter was added (R2), test 15 was updated — voice moved from the
"not implemented" list to an interface reported as *unavailable* on this host. The test's
intent (never report an unavailable capability as working) is unchanged.

Not covered by automated acceptance tests: Python 3.10/3.12/3.13 and Android/Termux
runtime behaviour (CI blocked by billing; interpreters unavailable in the sandbox), real
Gemini or Ollama endpoints (tested against a local fake HTTP server only).
