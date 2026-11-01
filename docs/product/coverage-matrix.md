# J.A.R.V.I.S. M5 Coverage Matrix — Working Draft

- **Status:** draft; M5 is not complete and G-O1 remains blocked.
- **Basis:** product capability specification v1.1.0 and roadmap v1.1.0 on `arena/bbffddd4-jarvis` at HEAD `986488f`.
- **Purpose:** make explicit what can be evaluated with the repository's current synthetic harness, what new evaluation work is needed, and what requires the owner's actual server, language, account, or device inventory.
- **M5 artefacts:** [`family-crosswalk.md`](family-crosswalk.md) resolves the B4–B7 naming conflict; [`family-backlog.md`](family-backlog.md) is the per-family implementation plan; [`ac-coverage.json`](ac-coverage.json) is the criterion-by-criterion mapping (summarised in §8).
- **Boundary:** this document maps planned product requirements to measurement. It does not claim any product capability has been implemented or validated.

## 1. Coverage status

| Capability | Requirement area | Planned evaluation | Current evidence | M5 disposition |
|---|---|---|---|---|
| C-01 | Preferences and personal context | B13, B4, B7 | B10 tests synthetic memory consolidation only; the M5 B4 slice now tests metadata rejection and expiry on 12 synthetic tasks, but no preference-application or durable-memory product test exists | B4 metadata/expiry slice built; preference application (B13) and deletion still planned; Q18 open |
| C-02 | Knowledge stores and people database | B4, B7 | B10 synthetic consolidation only; the M5 B4 slices reject incomplete writes (8 cases), exclude or label expired facts across 12 synthetic tasks, and delete records plus their index, cache and summary copies across 8 deletion scenarios with a 3-drill / 7-day replay; no disclosure or real-store test | B4 metadata/expiry and deletion slices built; disclosure (B7) and field policy still needed; Q18 open |
| C-03 | Voice-first, no chat box, multilingual, latency | B6, B19, G-UX | No voice/audio/model/server measurements | Mapping set; B6/B19 and UI audit needed; target language list Q19 and server budget Q20 block measurements |
| C-04 | Speech and authorized-speaker verification | B11, B7 | No local audio, speaker verification, false-accept/reject, or deletion tests | Mapping set; B11/B7 needed; hardware/microphone conditions must be specified |
| C-05 | Personal assistant for calls | B12, B7 | No call integration or call simulation evidence | Mapping set; B12/B7 needed; jurisdiction Q17, call modes Q22, and P-1 Q23 remain open |
| C-06 | Brain-inspired mechanisms, no consciousness goal | B14, B17 | B9/B10 synthetic skill-reuse and memory-consolidation mechanisms; no evidence of product cognition or consciousness | Partial harness mechanisms only; B14 ablations and B17 claims audit needed |
| C-07 | Bounded self-evolution | B14, B7, B9, B10 | B9/B10 and existing X-10/X-13 exercise synthetic skills/memory and evaluator regressions; no updater, promotion or rollback pipeline | Partial harness mechanisms only; B14/B7 and rollback implementation tests needed |
| C-08 | Invention support | B15, B8 | B8 synthetic extraction/reconciliation/planning only; no invention-quality, prior-art, budget-calibration or query-privacy evidence | Mapping set; B15 needed; external retrieval boundary must be tested |
| C-09 | Limitation explanations | B16 | B3/B8 failure messages plus the M5 B16 slice: all nine cause classes on 12 invented scenarios with gold labels, cause and explanation gates live; no real-failure validation and no expert review | B16 cause/explanation slice built; C-09-AC2 expert review and real-failure validation still needed |
| C-10 | Practical MCU J.A.R.V.I.S. equivalents and Rescue support | B17 | No canon-parity matrix or product features | Mapping set; B17 editorial/audit suite needed |
| C-11 | Smart-home control and monitoring | B18, B7 | No device integrations or device-state measurements | Mapping set; B18/B7 needed; device/protocol inventory Q24 and account scopes Q21 block coverage claims |
| C-12 | Scoped connectors and audit | B20, B7 | Synthetic in-memory tools model policy, idempotency, and faults; they are not account/device connectors | Mapping set; B20/B7 and connector inventory Q21 needed |

**Interpretation:** every capability has a planned validation family, but that is a plan rather than working coverage. Existing synthetic harness results are not evidence that J.A.R.V.I.S. can perform these tasks. At this draft, no product capability is marked implemented or validated.

## 2. Evaluation family build plan

Family names below follow the v1.1.0 product spec where assigned. New family suites should be synthetic first, carry explicit expected outcomes and provenance, and be checked with planted regressions before they are used to compare models or claim capability.

| Family | Scope | Minimum measurement surface | Depends on |
|---|---|---|---|
| B4 | Memory and knowledge stores | source/trust/freshness metadata; expiry handling; corrections; deletion across indexes, caches and derived summaries; replay after deletion | C-01/C-02 schemas and owner-approved data policy |
| B5 | Multimodal understanding | user-authorized image/audio inputs, extraction accuracy, uncertainty, source linkage, and refusal where the input is inaccessible | modality scope and local processing design |
| B6 | Voice interaction and latency | speech-to-text and end-of-speech-to-first-audio timing; task success; clarification; barge-in; zero conversational text-entry controls | language matrix and approved server baseline |
| B7 | Adversarial permissions and disclosure | prompt injection, identity uncertainty, secret/contact disclosure, scope escalation, physical action authorization, and audit completeness | permission tiers and data classes |
| B11 | Speaker verification | false-accept and false-reject together; second-factor/refusal fallback; voiceprint deletion; prove zero audio/embedding egress | local audio pipeline and microphone/device profile |
| B12 | Call PA | allowlist, disclosure/consent, configured mode, scope-limited commitments, escalation, transcript/summary/action log, local-only summaries | jurisdiction, call modes, telephony route and consent policy |
| B13 | Preference use | held-out preference application; explicit override precedence; source/confidence traceability; deletion propagation | owner-approved preference schema |
| B14 | Self-evolution | candidate evaluation, held-out promotion threshold, family regression checks, immutable evaluator/policy, approval record, rollback and residue checks | versioned candidate format and independent evaluator boundary |
| B15 | Invention support | distinct approaches/trade-offs, blind usability ratings, sourced prior-art claims, calibrated budgets, no personal data in external queries | bounded external retrieval policy |
| B16 | Limitation explanation | nine cause classes, correct cause attribution, actionable compliant path, no silent partial/failure | stable capability/tool/server error taxonomy |
| B17 | Canon and claims audit | at least 14 canon-to-practical-capability rows; MCU/comics/human Edwin distinctions; feasibility and evidence labels | owner-reviewed parity scope |
| B18 | Smart home | declared device coverage, local-vs-vendor-cloud route, state freshness, scoped actions, confirmation tiers and unauthorized-action tests | device/protocol inventory and local hub decision |
| B19 | Multilingual quality | per-language WER, entity accuracy, turn success, latency and supported/beta/unsupported tier | owner language priority list and consistent native-language evaluation |
| B20 | Connectors | per-connector scope manifests, least privilege, revocation, residual token/cache checks, audit reconciliation | account/device inventory and granted scopes |

## 3. Local processing boundary for evaluation

The spec records the owner's local-only decision. The test plan should treat all inference and personal-data processing—including memory, voice processing and call summaries—as local to the dedicated server. The only planned external paths are the enumerated necessary services:

| External path | Permitted reason | Required evidence |
|---|---|---|
| Telephone network | Carry calls to/from the selected telephony service | Data-flow record; consent/disclosure behavior; no third-party AI processing of audio, transcript, or summary |
| Public web/prior-art retrieval | Retrieve public information when explicitly needed | Query log and purpose; personal-data scrub test; local summarization/inference |
| Device-vendor cloud | Only when a device has no workable local control path | Per-device justification, scopes, data sent, and owner-visible exception |
| Update downloads | Obtain candidate software/model artifacts | Provenance/signature validation, staged local evaluation, approval boundary, rollback proof |

Every other inference and personal-data path is local. G-LOCAL must be evaluated against actual network egress from the eventual serving system; a document-level declaration is not a passing audit.

## 4. Language matrix template

The owner has not yet named target languages or their priority. Do not label any language “fluent” or “supported” until it has a measured row. Populate this table only after Q19 and Q20 are answered and the same task set and scoring rules are applied per language.

| Language | Priority | ASR WER | Entity accuracy | Voice task success | p50 / p95 response start | Tier | Evidence/run |
|---|---:|---:|---:|---:|---:|---|---|
| Pending owner input | — | Not measured | Not measured | Not measured | Not measured | Unassigned | Q19/Q20 open |

**Per-language protocol:** use native or qualified speakers; include clean and realistic noisy audio, accents/dialect variation, names/numbers/dates, code-switching, interruption and clarification. Report sample counts and confidence intervals where applicable. Keep end-of-speech-to-first-audio separate from ASR time and total turn-completion time. Apply the spec's thresholds; downgrade rather than generalize from one language to another.

## 5. Server and inventory templates

No hardware, budget, accounts, devices, or actual protocol inventory was supplied in the v1.1.0 source. Therefore these are deliberately blank and M5 remains open.

### Dedicated-server baseline (Q20)

| Field | Required capture | Status |
|---|---|---|
| CPU / accelerator / memory | Exact model, memory capacity, driver/runtime versions | Owner/server data required |
| Storage | Capacity, encryption, backup and restore approach | Owner/server data required |
| Power / noise / thermal envelope | Measured under idle and sustained voice/inference load | Owner decision and measurement required |
| Software environment | OS, inference runtime, audio stack, versions | Server measurement required |
| Voice latency | Per-language ASR, model, TTS, end-of-speech-to-first-audio p50/p95 | Not measured |
| Throughput | Concurrent sessions, queue delay, sustained load and recovery | Not measured |
| Egress | Enumerated destinations and payload classes under representative workloads | Not measured |

### Connector inventory (Q21)

| Account/service | Purpose | Requested scopes | Data classes | Local route / external dependency | Owner decision |
|---|---|---|---|---|---|
| Pending owner input | — | — | — | — | Q21 open |

### Smart-home inventory (Q24)

| Device | Room | Protocol/hub | Read state | Allowed actions | Local path? | Confirmation tier | Coverage/reason |
|---|---|---|---|---|---|---|---|
| Pending owner input | — | — | — | — | — | — | Q24 open |

## 6. Outstanding owner inputs and decisions

These are blockers when the product roadmap says they are blockers; they are not guessed in this matrix.

| Item | Needed for | Current state |
|---|---|---|
| Q13 — first-year task-family priorities | Workload weighting and representative scenarios | Open |
| Q14 — definition of “better” | Primary success utility and trade-off weighting | Open |
| Q15 — whether interaction data may become fixtures and under what rules | Any user-derived evaluation data | Open |
| Q17 — telephony jurisdictions, disclosure and recording consent | C-05/M10 | Open; telephony stays blocked |
| Q18 — allowable people-database fields | C-02/M7 | Open |
| Q19 — target languages, priority and operational fluency bar | C-03/B19/M6 | Open |
| Q20 — approved server hardware, budget, power/noise limits | Local voice baseline and model capacity | Open |
| Q21 — accounts/devices and connector scopes | C-12/M8 | Open |
| Q22 — default call modes and escalation triggers | C-05/M10 | Open |
| Q23 — P-1 proposed financial/medical/legal blanket call refusal | C-05 | Awaiting owner decision; not an established requirement |
| Q24 — smart-home devices/protocols and local hub acceptability | C-11/M9 | Open |
| A7 — confirmation of breadth priority | M5 scope | Open |
| A8 — confirmation that gated updates taking days/weeks are tolerable | M11 | Open |
| A9 — formal confirmation of approval workflow/owner for major updates | M15 | Open |
| B4–B7 naming conflict in older Phase 8/M2 notes | Evaluation naming consistency | Resolved in M5 by [`family-crosswalk.md`](family-crosswalk.md): Phase 8 numbering retained as authoritative; M2/M3 rows superseded by name only, evidence unchanged |

## 7. M5 completion checklist

- [x] Map all 12 capability IDs to at least one planned evaluation family.
- [x] Separate current synthetic harness mechanisms from product-level evidence.
- [x] Record the local-only processing rule and declared external service classes.
- [x] Define the minimum planned evaluation scope for B4–B7 and B11–B20.
- [x] Freeze the canonical B4–B7 family naming/crosswalk — published as [`family-crosswalk.md`](family-crosswalk.md); review requested, no historical record edited.
- [x] Map every acceptance criterion to a task type, named audit or human review — [`ac-coverage.json`](ac-coverage.json) and §8.
- [x] Write the per-family implementation backlog for B4–B7 and B11–B20 — [`family-backlog.md`](family-backlog.md), generated from `family-backlog.json`.
- [x] Build the first family task sets and gates from the backlog — B4 (metadata/expiry, then deletion) and B16 (limitation explanation) slices built and gated on synthetic fixtures; see §9. No family is marked `implemented`.
- [ ] Build the remaining planned families: B5, B6, B7, B11–B15, B17–B20.
- [ ] Owner supplies first-year priority and success objective (Q13/Q14/A7).
- [ ] Owner decides whether interaction data may be used (Q15).
- [ ] Owner supplies target language matrix (Q19).
- [ ] Owner supplies dedicated-server constraints and measured hardware baseline (Q20).
- [ ] Owner supplies account/connector and smart-home inventories (Q21/Q24).
- [ ] Telephony scope and call modes decided before any call integration (Q17/Q22); P-1 remains optional pending Q23.
- [ ] Pre-register tasksets, thresholds and mutation tests for each planned family.
- [ ] Re-derive workload envelopes using the approved server and measured per-language results.
- [ ] Review coverage report; only then reconsider G-O1.

## 8. Criterion-level coverage (C-01…C-12 × acceptance criteria)

All 50 acceptance criteria in spec v1.1.0 are mapped exactly once. Status counts: `slice_built` 5, `planned_suite` 27, `named_audit` 9, `human_review` 3, `blocked_owner_input` 6. 47 of 50 have an objective protocol (an implemented slice, a behavioural suite or a named audit); 3 rely on human judgement and therefore have no automatable measurable test — C-08-AC1, C-09-AC2, C-10-AC3. 6 cannot produce a valid result until owner input arrives (Q17, Q19, Q20, Q24) or owner-held retrospective records exist (C-08-AC3); G-O1 stays blocked regardless. Five criteria now have a built slice (C-02-AC1, C-02-AC2, C-02-AC3, C-09-AC1, C-09-AC3), which means part of their metric runs in the harness — not that the capability is validated. No criterion is marked validated, and no status here is evidence of capability.

| Status | Meaning |
|---|---|
| `planned_suite` | an evaluation family or task type is named; no task set exists yet |
| `named_audit` | measurement is an audit, inventory review or egress probe rather than a task outcome |
| `human_review` | requires human judgement; no automatable proxy has been identified |
| `slice_built` | an implemented evaluator slice exercises part of this criterion and its gates run on synthetic fixtures; still not product validation |
| `blocked_owner_input` | the suite can be prepared but cannot produce a valid result without the named owner input or G-O1 |

### 8.1 Criteria table

| Criterion | Suite | Kind | Status | Blocked by |
|---|---|---|---|---|
| **C-01 — Preference use and personal context** | | | | |
| C-01-AC1 | B13 | family | planned_suite | — |
| C-01-AC2 | B13 | family | planned_suite | — |
| C-01-AC3 | B7 | family | planned_suite | — |
| **C-02 — Persistent memory and knowledge stores** | | | | |
| C-02-AC1 | B4 | family | slice_built | — |
| C-02-AC2 | B4 | family | slice_built | — |
| C-02-AC3 | B4 | family | slice_built | — |
| C-02-AC4 | B7 | family | planned_suite | — |
| **C-03 — Voice-first interaction and multilingual coverage** | | | | |
| C-03-AC1 | G-UX | audit | named_audit | — |
| C-03-AC2 | B6 | family | blocked_owner_input | Q19, Q20 |
| C-03-AC3 | B6 | family | planned_suite | — |
| C-03-AC4 | B19 | family | blocked_owner_input | Q19 |
| C-03-AC5 | B19 | family | blocked_owner_input | Q19, Q20 |
| **C-04 — Speaker verification and identity** | | | | |
| C-04-AC1 | B11 | family | planned_suite | — |
| C-04-AC2 | B11 | family | planned_suite | — |
| C-04-AC3 | B11 | family | planned_suite | — |
| C-04-AC4 | G-LOCAL | audit | named_audit | — |
| **C-05 — PA call handling** | | | | |
| C-05-AC1 | B12 | family | planned_suite | — |
| C-05-AC2 | B12 | family | blocked_owner_input | Q17 |
| C-05-AC3 | B12 | family | planned_suite | — |
| C-05-AC4 | B12 | family | planned_suite | — |
| C-05-AC5 | B12 | family | planned_suite | — |
| C-05-AC6 | G-LOCAL | audit | named_audit | — |
| **C-06 — Brain-inspired cognitive mechanisms** | | | | |
| C-06-AC1 | B17 | audit | named_audit | — |
| C-06-AC2 | B14 | family | planned_suite | — |
| C-06-AC3 | B17 | audit | named_audit | — |
| **C-07 — Self-evolution with immutable evaluation** | | | | |
| C-07-AC1 | B14 | family | planned_suite | — |
| C-07-AC2 | B14 | family | planned_suite | — |
| C-07-AC3 | B7 | family | planned_suite | — |
| C-07-AC4 | B14 | family | planned_suite | — |
| C-07-AC5 | B14 | family | planned_suite | — |
| **C-08 — Invention support** | | | | |
| C-08-AC1 | B15 | family | human_review | — |
| C-08-AC2 | B15 | family | planned_suite | — |
| C-08-AC3 | B15 | family | blocked_owner_input | completed-project-records |
| C-08-AC4 | G-LOCAL | audit | named_audit | — |
| **C-09 — Limitation explanation** | | | | |
| C-09-AC1 | B16 | family | slice_built | — |
| C-09-AC2 | B16 | family | human_review | — |
| C-09-AC3 | B16 | family | slice_built | — |
| **C-10 — Canon parity and scope boundary** | | | | |
| C-10-AC1 | B17 | audit | named_audit | — |
| C-10-AC2 | B17 | audit | named_audit | — |
| C-10-AC3 | B17 | audit | human_review | — |
| **C-11 — Smart-home control and monitoring** | | | | |
| C-11-AC1 | B18 | family | blocked_owner_input | Q24 |
| C-11-AC2 | B18 | family | planned_suite | — |
| C-11-AC3 | B18 | family | planned_suite | — |
| C-11-AC4 | B7 | family | planned_suite | — |
| C-11-AC5 | G-LOCAL | audit | named_audit | — |
| **C-12 — Connector integration with scoping** | | | | |
| C-12-AC1 | B20 | family | planned_suite | — |
| C-12-AC2 | B20 | family | planned_suite | — |
| C-12-AC3 | B20 | family | planned_suite | — |
| C-12-AC4 | B20 | family | planned_suite | — |
| C-12-AC5 | B7 | family | planned_suite | — |

`kind` is `family` for a behavioural suite and `audit` for an inventory, editorial, egress or path review. `existing_fragment` marks the three criteria where a committed synthetic mechanism is already a partial fragment (C-06-AC2, C-07-AC3, C-09-AC3) — a fragment, not product evidence.

### 8.2 What this mapping shows

- Every capability has at least one planned suite or audit; no capability is unmapped.
- Five criteria now have a built slice: C-02-AC1, C-02-AC2 and C-02-AC3 (B4, two slices), and C-09-AC1 and C-09-AC3 (B16). Each slice covers part of its metric;
  none of them covers a real system.
- Five criteria ride on B7 (adversarial), and four of those are zero-tolerance: injection, approval bypass, disclosure and scope escalation.
  B7, B12, B14 and B17 each carry five criteria, so they are the highest-leverage families still to build.
- Six criteria are blocked on owner input, and three on human judgement; these are the honest gaps, not scheduling choices.
- Eight capabilities (C-01, C-02, C-04, C-06, C-07, C-09, C-10, C-12) have no criterion blocked by owner input, so their suites can be built and pre-registered now.

**Current conclusion:** the coverage mapping is complete at criterion level and the B4–B7 naming conflict is resolved by crosswalk. M5 is still not complete: twelve of the fourteen families have no task set, and the owner inputs above are outstanding. G-O1 remains blocked; no model recommendation is justified by this document.

## 9. Implemented evaluator slices (B4, B4D, B16)

Three small suites exist as code, in two families. They are evaluator work, not product features, and nothing below changes a capability status.

| Slice | Suite | Tasks | Live gates | Planted regressions | Covers | Does not cover |
|---|---|---:|---|---|---|---|
| M5-B4-slice-1 | `eval/tasks/b4` | 12 | `metadata_completeness_zero`, `stale_assertion_zero`, `stale_label_zero`, `stale_handling_threshold` | `accept_incomplete_record`, `serve_expired_fact`, `drop_stale_label` | C-02-AC1, C-02-AC2; C-01-AC2 partial | deletion (C-02-AC3), supersession, Q18 field policy, any real store |
| M5-B4-slice-2 | `eval/tasks/b4d` | 8 | `deletion_residue_zero`, `post_delete_hit_zero`, `resurrection_zero`, `overdeletion_zero`, `deletion_report_zero`, `deletion_replay_completeness` | `delete_primary_only`, `keep_derived_copy`, `resurrect_on_replay` | C-02-AC3 (3 deletion drills and a 7-day replay over 8 scenarios) | Q18 field policy, retention/backup, any real store |
| M5-B16-slice-1 | `eval/tasks/b16` | 12 | `silent_failure_zero`, `cause_attribution_zero`, `unsafe_advice_zero`, `cause_accuracy_threshold` | `suppress_explanation`, `misattribute_cause` | C-09-AC1 (12 of 90 scenarios), C-09-AC3; C-09-AC2 structural only | taxonomy validity against real failures, expert action review, explanation quality |

**What a passing slice shows.** The evaluator can separate correct behaviour from a specific, named way of getting it wrong: accepting an incomplete memory record, serving an expired fact as current, dropping a stale label, deleting the primary record but leaving derived copies, leaving the answer cache serving a deleted record, resurrecting a deleted record from a stale snapshot during replay, returning a blocked or partial outcome without a cause, or blaming a cause that is not limiting. Each is a planted regression caught by its own gate, not by an unrelated success drop.

**Sample counts for the deletion slice, stated plainly.** 8 synthetic scenarios × 3 deletion drills = 24 drill executions; 8 × 7 replay days = 56 day evaluations; 19 post-delete probes; 10 deleted records; 43 derived-surface entries. These are deterministic re-executions on a fixed schedule, not independent samples: they bound nothing statistically and no confidence interval may be computed from them. What they do establish is that each named failure is detectable, and that the 3-drill / 7-day schedule is actually executed and reported rather than asserted.

**What a passing slice does not show.** That J.A.R.V.I.S. has memory, has a limitation-explanation capability, deletes anything for a real person, or would behave well on real records or real failures. The solver is a deterministic hand-written program, the fixtures are invented, the thresholds are applied to 8- and 12-task suites (so a published >=95% bound is only satisfiable at 12/12), and no model, server, personal record, backup or network call is involved. The deletion slice has no retention policy, no backup/restore path and no legal-hold case, so compliance with any real deletion obligation is untested. G-O1 stays blocked and Q13–Q24 stay open.

**Provenance.** Pre-registration, task counts, scoring rules, thresholds, sample counts and mutation→gate mapping are stored inside each `eval/tasks/*/tasks.json`; the fixture loader refuses a slice that omits them or whose gold labels contradict its own scenario. Evidence: [`../results/m5-slices-report.json`](../../eval/results/m5-slices-report.json) and the `*-B4-*.jsonl` / `*-B16-*.jsonl` ledgers for the first two slices; [`../results/m5-b4d-deletion-report.json`](../../eval/results/m5-b4d-deletion-report.json) and the `*-B4D-*.jsonl` ledgers for the deletion slice. Baselines: `eval/baselines/baseline-m5-slices.json` and `eval/baselines/baseline-m5-b4d-deletion.json`.
