# Roadmap

## M0 — Core foundation (this branch)
- [x] SQLite persistence for projects, tasks, memory, and audit.
- [x] Local-first config and Gemini REST adapter.
- [x] Permissioned deterministic tool registry.
- [x] CLI and loopback-only JSON API.
- [x] Initial tests and architecture/security docs.

## M1 — Reliability
- [ ] Run tests in Android/Termux and desktop CI.
- [ ] Add schema migrations and database backup/restore.
- [ ] Add audit-chain integrity verification.
- [ ] Add task dependencies, recurring schedules, and project checkpoints.
- [ ] Add provider retries with bounded exponential backoff and rate limits.

## M2 — Execution
- [ ] Add a sandboxed job runner with process timeouts and resource limits.
- [ ] Git inspection and diff-based code changes on an isolated worktree.
- [ ] Verification gates: tests, lint, type checks, artifact checksums.
- [ ] Human approval UX for write operations, with policy that model text cannot override.
- [ ] Per-tool budgets, cancellation, and idempotency.

## M3 — Interfaces and integrations
- [ ] Browser dashboard with real status only.
- [ ] Voice input/output as optional adapters.
- [ ] Calendar and file-service connectors behind explicit scopes.
- [ ] Project-specific adapters for AXIOM, Solar, and DUM-E.
- [ ] Notifications and daily briefing.

## M4 — Improvement loop
- [ ] Build a benchmark set from real failures and tasks.
- [ ] Candidate changes run in isolated branches/worktrees.
- [ ] Evaluate on fixed regression, security, and adversarial suites.
- [ ] Promote only if measurable scores improve and rollback is available.
- [ ] Never allow self-modification to rewrite policy or disable tests.
