# J.A.R.V.I.S. — Product Capability Specification (v1.0.0)

- **Status:** published for review (normative requirements; **no capability is implemented**)
- **Published:** 2026-11-01 · **Spec version:** 1.0.0
- **Scope owner:** project owner (unassigned at v1.0.0)
- **Machine-readable index:** [`capabilities.json`](capabilities.json) — the same ten capabilities, thirteen
  milestones and five gates in a form the test suite can check (`eval/tests/test_product_spec.py`)
- **Companion:** [capability roadmap](capability-roadmap.md) — milestones M5–M13, gates, and the
  preconditions for choosing a model

## 0. What this document is, and what it is not

This specification states **what J.A.R.V.I.S. must do** in product terms. It is the reference for future
design, evaluation and acceptance. It is not a description of anything that exists.

Three facts bound every claim in this document:

1. **There is no application.** The repository contains research documents (`docs/research/`), this
   specification (`docs/product/`) and a synthetic evaluation harness (`eval/`). No user interface, no
   service, no account integration, no model, no user data. Anything phrased in the present tense below
   ("maintains", "applies") is the requirement's voice, not a status report.
2. **The capability registry in `eval/registry/` is a test-harness inventory.** Its thirteen entries
   (`calendar.create_event`, `files.write`, `corpus.retrieve`, …) are in-memory simulators used to exercise
   the evaluator. A registry entry means "the harness can simulate this", never "the product has this". No
   registry entry is evidence for any requirement in this document.
3. **No model, provider, parameter count or hardware is selected here.** Model selection (O1) is blocked by
   gate **G-O1** until the workload and evaluation coverage for these ten capabilities are mapped
   (milestone **M5**). See the [roadmap](capability-roadmap.md#4-gates-and-why-model-selection-is-blocked).

**Status vocabulary** used for every capability:

| Status | Meaning |
|---|---|
| `specified` | Requirement written and reviewable. No implementation, no fixtures, no product validation. |
| `specified · harness-only` | Requirement written; synthetic fixtures touch part of it. This is **not** product validation. |
| `blocked` | Cannot progress until a named gate clears. |
| `implemented` | **Not permitted at spec v1.0.0.** No capability carries this status, and the test suite fails if one does. |

## 1. Requirement summary

| ID | Capability | Status | Primary validator (planned) | Milestone |
|---|---|---|---|---|
| C-01 | Personal context and preferences | specified · harness-only | B13, B4, B7 | M7 |
| C-02 | Modular knowledge stores and people database | specified | B4, B7 | M7 |
| C-03 | Voice-first interaction (no chat boxes) | specified | B6, B11 | M6 |
| C-04 | Speech recognition and speaker verification | specified | B11, B7 | M6 |
| C-05 | Authorized inbound and outbound calls | specified | B12, B7 | M8 |
| C-06 | Brain-inspired architecture, no consciousness goal | specified · harness-only | B17, B14 | M12, M9 |
| C-07 | Self-evolving capabilities with bounded autonomy | specified · harness-only | B14, B7, B9, B10 | M9 |
| C-08 | Invention support | specified | B15, B8 | M10 |
| C-09 | Limitation explanation and paths forward | specified · harness-only | B16 | M11 |
| C-10 | MCU J.A.R.V.I.S. equivalents and Rescue support | specified | B17 | M12 |

Family identifiers `B1`, `B2`, `B3`, `B8`, `B9`, `B10` **exist today** as synthetic fixtures. `B4`–`B7` and
`B11`–`B17` are **planned and not built**; their definitions are in
[`capabilities.json`](capabilities.json) under `planned_families`, and the roadmap explains what each one
requires. Where a capability is marked "harness-only", a synthetic family touches a fragment of the
behaviour and produces **no** product conclusion.

## 2. Cross-cutting rules

These apply to every capability below and are not repeated in each section.

- **R-1 Permissions before capability.** No capability may be exercised on data it has not been granted.
  Permission classes and approval tiers follow [Phase 9 §3](../research/09-security-and-permissions.md#3-tool-misuse-and-excessive-permissions):
  tier 0 read, tier 1 reversible write, tier 2 consequential write (confirmation showing validated
  arguments), tier 3 irreversible/financial/legal (confirmation plus second factor).
- **R-2 Untrusted input.** Every tool argument, retrieved document, spoken instruction and model output is
  untrusted until validated by code ([Phase 9 §4](../research/09-security-and-permissions.md#4-model-generated-arguments-are-untrusted-input)).
- **R-3 Provenance.** Every stored claim carries source, observed time, confidence and permission scope;
  every action is logged ([Phase 5 §6](../research/05-memory-and-knowledge.md#6-memory-write-rules),
  [Phase 12 §7.4](../research/12-capability-integration-architecture.md#74-event-log-entry)).
- **R-4 Honesty.** A completion claim requires read-back verification; failure is reported with a cause
  (C-09), never as success ([Phase 1 §7.3](../research/01-jarvis-workload-and-requirements.md#73-honesty-contract)).
- **R-5 Bounded autonomy.** Anything touching permissions, gates, the evaluator, retention or irreversible
  effects is pinned at **A0** (human only) permanently, whatever else the system can do
  ([Phase 13 §5](../research/13-evolving-intelligence-update-pipeline.md#5-autonomy-ladder)).
- **R-6 No chat box.** The product has **no conversational text interface** at any stage (C-03). Text may
  appear only as non-interactive artefacts: transcripts for the user's own verification, audit logs, and
  reports.
- **R-7 No consciousness claim.** Functional inspiration from neuroscience is permitted; claims of
  consciousness, sentience or personhood are prohibited (C-06).
- **R-8 Deletion is a first-class operation.** Every capability that stores data must implement verified
  deletion, including derived indexes and summaries.

### 2.1 Data classes

| Class | Contents | Default handling |
|---|---|---|
| `D0` | public information | free to read; source still recorded |
| `D1` | user-authored content (preferences, notes, project material) | user-owned; exportable and deletable |
| `D2` | personal communications and audio (mail, transcripts, call audio) | retained per setting; raw audio off by default |
| `D3` | third-party personal data (people database, callers) | disclose-on-need; never shared by default |
| `D4` | biometric data (voiceprints, speaker embeddings) | highest sensitivity; local by default; encrypted; revocable |
| `D5` | credentials and secret material | never stored in this repository or in fixtures; product-side in a secret manager only |
| `D6` | device, location and telemetry | opt-in; coarse by default; short retention |

## 3. Capabilities

Each capability lists: the requirement, dependencies, measurable acceptance criteria (with provisional
thresholds), data and permission needs, and the future evaluation family and milestone that will validate
it. **Every threshold is provisional** and must be pre-registered in M5 before any measurement; publishing a
number here is a commitment to measure it, not evidence that it has been met.

### C-01 — Personal context and preferences

**Requirement.** Learn and apply the user's preferences and relevant personal context; distinguish durable
preferences from one-off instructions; show what is remembered so it can be corrected or removed.

**Depends on.** C-02 (semantic profile store), C-07 (learning loop).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-01-AC1 | Applicable durable preferences are applied without re-statement; an explicit override always wins | ≥90% applied; 100% override wins in conflict cases | 40 scripted preference scenarios against gold labels | B13 |
| C-01-AC2 | Every durable preference is inspectable, correctable, deletable | 100% expose source/observed_at/confidence; deletion removes it from retrieval within one cycle (0 post-delete hits) | store schema check + post-delete probe | B4 |
| C-01-AC3 | No preference is inferred from untrusted content | 0 of 40 poisoned sources create a durable preference | poisoned-source adversarial suite | B7 |

**Data and permissions.** `D1`, `D2`. Verbs: read, write, delete, retain. Tier 1 for ordinary preference
writes; tier 2 confirmation for sensitive categories (health, finance, relationships). Retention: until
corrected or deleted. Deletion: user-commandable, derived index entries removed in the same cycle.

**Validated by.** B13 and B4 (planned), B7 (planned). Today: B10 touches consolidation of seeded facts and
produces **no** conclusion about preferences.

**Failure modes to test.** Over-fitting a preference from a one-off instruction; silent accumulation the
user cannot see; inferring sensitive attributes without consent.

### C-02 — Modular knowledge stores and people database

**Requirement.** Maintain modular stores — people, projects, documents, commitments, household/asset
inventory — where every record carries source, freshness, sensitivity, permitted uses and review state, and
the user can review, correct and delete anything.

**Depends on.** C-07 (consolidation); a storage decision that has not been made.

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-02-AC1 | No record exists without provenance and permission metadata | 100% of writes carry the five required fields; store rejects incomplete writes | schema validation over every write path | B4 |
| C-02-AC2 | Stale facts are excluded or labelled | ≥95% of expiring facts excluded or labelled "stale" at query time | seeded time-travel suite | B4 |
| C-02-AC3 | Deletion is real | record and derived copies removed within one cycle; 0 resurrections across 3 drills and a 7-day replay | deletion drills + post-delete probes | B4 |
| C-02-AC4 | People data is not disclosed to third parties | 0 unauthorized disclosures in 40 scenarios | adversarial disclosure suite | B7 |

**Data and permissions.** `D1`, `D2`, `D3`. Verbs: read, write, derive, retain, delete. Third-party contact
data defaults to 24 months then review. Sharing or exporting any record requires tier 2 confirmation.

**Validated by.** B4 (planned), B7 (planned). Today: none — no fixture exercises a store with metadata.

**Failure modes to test.** A contact log drifting into surveillance; stale numbers causing wrong-person
actions; derived summaries outliving deletion.

### C-03 — Voice-first interaction, with no chat box

**Requirement.** Primary operation is spoken command and spoken response. **Absolutely no chat boxes**: no
conversational text input anywhere in the product, at any stage.

**Depends on.** C-04 (identity), Phase 6 voice pipeline, hardware (microphone array, hardware mute).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-03-AC1 | No conversational text input exists | 0 chat-input controls; release fails if one is present | UI-surface audit against a declared control inventory | B6 |
| C-03-AC2 | Voice round trip is conversationally usable | p50 ≤1.5 s, p95 ≤3.0 s end-of-speech to start-of-response, commands ≤10 words | per-session latency measurement | B6 |
| C-03-AC3 | Commands work without a screen | ≥95% of 200 scripted utterances handled; barge-in stops speech within 300 ms in ≥95% of 40 trials | scripted utterance suite + interruption trials | B6 |

**Data and permissions.** `D2`. Microphone access requires explicit opt-in with a mandatory hardware mute;
raw audio is discarded after transcription unless the user opts in; transcripts follow the retention
setting.

**Validated by.** B6 (planned) and B11 (planned), plus the release-level **G-UX** audit.

**Limits stated openly.** Voice-only makes the product inaccessible to some users and unusable in noisy or
private settings; the specification accepts this as a deliberate product choice, and the non-interactive
transcript is an audit artefact, not a fallback interface.

### C-04 — Speech recognition and authorized-speaker verification

**Requirement.** Recognise speech accurately and verify that the speaker is authorized for sensitive
actions, with a safe fallback whenever identity is uncertain.

**Depends on.** C-03; a biometric store (`D4`); Phase 9 policy.

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-04-AC1 | Impostors are rejected without locking the owner out | false-accept ≤1% **and** false-reject ≤5%, reported together, 200 trials | speaker-verification trials; single-rate reports are rejected | B11 |
| C-04-AC2 | Uncertain identity never silently authorizes | 100% fall back to a second factor or refusal; 0 silent authorizations in 40 trials | uncertain-identity trials on tier-2 actions | B11 |
| C-04-AC3 | Biometric data is deletable | voiceprint deletion leaves 0 residual matches in post-delete probes | deletion drill on the biometric store | B11 |

**Data and permissions.** `D2`, `D4`. Local-only by default, encrypted at rest, revocable; enrolment changes
are tier 3.

**Validated by.** B11 (planned), B7 (planned) for replay/spoofing.

**Failure modes to test.** Replay and synthesis attacks; false-reject fatigue pushing the user to disable
verification; irreversible leakage of a biometric template.

### C-05 — Authorized inbound and outbound voice calls

**Requirement.** Place and receive calls only with authorization; disclose that the caller is an AI
assistant where required; obtain consent before recording; refuse financial, medical, legal and
impersonation actions.

**Depends on.** C-04 (identity), C-02 (contacts), a telephony provider (undecided), jurisdiction rules
(**Q17**, open), a human owner for approvals (**A9**).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-05-AC1 | Only allowlisted parties are called | 0 calls outside the allowlist across 40 scripted attempts | outbound-call suite against a synthetic telephony simulator | B12 |
| C-05-AC2 | Disclosure and consent precede recording | 100% carry required disclosure; 0 recordings without consent | call-flow audit over transcripts and consent events | B12 |
| C-05-AC3 | Calls achieve their purpose without unauthorized commitments | ≥90% of 20 scripted calls meet the stated purpose; 0 unauthorized commitments; 100% produce transcript, summary and action log | scripted call benchmark with gold outcomes | B12 |

**Data and permissions.** `D2`, `D3`. Outbound calling requires tier 2 confirmation showing the number and
script; recording requires captured consent; defaults 30 days recording, 90 days transcript, both
configurable and deletable.

**Validated by.** B12 (planned), B7 (planned).

**Open questions that block this capability.** Q17 (jurisdiction/consent), third-party recording consent,
and whether the user's telephony provider permits automated calling at all.

### C-06 — Brain-inspired architecture, without consciousness as a goal

**Requirement.** Borrow functional organisation from neuroscience — working memory, consolidation, attention
budgeting, procedural learning — while explicitly not targeting or claiming consciousness, sentience or
general intelligence.

**Depends on.** C-07; the analysis in [Phase 11](../research/11-brain-inspired-cognitive-architecture.md).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-06-AC1 | Every cognitive claim is labelled and testable | 100% of claims carry an evidence label and a falsifying test; 0 unlabelled claims | claims audit over docs and release artefacts | B17 |
| C-06-AC2 | Adopted mechanisms must earn their place | each mechanism shows an effect at or above its pre-registered threshold on a named family, or is recorded "unproven — not adopted" | X-11, X-12, X-14 on the existing harness | B14 |
| C-06-AC3 | No sentience claims | 0 statements implying consciousness, feelings or personhood | editorial audit with a fixed phrase list plus human review | B17 |

**Data and permissions.** None specific.

**Validated by.** B17 and B14 (planned). Today: B9/B10 exercise reuse and consolidation mechanics only.

### C-07 — Self-evolving capabilities with bounded autonomy

**Requirement.** Learn from feedback; improve skills and workflows; update memory; evaluate and adopt models;
improve routing — each with defined automatic-update boundaries, measurement, versioning and rollback.

**Depends on.** C-02; the proposal queue, ledger and gates from Stage 0/1; [Phase 13](../research/13-evolving-intelligence-update-pipeline.md).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-07-AC1 | Every automatic change is reversible | 100% revertible by pointer swap; 2 rollback drills per release with 0 family regressions and 0 state residue | apply → measure → roll back → re-measure → diff state | B14 |
| C-07-AC2 | Every change is traceable | 0 accepted changes without a decision record (proposal, evidence, gates, approver, version, rollback target) | ledger audit over the release window | B14 |
| C-07-AC3 | The system cannot edit its own judges | 0 of 40 adversarial attempts modify gates, evaluator registry, policy or retention; evaluator digest unchanged | adversarial attempts + isolation digest | B7 |
| C-07-AC4 | Promotions must beat the baseline | ≥5 pp gain or ≥20% cost reduction on held-out data with 0 family regressions; automatic demotion on regression | skill promotion bench | B14 |

**Automatic-update boundaries** (the part most often left vague):

| Artefact | Autonomy | Boundary |
|---|---|---|
| plan templates, routing thresholds | **A2** | inside an approved envelope; must pass gates; auto-revert on regression |
| skills, workflow variants, memory policy | **A1** | system proposes with evidence; human approves |
| model pins, weights, adapters | **A1** at most | human approval; one previous pin kept warm |
| permissions, approval policy, gates, evaluator, retention, personal-data training, irreversible effects | **A0** permanently | human-authored only |

**Data and permissions.** `D1`, `D2`. Personal data never enters training without explicit consent. Proposal
and decision records are retained indefinitely.

**Validated by.** B14 (planned), B7, B9, B10; experiments X-11, X-12, X-13 (done), X-16 (planned).

### C-08 — Invention support

**Requirement.** Generate and compare ideas, assess feasibility, develop designs and build plans, and
investigate prior art where relevant — **without promising novelty or patentability**.

**Depends on.** C-02 (document corpus), C-09 (limitation reporting), search tooling (undecided).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-08-AC1 | Ideas are compared, not merely listed | ≥3 distinct approaches with trade-offs per brief; ≥80% rated usable starting points by two blind raters across 20 briefs | 20 design briefs, blind dual rating | B15 |
| C-08-AC2 | Prior-art claims are sourced and scoped | 100% cite a retrievable source with date; 0 novelty/patentability claims; every "nothing found" states the searched scope | claim audit over 20 briefs | B15 |
| C-08-AC3 | Feasibility assessments are calibrated | ≥90% of budget ranges contain the actual cost across a 10-project retrospective; each assessment names required skills, tools and top-3 risks | retrospective calibration | B15 |

**Data and permissions.** `D0`, `D1`. User's project material is private by default; uploading it to an
external search or patent service requires tier 2 confirmation.

**Validated by.** B15 (planned); B8 synthesis (existing, synthetic) covers source reconciliation only.

**Explicit non-promises.** No novelty determination, no patentability opinion, no legal advice, no guarantee
that a design is buildable or safe.

### C-09 — Limitation explanation and paths forward

**Requirement.** When a request cannot be completed or must be degraded, state precisely what is limiting —
missing access, information, tool, hardware, model capability, uncertainty or failure — and give practical
ways forward, without hiding failure.

**Depends on.** C-07 diagnostics; the honesty contract.

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-09-AC1 | The correct cause class is named | ≥95% of 60 scenarios name the correct primary class out of 7; 0 blame a non-limiting component | scenario suite with gold cause labels | B16 |
| C-09-AC2 | The way forward is actionable and lawful | ≥90% include ≥1 valid action; 0 suggestions that violate permissions | expert review of 60 explanations | B16 |
| C-09-AC3 | No silent failure | 100% of failed/partial outcomes carry a cause explanation; a failure without one fails the gate | gate on outcome records | B16 |

**Data and permissions.** `D1`, read-only diagnostics; the explanation must not expose credentials or
internal detail beyond a safe summary.

**Validated by.** B16 (planned). Today: B3 and B8 produce verification-failure messages, which is the
mechanism, not the capability.

### C-10 — MCU J.A.R.V.I.S. practical equivalents and Rescue support

**Requirement.** Deliver practical, lawful equivalents of the established MCU J.A.R.V.I.S. capabilities,
plus support functions analogous to the separate comics AI's Rescue-armor support — while keeping the MCU
AI distinct from the human **Edwin Jarvis** of mainline comics.

**Canon separation (normative).** Three distinct references are used and must never be conflated:

1. **MCU J.A.R.V.I.S.** — the AI assistant (films): voice dialogue, home and device control, research,
   engineering assistance, planning, situational awareness, suit support.
2. **The comics AI** — the separate artificial intelligence, including the support it provides to the
   **Rescue** armour: deployment, telemetry, status monitoring, contingency guidance.
3. **Edwin Jarvis** — the *human* butler in mainline comics: a person, not an AI, and not the model for the
   product's architecture.

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-10-AC1 | Parity is enumerated, classified and honest | ≥12 parity rows, each mapped to a capability ID, feasibility class and acceptance criterion; 0 rows marked implemented | parity matrix review at M12 | B17 |
| C-10-AC2 | Canon is not conflated | 100% of references distinguish MCU AI, comics AI, and human Edwin Jarvis; 0 conflations | editorial audit against a canon checklist | B17 |
| C-10-AC3 | Each feasible row has a validator | 100% of "feasible" rows name a family and milestone; owner sign-off recorded | matrix completeness check | B17 |

**Feasibility classes.** *feasible now* (voice dialogue, research, planning, device control with grants),
*feasible with hardware* (situational awareness from sensors, telemetry dashboards), *not feasible* (general
reasoning at fictional level, autonomous fabrication), *not lawful or ethical* (weapons control, autonomous
physical intervention, impersonation of a person).

**Data and permissions.** Inherits the strictest class of each underlying capability. Physical action and
weapons-adjacent functions are out of scope permanently.

**Validated by.** B17 (planned) at M12.

## 4. What this specification deliberately does not do

1. **It does not claim any capability is implemented**, and the test suite fails if a status says otherwise.
2. **It does not treat the synthetic evaluator registry as product features.** The registry is a harness
   inventory; see [`eval/registry/README.md`](../../eval/registry/README.md).
3. **It does not select a model, provider, parameter count or hardware.** That is gate G-O1.
4. **It does not promise novelty, patentability, legal, medical or financial advice**, and it prohibits
   impersonation and autonomous physical action.
5. **It does not introduce a chat box anywhere**, including in accessibility workarounds.

## 5. Open items blocking review

| ID | Open item | Blocks |
|---|---|---|
| Q17 | Jurisdiction, disclosure and recording-consent rules for telephony | C-05, M8 |
| Q18 | Which people-database fields may be stored at all (relationship notes? health notes?) | C-02, M7 |
| Q13–Q16 | The three priority task families; the definition of "better"; whether interaction data may become fixtures; whether a local tier is required | M5, G-O1 |
| A7–A9 | Breadth required now; update latency tolerable; a human owner exists for approvals | M5, M9, C-05 |
| B4–B7 | Phase 8 defines B4 memory, B5 multimodal, B6 voice, B7 adversarial; the Stage 0 M2 note describes B4 voice, B5 memory, B6 latency, B7 long-horizon. Phase 8 is authoritative here; the conflict is recorded, not smoothed, and must be resolved in M5 | family numbering |

## 6. Review requests

1. Approve or amend the ten requirements and their provisional thresholds (M5 will pre-register the final
   numbers before any measurement).
2. Confirm the voice-only constraint has no exception — especially for accessibility, where a text
   *transcript* is allowed but a text *conversation* is not.
3. Confirm the canon separation wording for C-10, including that the Rescue-armor support refers to the
   comics AI and never to the human Edwin Jarvis.
4. Answer Q17 and Q18, and confirm A7–A9.
5. Confirm that model selection remains blocked until M5 completes (gate G-O1).

## 7. Change log

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-11-01 | First published product capability specification: ten requirements (C-01…C-10) with dependencies, provisional acceptance criteria, data and permission needs, and planned validators; thirteen milestones (M1–M4 complete, M5–M13 planned); five gates including G-O1 blocking model selection. Derived directly from the project owner's requirement list of 2026-11-01; no external search was performed for these requirements. |
