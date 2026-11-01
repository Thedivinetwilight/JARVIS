# J.A.R.V.I.S. M5 Coverage Matrix — Working Draft

- **Status:** draft; M5 is not complete and G-O1 remains blocked.
- **Basis:** product capability specification v1.1.0 and roadmap v1.1.0 on `arena/bbffddd4-jarvis` at HEAD `986488f`.
- **Purpose:** make explicit what can be evaluated with the repository's current synthetic harness, what new evaluation work is needed, and what requires the owner's actual server, language, account, or device inventory.
- **Boundary:** this document maps planned product requirements to measurement. It does not claim any product capability has been implemented or validated.

## 1. Coverage status

| Capability | Requirement area | Planned evaluation | Current evidence | M5 disposition |
|---|---|---|---|---|
| C-01 | Preferences and personal context | B13, B4, B7 | B10 tests synthetic memory consolidation only; no preference-application or durable-memory product test | Mapping set; B4/B13/B7 suites needed |
| C-02 | Knowledge stores and people database | B4, B7 | B10 synthetic consolidation only; no deletion, expiry, disclosure, or real-store test | Mapping set; B4/B7 suites needed; permitted fields remain owner decision Q18 |
| C-03 | Voice-first, no chat box, multilingual, latency | B6, B19, G-UX | No voice/audio/model/server measurements | Mapping set; B6/B19 and UI audit needed; target language list Q19 and server budget Q20 block measurements |
| C-04 | Speech and authorized-speaker verification | B11, B7 | No local audio, speaker verification, false-accept/reject, or deletion tests | Mapping set; B11/B7 needed; hardware/microphone conditions must be specified |
| C-05 | Personal assistant for calls | B12, B7 | No call integration or call simulation evidence | Mapping set; B12/B7 needed; jurisdiction Q17, call modes Q22, and P-1 Q23 remain open |
| C-06 | Brain-inspired mechanisms, no consciousness goal | B14, B17 | B9/B10 synthetic skill-reuse and memory-consolidation mechanisms; no evidence of product cognition or consciousness | Partial harness mechanisms only; B14 ablations and B17 claims audit needed |
| C-07 | Bounded self-evolution | B14, B7, B9, B10 | B9/B10 and existing X-10/X-13 exercise synthetic skills/memory and evaluator regressions; no updater, promotion or rollback pipeline | Partial harness mechanisms only; B14/B7 and rollback implementation tests needed |
| C-08 | Invention support | B15, B8 | B8 synthetic extraction/reconciliation/planning only; no invention-quality, prior-art, budget-calibration or query-privacy evidence | Mapping set; B15 needed; external retrieval boundary must be tested |
| C-09 | Limitation explanations | B16 | Some B3/B8 failure messages exist in the synthetic harness; no nine-class product-level coverage | Partial synthetic overlap only; B16 needed |
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
| B4–B7 naming conflict in older Phase 8/M2 notes | Evaluation naming consistency | Open; resolve in M5 with an explicit supersession/crosswalk |

## 7. M5 completion checklist

- [x] Map all 12 capability IDs to at least one planned evaluation family.
- [x] Separate current synthetic harness mechanisms from product-level evidence.
- [x] Record the local-only processing rule and declared external service classes.
- [x] Define the minimum planned evaluation scope for B4–B7 and B11–B20.
- [ ] Freeze the canonical B4–B7 family naming/crosswalk.
- [ ] Owner supplies first-year priority and success objective (Q13/Q14/A7).
- [ ] Owner decides whether interaction data may be used (Q15).
- [ ] Owner supplies target language matrix (Q19).
- [ ] Owner supplies dedicated-server constraints and measured hardware baseline (Q20).
- [ ] Owner supplies account/connector and smart-home inventories (Q21/Q24).
- [ ] Telephony scope and call modes decided before any call integration (Q17/Q22); P-1 remains optional pending Q23.
- [ ] Pre-register tasksets, thresholds and mutation tests for each planned family.
- [ ] Re-derive workload envelopes using the approved server and measured per-language results.
- [ ] Review coverage report; only then reconsider G-O1.

**Current conclusion:** this draft advances the coverage mapping and identifies the minimum evidence plan. It does not complete M5 because no server measurements or owner-specific inventories/language priorities exist in the repository. G-O1 remains blocked; no model recommendation is justified by this document.
