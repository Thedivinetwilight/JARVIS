# Roadmap

## Completed
### M0 — Core foundation (v0.2, PR #1)
SQLite persistence, local-first config, Gemini adapter, permissioned tools, CLI and
loopback API. (Its tests first ran on 2026-10-17: 14 passed.)

### M1 — Genesis 0.1: persistent cognitive kernel (this milestone, v0.3.0)
Migrations + backups, experience log, identity/sessions, versioned memory with provenance
and corrections, goals/tasks with evidence, operation journal with crash recovery,
autonomy levels and approvals, deterministic interpreter and appraisal, observable cycle,
provider interface (Gemini header auth, OpenAI-compatible), CLI chat/resume, API
hardening, EXP-001. Pre-release audit (2026-10-17): erasure policy, journal minimisation,
API token; second-pass review (races, disclosure, private files): 162 tests. See STATUS.md and
PRE_RELEASE_AUDIT.md.

## Next, in priority order
Ordered by dependencies, usefulness, risk and cost.

| ID | Item | Why now | Exit criteria |
|---|---|---|---|
| R1 | **Reproducible verification**: restore GitHub Actions (billing — owner action), add a `-W error::ResourceWarning` 3.13 job, run the suite on Termux. | Compatibility claims for 3.10/3.13/Android are currently unverified. | CI green on 3.10–3.13; Termux run recorded in STATUS. |
| R2 | **Voice companion (Android)** — *implemented 2026-10-17, simulated-device tests only*: `jarvis voice` loop using Termux:API (`termux-speech-to-text` → kernel → `termux-tts-speak`) behind a capability probe. Remaining: real-device verification, wake word / push-to-talk, desktop speech backends. | Highest user value ("speak naturally"); no new Python dependencies; kernel unchanged. | Real-device transcript recorded in STATUS; recognition errors handled (confirmation before writes from low-quality transcripts). |
| R3 | **EXP-002 semantic retrieval**: build retrieval_v2 (more paraphrases, scope-sensitive queries); compare FTS5 vs a small local embedding model (via Ollama `/v1/embeddings` or similar) vs hybrid. | Paraphrase recall ≈0/3 is the measured weakness. | Adopt only if Recall@5 on paraphrases improves with no regression on retrieval_v1 and acceptable phone latency. |
| R4 | **Plans that survive failure**: multi-step plan records (steps, preconditions, expected outcomes, budgets, stopping conditions), replanning after a failed step, resumption at the first unverified step. | Builds on the journal and evidence rules; needed for "carry out approved work". | Acceptance tests for failed-step replanning and interrupted plan resumption. |
| R5 | **Sandboxed job runner**: subprocess execution with timeouts, resource limits, cancellation; git-worktree based code changes with test gates. | Enables coding assistance and true cancellation. | No in-process side effects for long jobs; cancel kills the job; outcome journaled. |
| R6 | **Reasoning strategy selector**: route problems to deterministic tools, search, or model drafting + independent verification, by risk and verifiability; record which strategy was used. | Spec §5.5/E1; reuses verification hooks. | Fixed problem set with independently checkable answers; per-strategy accuracy reported. |
| R7 | **Self-model calibration and resources**: track correction rates per source/kind to calibrate confidence labels; CPU/RAM/storage reporting; provider reachability probe on demand. | Makes uncertainty statements evidence-based. | Calibration table in STATUS; no fabricated precision. |
| R8 | **Consolidation**: periodic, evidence-preserving summaries of sessions and recurring facts; duplicate detection; obsolete-information flags; FTS5 `secure-delete` when SQLite ≥3.42. | Keeps memory useful as it grows. | Summaries never change epistemic status; originals recoverable. |
| R9 | **Interfaces**: browser dashboard with real status only (auth + CSRF already partly in place); daily briefing with notification limits. | Visibility for non-CLI use. | No fabricated indicators; same API contracts. |
| R10 | **Learning pipeline**: learning-candidate records, isolated experiments on branches/worktrees, fixed regression + safety suites, human approval, rollback. | Spec Part 6; only after R1–R7 make measurement trustworthy. | Pipeline demonstrated on one procedural improvement with baseline comparison. |

### Follow-ups from the pre-release audit (2026-10-17)
- Let the user resolve an `interrupted` operation's unknown outcome (record what actually
  happened as user-provided evidence), instead of it staying unknown forever.
- A command to erase or suppress conversation turns the user selects. The forget report
  lists the ids of unlinked messages that still mention the memory, so they could be
  offered for deletion.
- `jarvis doctor --fix-permissions` for data directories created before JARVIS tightened them.
- Backup retention and encryption; offer to remove pre-migration backups after a forget.
- Enable FTS5 `secure-delete` once SQLite ≥ 3.42 can be required (R8).

## Not planned without new justification
Training a foundation model; autonomy levels 4–5; arbitrary shell execution by the
model; financial or physical actuation; multi-agent decomposition.
