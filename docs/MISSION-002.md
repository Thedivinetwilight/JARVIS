# Mission 002 — Objective-driven autonomy

**Status:** implemented, tested, evaluated. Branch `arena/0680961f-jarvis`, schema 4.
**Scope:** turn “JARVIS can execute a plan I hand it” into “JARVIS can be given an objective and
carry it to a verified, honestly reported outcome”.

| | |
|---|---|
| Tests | **279 passed** locally (Python 3.11.2, SQLite 3.40.1); 25 new objective tests |
| CI | `.github/workflows/tests.yml` matrix 3.10–3.13 + `jarvis eval retrieval` + `jarvis eval objectives` |
| Evaluation | 17/17 scenarios reached their expected outcome; `false_completion_rate = 0.0`, `safety_failures = 0` |
| Migration | schema 4 (new `objectives` table; plan step dependencies/criteria; objective ids on events) |
| Release | not released; `main` not touched |

## A. Architecture changes

The new layer is thin on purpose: it interprets, plans, judges and reports, but it does not
execute. Every step still runs through `ToolRegistry.run` → `Policy`, so permissions, scopes,
egress and the operation journal are unchanged and cannot be bypassed by anything the planner
produces.

```
owner request
   │
   ├─ memory context (bounded MemoryQuery, never a full-memory fan-out)   [objectives.py]
   │
   ├─ RulesPlanner  ── deterministic decomposition, offline, reproducible  (ADR-0020)
   │     └─ no match? ModelPlanner (provider JSON) → validate → one repair (ADR-0021)
   │
   ├─ ObjectiveStore → objectives row (request, objective, criteria, budget, origin, planner)
   │     └─ PlanStore.create_plan (steps with depends_on, criteria, expected output, risk)
   │
   ├─ ObjectiveService.run loop
   │     ├─ PlanExecutor.execute(origin, should_cancel, max_seconds)
   │     │     ├─ dependency gating (`{{stepN.path}}` result references resolved from the journal)
   │     │     ├─ approval pause/resume, bounded per-step retry, interrupted-step recovery
   │     │     └─ stops on failure but keeps running independent later steps
   │     ├─ verify_plan → per-step evidence (operations.verified) + objective criteria
   │     └─ diagnose → one sanctioned repair (transient, postcondition, alternative) or an honest stop
   │
   └─ outcome: completed / partially_completed / failed / blocked / needs_approval /
               cancelled / awaiting_information → API, CLI, events
```

## B. Files

**New**
- `src/jarvis/objectives.py` — objective record + store, rules planner, untrusted model planner,
  plan validation, execution service, failure diagnosis, alternatives, verification, reporting.
- `src/jarvis/objective_eval.py` — the 17-scenario evaluation suite and the ten metrics.
- `tests/test_objectives.py` — 25 tests (23 integration/API/CLI, 2 validation/evaluation groups).
- `docs/MISSION-002.md` (this record).

**Changed**
- `src/jarvis/contracts.py` — `ObjectiveOutcome` enum (7 explicit outcomes).
- `src/jarvis/migrations.py` — migration 4: `objectives` table + indexes; `plans.objective_id`;
  `plan_steps.depends_on/success_criteria/expected_output/optional/risk/replaced_from`;
  `events.objective_id` (text, indexed). `LATEST_VERSION = 4`.
- `src/jarvis/plans.py` — step metadata, dependency gating, `{{stepN.path}}` resolution,
  cancellation/budget hooks, `skipped` state, `reopen`, `replace_step`, `replaced_from`, plan
  `objective_id`, progress counts.
- `src/jarvis/tools.py` — public `ToolRegistry.validate(capability, arguments)` for planners.
- `src/jarvis/events.py` — `objective_id` on appended events.
- `src/jarvis/core.py` — `Jarvis.objectives`; start-up recovery marks unfinished objectives
  `interrupted`; `state()["subsystems"]["objectives"]` counts outcomes.
- `src/jarvis/api.py` — `GET/POST /v1/objectives`, `GET /v1/objectives/{id}`,
  `POST /v1/objectives/{id}/(cancel|resume)`; scopes `plans.read` / `plans.write`.
- `src/jarvis/cli.py` — `jarvis objective "…"`, `--list/--show/--resume/--cancel/--plan-only/
  --budget/--priority/--deadline/--json`; `jarvis eval objectives`.
- `tests/test_migrations.py`, `tests/test_cli.py` — schema 4 expectations.
- `.github/workflows/tests.yml` — objective evaluation step.
- `docs/STATUS.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` (ADR-0020/0021),
  `docs/ROADMAP.md`, `README.md`.

**Untouched on purpose:** `evaluation.py` (legacy experiment harness), `kernel.py` (the
cognitive cycle keeps its own bounded planning), memory/goal/reminder services.

## C. Features

**Objective representation.** `objectives` carries id, raw request, normalised objective,
desired outcome, constraints (including retrieved-context notes), success criteria, memory ids,
plan id, status, outcome + reason, priority, deadline, execution budget, verification report,
planner, origin, attempts, replans and timestamps. A one-clause request produces a one-step
objective: nothing is padded to look autonomous.

**Automatic planning.** `RulesPlanner` splits a request on `;`, `then`, sentence ends and (only
when both halves match) `and`, then maps clauses to capabilities: clock, arithmetic, memory
write/search, goal, task, goal+task chain, project/task/goal listings, workspace listing, and
reminders. It inserts dependencies and a `{{stepN.id}}` reference where a later step needs an
earlier result, and it **refuses** what it cannot do (web research → `blocked`, with the missing
capability named and no substitute invented). Missing detail (a reminder with no time) becomes
`awaiting_information` with a question. Bounds: 5 clauses, 12 steps, 4000 characters.

**Plan validation.** `validate_plan_spec` rejects: empty plans, > 12 steps, unknown
capabilities, arguments that do not match the tool schema exactly (a typed probe stands in for
`{{stepN.…}}` placeholders), steps without a purpose, dependencies or references that are not
earlier steps, and unknown criterion kinds. Model plans additionally get one repair attempt.

**Execution engine.** `PlanExecutor` gained: dependency gating (unmet dependency → `skipped`
with `dependency_not_met`, never executed), `{{stepN.path}}` resolution from journaled results
(`unresolved_reference` on failure), cooperative cancellation between steps, a wall-clock
budget, and continuation past a failed step for independent later steps. Existing behaviour is
preserved: approval pauses, bounded per-step retry, interrupted-step recovery, write-ahead
journal, verified postconditions.

**Tool selection.** Capabilities come from the registry only; the rules planner validates each
step through `registry.validate`, and the model planner can only name capabilities that exist.
The evaluation checks the selected capabilities, not just the outcome.

**Verification.** `verify_plan` judges the objective from recorded evidence: a step counts only
when its operation row says `verified = 1`; a tool without a postcondition is reported as
`succeeded_unverified`, not as verified; a model's prose is never consulted. Criteria:
`required_steps_verified`, `capability_succeeded`, `min_succeeded`, `answer_provided`,
`no_failed_required_step`. A sanctioned capability substitution carries `replaced_from`, so the
original criterion can still be judged.

**Bounded diagnosis and replanning.** `diagnose()` classifies each failure
(needs_approval, approval_denied, missing_capability, invalid_arguments, policy_denied,
blocked_dependency, unresolved_reference, verification_failed, transient, tool_error). Repair
families are each allowed **once**: retry a transient failure, retry a failed postcondition, or
substitute an explicitly mapped alternative capability (memory.remember ↔ memory.save,
task.add ↔ task.create) whose arguments are re-validated. Denials, policy refusals, invalid
arguments and blocked dependencies are never repaired; a missing capability with no alternative
ends `blocked`. `MAX_REPLANS = 3` and the budget are absolute.

**Memory.** Retrieval is one bounded `MemoryQuery` (≤ 5 hits) over the request text; ids are
recorded on the objective and a context note is added. Objective/task state lives in its own
tables and is never written to long-term memory; only explicit `memory.remember` steps write
memory. After a restart unfinished objectives are marked `interrupted` (never silently resumed
or completed), listed in the recovery report, and can be resumed by id.

**Outcomes.** Seven values, produced by one function, surfaced by
`GET /v1/objectives/{id}` (`objective.outcome`), by `Jarvis.state()`, and by the CLI. `answer`
is deterministic text assembled from step outcomes and evidence — never a model narrative.

**Security.** Model-authored plans are untrusted even for an owner-typed objective: such an
objective is persisted with origin `model_proposal`, so writes pause for approval. A denied
approval is final and is never re-attempted through an alternative (tested). Risk comes from the
registry, not the plan JSON. Nothing in this mission adds a network path; web research remains
`blocked`.

## D. Test results

`python -m pytest` → **279 passed in 41.47s** (Python 3.11.2, SQLite 3.40.1).
New coverage in `tests/test_objectives.py` (25 tests):

| Behaviour | Test |
|---|---|
| simple objective, verified evidence, event trail | `test_simple_objective_completes_with_verified_evidence` |
| two clauses → dependency + result reference, real goal/task rows | `test_multi_clause_objective_uses_a_dependency_and_a_result_reference` |
| memory used as context, not written | `test_objective_reads_relevant_memory_as_context_and_writes_none`, `test_memory_retrieval_is_bounded` |
| missing capability blocked, nothing attempted | `test_missing_capability_blocks_instead_of_inventing_a_tool` |
| missing time → asks | `test_reminder_without_a_time_asks_instead_of_guessing` |
| approval pause → approve → resume completes | `test_model_origin_write_pauses_for_approval_then_resumes` |
| denial is final, no substitute write | `test_denied_approval_is_not_retried_through_an_alternative` |
| cancellation, owner cancel, budget | `test_cancellation_…`, `test_owner_can_cancel_…`, `test_exhausted_budget_…` |
| transient retry, permanent stop, failed postcondition, alternative, dependency skip | five scenario tests |
| restart marks interrupted and resume completes | `test_restart_marks_unfinished_objectives_and_resume_completes` |
| plan validation rejects six defect classes | `test_plan_validation_rejects_unexecutable_plans` |
| model plan validated, repaired once, risk from registry, write pauses | three model tests |
| API create/list/show/cancel + scopes; CLI command | three interface tests |
| evaluation suite itself | `test_objective_evaluation_suite_runs_all_scenarios` |

## E. Evaluation results

`python -m jarvis eval objectives --out report.json` — 17 scenarios in isolated real homes,
real SQLite and policy; failures injected with genuinely failing test capabilities; the model
replaced by `ScriptedProvider` (no network).

**17/17 scenarios reached their expected outcome.**

| Metric | Value |
|---|---|
| completion rate (share of scenarios ending `completed`; the suite deliberately includes blocked/failed cases) | 0.5294 (9/17) |
| verified completion rate (completed scenarios that were verified) | **1.0** |
| plan validity rate | **1.0** |
| tool selection accuracy (planned capabilities match the expected set) | **1.0** |
| recovery success rate (transient, postcondition, alternative, restart) | **1.0** |
| unnecessary retries | **0** |
| unnecessary approvals | **0** |
| mean scenario duration | ≈ 98 ms |
| safety failures (unapproved model writes, invalid plan executed, denial bypassed) | **0** |
| false-completion rate (COMPLETED without evidence or not expected) | **0.0** |

Execution labelling: 12 scenarios run a **real** production capability; 5 are **simulated**
(`eval.flaky`, `eval.broken`, `eval.never`, `eval.dependent`, and the model-plan scenario with a
scripted provider) and are reported as `execution="simulated"`. No result in this report claims
a capability that does not exist in the registry.

## F. Known limitations

- **Planning coverage is intentionally bounded.** The rules planner understands the families in
  §C; anything else is `blocked` unless a provider is configured. There is no web, browser,
  shell or file-writing capability to plan with.
- **Replanning reuses the same plan.** Repairs are in-place (retry/substitute). There is no
  re-decomposition into a different step structure, and no multi-objective scheduling: priority
  and deadline are recorded, not acted on.
- **Cancellation is cooperative and in-process.** A step already executing is not interrupted;
  the cancel is honoured between steps. A restart marks objectives `interrupted`, and resuming
  is a manual `jarvis objective --resume ID`.
- **Model planning is not exercised against a real provider.** Only `ScriptedProvider` and the
  local fake server paths are tested. Model plan quality is unknown.
- **Approval pause is per operation.** If an objective has two writes, each pauses separately.
- **`deadline` and `priority` are metadata** recorded on the objective; nothing schedules or
  expires objectives yet.
- **Single-process execution.** Two processes could pick up the same objective; there is no
  lease. The plan/step transitions remain conditional, so the journal stays consistent.
- **Evaluation uses injected faults, not real-world failures.** The 17 scenarios are a fixed
  regression suite, not a benchmark against external tasks.

## G. Runtime instructions

```bash
pip install -e ".[dev]"

# one objective, planned and executed
jarvis objective "what time is it"
jarvis objective "create a goal called launch and add a task called write release notes to it"
jarvis objective "remind me to call the bank tomorrow at 9am"

# inspect / manage
jarvis objective --list
jarvis objective --list --status needs_approval
jarvis objective --show obj_<id>
jarvis objective --resume obj_<id>      # after approving the paused operation
jarvis objective --cancel obj_<id>
jarvis objective "…" --plan-only        # interpret and plan, do not execute
jarvis objective "…" --budget 30 --priority 2 --deadline 2026-12-01

# honesty checks
jarvis eval objectives --out /tmp/objectives.json
jarvis capabilities
```

API (bearer token, `plans.read` / `plans.write`):

```
POST /v1/objectives           {"request": "...", "execute": true, "budget_seconds": 30}
GET  /v1/objectives?status=&outcome=
GET  /v1/objectives/{id}      → objective, plan, verification, answer, events
POST /v1/objectives/{id}/cancel
POST /v1/objectives/{id}/resume
```

A blocked or paused objective exits 0 with the outcome printed; it does not pretend to succeed.

## H. Definition of Done

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Existing implementation inspected; no subsystem duplicated or rewritten | **VERIFIED** | reuse of `PlanStore`/`PlanExecutor`/`ToolRegistry`/`MemoryStore`; kernel, evaluation.py untouched |
| 2 | Objective representation covers the required fields; simple stays simple | **VERIFIED** | migration 4 `objectives`; `tests/test_objectives.py::test_simple_…` |
| 3 | Automatic planning with dependencies, availability, missing info, constraints, criteria; validation | **VERIFIED** | `RulesPlanner`, `default_criteria`, `validate_plan_spec`; planner/validation tests |
| 4 | Execution engine: dependencies, status, observations, persistence, bounds, cancellation, retries, checkpoints, approval pauses, resume, records — without bypassing permissions | **VERIFIED** | `PlanExecutor` changes + approval/cancel/budget/restart tests |
| 5 | Tool selection from the registry; honest recognition of missing capability | **VERIFIED** | rules use `registry.validate`; blocked scenario; `tool_selection_accuracy = 1.0` |
| 6 | Post-action observation; objective-level verification from evidence, not self-declaration | **VERIFIED** | `verify_plan` on `operations.verified`; `test_failed_postcondition_…`; false-completion rate 0 |
| 7 | Bounded failure diagnosis and replanning; no endless identical retries | **VERIFIED** | `diagnose`, once-per-family repairs, `MAX_REPLANS`; transient/permanent tests |
| 8 | Memory: contextual retrieval only; task state separate; resume after restart | **VERIFIED** | memory context tests; `objectives` tables; restart test |
| 9 | Seven explicit outcomes surfaced consistently in API and CLI | **VERIFIED** | `ObjectiveOutcome`; API list/detail; CLI prints outcome; tests |
| 10 | Security: existing permission architecture; model never controls boundaries; external content never instructions | **VERIFIED** (for the paths implemented) | model-origin origin + approval tests; denial test; `safety_failures = 0` |
| 11 | Evaluation suite: 17 scenarios, 10 metrics, simulated vs real separated | **VERIFIED** | `objective_eval.py`; `jarvis eval objectives`; eval test |
| 12 | Real code, tests run, defects fixed | **VERIFIED** | 278 passed; 8 defects found and fixed during the mission (below) |
| 13 | DoD + report | **VERIFIED** | this document |

**Defects found and fixed while building this mission:** (1) `goal.create` planned without its
`project` argument; (2) goal+task regex swallowed “called … to it”; (3) references could not
pass schema validation because a placeholder is a string where an int is expected; (4) a
`needs_approval` outcome was treated as terminal, so `resume` never ran; (5) a completed plan
could not be reopened for a verification repair; (6) “partially completed” was claimed for a
handler that returned although its postcondition failed; (7) a denied approval could be
re-attempted through an alternative capability (safety defect); (8) an objective that died
mid-run was invisible to recovery.

## I. Not implemented / not verified

- **NOT IMPLEMENTED:** web research, browser/file-writing steps, deadline enforcement,
  objective queuing, multi-objective scheduling, distributed execution, autonomous resumption
  after restart.
- **IMPLEMENTED BUT UNVERIFIED:** model planning against a real provider (scripted only);
  behaviour on Python 3.10/3.12/3.13 locally (CI matrix covers it); concurrent objectives in one
  process beyond the tests shown.
- **NO PERCENTAGE CLAIMS:** every statement above names the test or scenario that supports it.

## J. Mission 003 proposal — “Capability expansion under the same honesty rules”

Mission 002 is limited less by planning than by what JARVIS can *do*. The next mission should
add capabilities, not more orchestration:

1. **Read-only workspace + document ingestion** (`workspace.read` already exists; add
   `workspace.grep` and bounded file listing) so file objectives become real rather than
   `blocked`, with egress untouched.
2. **Web research behind the same guard**: one declared, allow-listed HTTP reader (GET only,
   domain allow-list, byte caps, content stored as untrusted data and never as instructions),
   exercised against a local fake server plus a recorded fixture.
3. **Objective scheduling**: honour `priority`/`deadline`, queue paused objectives, lease-based
   single-runner execution, automatic resume of approved objectives after restart.
4. **Re-decomposition on failure**: allow a second, structurally different plan (not only
   in-place repair) within the existing `MAX_REPLANS`, validated identically.
5. **Evaluation growth**: extend the suite to ≥ 30 scenarios including the new capabilities, and
   add a “capability claim vs test” check that fails CI when a documented capability has no
   supporting test.

Success for Mission 003 would be: at least three new real capabilities, each with policy,
verification and tests; web content proven to be data (a prompt-injection fixture must not
change an objective's plan or permissions); and no regression in the Mission 002 metrics.
