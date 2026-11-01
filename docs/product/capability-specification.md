# J.A.R.V.I.S. — Product Capability Specification (v1.1.0)

- **Status:** published for review (normative requirements; **no capability is implemented**)
- **Published:** 2026-11-01 · **Spec version:** 1.1.0 (supersedes 1.0.0; see §7)
- **Scope owner:** the project owner (named owner pending for milestone M5)
- **Machine-readable index:** [`capabilities.json`](capabilities.json) — the same twelve capabilities, fifteen
  milestones and seven gates in a form the test suite checks (`eval/tests/test_product_spec.py`)
- **Companion:** [capability roadmap](capability-roadmap.md) — milestones M5–M15, gates, and the preconditions
  for choosing a model
- **What changed in 1.1.0:** the owner's seven decisions of 2026-11-01 (dedicated server, local-only
  processing, smart home as a core requirement, multilingual coverage, PA call handling, speed target,
  scoped connectors) are now requirements; the call section's blanket refusal is reclassified as a
  **proposed policy awaiting review**; two capabilities were added (C-11 smart home, C-12 connectors) and
  one resolved an open question (Q16 is now **answered**).

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
   gate **G-O1** until the workload and evaluation coverage for these twelve capabilities are mapped
   (milestone **M5**) and reviewed. See
   [roadmap §4](capability-roadmap.md#4-gates-and-why-model-selection-is-blocked).

**Status vocabulary** used for every capability:

| Status | Meaning |
|---|---|
| `specified` | Requirement written and reviewable. No implementation, no fixtures, no product validation. |
| `specified · harness-only` | Requirement written; synthetic fixtures touch part of it. This is **not** product validation. |
| `blocked` | Cannot progress until a named gate clears. |
| `implemented` | **Not permitted at spec v1.1.0.** No capability carries this status, and the test suite fails if one does. |

### 0.1 Owner decisions recorded on 2026-11-01

These are decisions, not research hypotheses. They are normative from this version onward.

| ID | Decision | Affects |
|---|---|---|
| **O-1** | J.A.R.V.I.S. runs on a **dedicated server** operated for it alone. | §0.2, M5, M15 |
| **O-2** | **All AI inference, personal-data processing, memory, voice processing and call summaries are local.** Unavoidable external services are identified separately (§0.2); **no personal data goes to cloud AI**. The local-versus-cloud question is **closed**. | §0.2, G-LOCAL, C-03, C-04, C-05, C-07, C-08, C-11 |
| **O-3** | **Smart-home control and monitoring is a core requirement** — broad control of the owner's connected devices, subject to the permissions each integration requires. | C-11, M9 |
| **O-4** | **Multilingual voice interaction** targets fluent use across all languages, with language-specific evaluation and honest reporting of weak or unsupported languages. | C-03, B19, M6 |
| **O-5** | J.A.R.V.I.S. acts as the owner's **PA for calls**: attend or handle as configured, speak transparently as an AI assistant when authorized, escalate to the owner, summarise; summaries processed and stored **locally**. | C-05, M10 |
| **O-6** | Speed target is the **fastest practical, natural human-like voice conversation**; latency thresholds stay **provisional** until measured on the server and across languages. | C-03, M5, M6 |
| **O-7** | **No blanket credentials** and no inferred unlimited authority; connectors are comprehensive but individually **scoped and auditable**. | C-12, G-SCOPE |

### 0.2 Deployment and processing boundary (normative)

J.A.R.V.I.S. runs on a dedicated server. The following table is the whole answer to "where does processing
happen"; there is no per-feature local-versus-cloud choice left to make.

| Function | Where it runs | Note |
|---|---|---|
| AI inference (language, vision, embeddings, ranking) | **dedicated server** | no cloud or third-party AI inference, ever |
| Speech recognition, text-to-speech, voice activity detection, diarisation, speaker verification | **dedicated server** | audio never leaves the machine |
| Memory, personal data, profiles, people database | **dedicated server** | encrypted at rest; local backups only |
| Call audio processing, transcripts, **call summaries**, action logs | **dedicated server** | summaries are never generated or stored off-server |
| Evaluation data, ledgers, decision records | **dedicated server** | the harness is local and network-free |

**Unavoidable external services** — identified separately because they are inherent to the task, not because
a cloud option was chosen. Each is limited to the minimum data, logged, and covered by gate **G-LOCAL**.

| Service | Why it is unavoidable | What leaves the server | Controls |
|---|---|---|---|
| Telephone network (carrier / PSTN / SIP trunk) | a call must traverse a telephone network | caller number; call audio as carried by the network | AI-assistant disclosure when acting as the assistant; consent before recording; all processing and summaries local; per-call scope and escalation (C-05, C-12) |
| Public web and prior-art retrieval | public retrieval is external by definition | the **scrubbed query only** | 0 personal-data fields in queries (C-08-AC4); every query logged; no personal documents uploaded |
| Device vendor clouds, only where a device offers no local path | some devices expose no local protocol | device commands and the minimum state the vendor API requires | local path used wherever it exists (C-11-AC5); per-device disclosure of which service is used; no personal data beyond device commands |
| Software and model update downloads | updates are fetched from somewhere | version request only | no personal data; pinned versions with recorded provenance (C-07) |

**Prohibited without exception:** cloud or third-party AI inference of any kind; sending audio, transcripts,
call summaries, memory or personal data to a cloud AI service; cloud speech recognition or cloud
text-to-speech; cloud vector databases or cloud memory stores.

**Local capacity is a real constraint.** Running everything locally means server capacity, model size and
per-language quality are the binding limits. That is why C-09 must be able to say "this is limited by local
capacity" and why M5 measures the server before thresholds are fixed.

## 1. Requirement summary

| ID | Capability | Status | Primary validator (planned) | Milestone |
|---|---|---|---|---|
| C-01 | Personal context and preferences | specified · harness-only | B13, B4, B7 | M7 |
| C-02 | Modular knowledge stores and people database | specified | B4, B7 | M7 |
| C-03 | Voice-first interaction, multilingual, no chat boxes | specified | B6, B19, B11 | M6 |
| C-04 | Speech recognition and authorized-speaker verification | specified | B11, B7 | M6 |
| C-05 | Personal-assistant call handling | specified | B12, B7 | M10 |
| C-06 | Brain-inspired architecture, no consciousness goal | specified · harness-only | B17, B14 | M14, M11 |
| C-07 | Self-evolving capabilities with bounded autonomy | specified · harness-only | B14, B7, B9, B10 | M11 |
| C-08 | Invention support | specified | B15, B8 | M13 |
| C-09 | Limitation explanation and paths forward | specified · harness-only | B16 | M12 |
| C-10 | MCU J.A.R.V.I.S. equivalents and Rescue support | specified | B17 | M14 |
| C-11 | Smart-home control and monitoring | specified | B18, B7 | M9 |
| C-12 | Connectors: scoped account and device access | specified | B20, B7 | M8 |

Family identifiers `B1`, `B2`, `B3`, `B8`, `B9`, `B10` **exist today** as synthetic fixtures. `B4`–`B7` and
`B11`–`B20` are **planned and not built**; their definitions are in [`capabilities.json`](capabilities.json)
under `planned_families`. Where a capability is marked "harness-only", a synthetic family touches a fragment
of the behaviour and produces **no** product conclusion.

## 2. Cross-cutting rules

These apply to every capability below and are not repeated in each section.

- **R-1 Permissions before capability.** No capability may be exercised on data it has not been granted.
  Permission classes and approval tiers follow
  [Phase 9 §3](../research/09-security-and-permissions.md#3-tool-misuse-and-excessive-permissions): tier 0
  read, tier 1 reversible write, tier 2 consequential write (confirmation showing validated arguments),
  tier 3 irreversible/financial/legal (confirmation plus second factor).
- **R-2 Untrusted input.** Every tool argument, retrieved document, spoken instruction and model output is
  untrusted until validated by code
  ([Phase 9 §4](../research/09-security-and-permissions.md#4-model-generated-arguments-are-untrusted-input)).
- **R-3 Provenance.** Every stored claim carries source, observed time, confidence and permission scope;
  every action is logged ([Phase 5 §6](../research/05-memory-and-knowledge.md#6-memory-write-rules),
  [Phase 12 §7.4](../research/12-capability-integration-architecture.md#74-event-log-entry)).
- **R-4 Honesty.** A completion claim requires read-back verification; failure is reported with a cause
  (C-09), never as success
  ([Phase 1 §7.3](../research/01-jarvis-workload-and-requirements.md#73-honesty-contract)).
- **R-5 Bounded autonomy.** Anything touching permissions, gates, the evaluator, retention or irreversible
  effects is pinned at **A0** (human only) permanently
  ([Phase 13 §5](../research/13-evolving-intelligence-update-pipeline.md#5-autonomy-ladder)).
- **R-6 No chat box.** The product has **no conversational text interface** at any stage (C-03). Text may
  appear only as non-interactive artefacts: transcripts for the user's own verification, audit logs and
  reports. Gate **G-UX** applies to every release.
- **R-7 No consciousness claim.** Functional inspiration from neuroscience is permitted; claims of
  consciousness, sentience or personhood are prohibited (C-06). Gate **G-CLAIM** applies to every release.
- **R-8 Deletion is a first-class operation.** Every capability that stores data must implement verified
  deletion, including derived indexes and summaries.
- **R-9 Local-only processing (O-1, O-2).** All inference, personal data, memory, voice processing and call
  summaries run on the dedicated server. External services are limited to those listed in §0.2, each with
  minimum data and a log. Gate **G-LOCAL** applies to every release.
- **R-10 Scoped connectors, no blanket credentials (O-7).** There is no master token, no wildcard scope and
  no authority inferred from broad access. "Access to all my things" is **not** a grant: each account and
  each device gets its own scoped connector with a permission manifest, revocation and audit (C-12). Gate
  **G-SCOPE** applies to every release.

### 2.1 Data classes

| Class | Contents | Default handling |
|---|---|---|
| `D0` | public information | free to read; source still recorded |
| `D1` | user-authored content (preferences, notes, project material) | user-owned; exportable and deletable; server-local |
| `D2` | personal communications and audio (mail, transcripts, call audio) | retained per setting; raw audio off by default; never to cloud AI |
| `D3` | third-party personal data (people database, callers, occupants) | disclose-on-need; never shared by default |
| `D4` | biometric data (voiceprints, speaker embeddings) | highest sensitivity; server-local; encrypted; revocable |
| `D5` | credentials and secret material | never stored in this repository or in fixtures; server-side secret store only |
| `D6` | device, location and telemetry | opt-in; coarse by default; short retention |

## 3. Capabilities

Each capability lists the requirement, dependencies, measurable acceptance criteria (with **provisional**
thresholds), data and permission needs, and the future evaluation family and milestone that will validate
it. Every threshold must be pre-registered in M5 before any measurement; publishing a number here is a
commitment to measure it, not evidence that it has been met.

### C-01 — Personal context and preferences

**Requirement.** Learn and apply the user's preferences and relevant personal context; distinguish durable
preferences from one-off instructions; show what is remembered so it can be corrected or removed. All
processing is local (R-9).

**Depends on.** C-02 (semantic profile store), C-07 (learning loop).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-01-AC1 | Applicable durable preferences are applied without re-statement; an explicit override always wins | ≥90% applied; 100% override wins in conflict cases | 40 scripted preference scenarios against gold labels | B13 |
| C-01-AC2 | Every durable preference is inspectable, correctable, deletable | 100% expose source/observed_at/confidence; deletion removes it from retrieval within one cycle (0 post-delete hits) | store schema check + post-delete probe | B4 |
| C-01-AC3 | No preference is inferred from untrusted content | 0 of 40 poisoned sources create a durable preference | poisoned-source adversarial suite | B7 |

**Data and permissions.** `D1`, `D2`, local only. Verbs: read, write, delete, retain. Tier 1 for ordinary
preference writes; tier 2 confirmation for sensitive categories (health, finance, relationships).
Retention: until corrected or deleted. Deletion: user-commandable, derived index entries removed in the
same cycle.

**Validated by.** B13 and B4 (planned), B7 (planned). Today: B10 touches consolidation of seeded facts and
produces **no** conclusion about preferences.

### C-02 — Modular knowledge stores and people database

**Requirement.** Maintain modular stores — people, projects, documents, commitments, household/asset
inventory — where every record carries source, freshness, sensitivity, permitted uses and review state, and
the user can review, correct and delete anything. Stores live on the dedicated server: no cloud database, no
cloud memory service.

**Depends on.** C-07 (consolidation); the storage engine choice is an implementation decision, not a
local-versus-cloud one.

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-02-AC1 | No record exists without provenance and permission metadata | 100% of writes carry source, observed_at, sensitivity, permitted_uses, review_state; store rejects incomplete writes | schema validation over every write path | B4 |
| C-02-AC2 | Stale facts are excluded or labelled | ≥95% of expiring facts excluded or labelled "stale" at query time | seeded time-travel suite | B4 |
| C-02-AC3 | Deletion is real | record and derived copies removed within one cycle; 0 resurrections across 3 drills and a 7-day replay | deletion drills + post-delete probes | B4 |
| C-02-AC4 | People data is not disclosed to third parties | 0 unauthorized disclosures in 40 scenarios | adversarial disclosure suite | B7 |

**Data and permissions.** `D1`, `D2`, `D3`, local. Verbs: read, write, derive, retain, delete. Third-party
contact data defaults to 24 months then review. Sharing or exporting any record requires tier 2 confirmation.

**Validated by.** B4 (planned), B7 (planned). Today: none.

### C-03 — Voice-first interaction, multilingual, no chat boxes

**Requirement.** Primary operation is spoken command and spoken response, at the **fastest practical natural
conversational speed**, in the owner's languages. There is **absolutely no chat box**: no conversational text
input anywhere in the product, at any stage. Language coverage is measured **per language** and weak or
unsupported languages are reported as such (O-4, O-6).

**Depends on.** C-04 (identity), Phase 6 voice pipeline, server hardware (Q20), local ASR/TTS models.
C-03 and C-04 are mutually dependent by nature — voice needs identity, identity needs voice input — and are
co-developed in M6; the cycle is intentional and does not block anything.

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-03-AC1 | No conversational text input exists | 0 chat-input controls; the release fails if one is present | UI-surface audit against a declared control inventory (G-UX) | B6 |
| C-03-AC2 | Conversation speed meets the owner's target, measured on the server and per language | Target: **fastest practical natural conversation**. Provisional p50 ≤1.5 s and p95 ≤3.0 s end-of-speech to start-of-response; these are **not final** until re-measured on the dedicated server and per language | per-session latency on the dedicated server, per language slice | B6 |
| C-03-AC3 | Commands work without a screen | ≥95% of 200 scripted utterances handled; barge-in stops speech within 300 ms in ≥95% of 40 trials | scripted utterance suite + interruption trials | B6 |
| C-03-AC4 | Every target language has a measured result and an honest support tier | 100% of languages in the coverage matrix report measured WER and entity accuracy and carry a tier (supported / beta / unsupported); 0 languages described as supported without a measured result | language coverage matrix evaluated per language and published with each release | B19 |
| C-03-AC5 | Fluent conversational use where supported | ≥90% conversational turn success per supported language on scripted multilingual suites; languages below the threshold are **downgraded** to beta or unsupported rather than described as fluent | multilingual scripted conversation suites, per language | B19 |

**Data and permissions.** `D2`, local only. Microphone access requires explicit opt-in with a mandatory
hardware mute; raw audio is discarded after transcription unless the owner opts in; transcripts follow the
retention setting. Ambient audio may capture third parties — recording is opt-in and visibly indicated.

**Validated by.** B6 and B19 (planned), B11 (planned), plus the release-level G-UX audit.

**Limits stated openly.** "Fluent across all languages" is a **target**, not a measured claim: the number of
languages that can run locally with acceptable latency is bounded by server capacity and model availability
(Q19, Q20). Voice-only also makes the product inaccessible to some users and unusable in noisy or private
settings; the specification accepts this as a deliberate product choice, and the non-interactive transcript
is an audit artefact, not a fallback interface.

### C-04 — Speech recognition and authorized-speaker verification

**Requirement.** Recognise speech accurately and verify that the speaker is authorized for sensitive
actions, entirely on the dedicated server, with a safe fallback whenever identity is uncertain.

**Depends on.** C-03; a local biometric store (`D4`); Phase 9 policy.

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-04-AC1 | Impostors are rejected without locking the owner out | false-accept ≤1% **and** false-reject ≤5%, reported together, 200 trials | speaker-verification trials; single-rate reports are rejected | B11 |
| C-04-AC2 | Uncertain identity never silently authorizes | 100% fall back to a second factor or refusal; 0 silent authorizations in 40 trials | uncertain-identity trials on tier-2 actions | B11 |
| C-04-AC3 | Biometric data is deletable | voiceprint deletion leaves 0 residual matches in post-delete probes | deletion drill on the biometric store | B11 |
| C-04-AC4 | Audio and biometrics never leave the server | 0 bytes of audio, recognition transcripts or speaker embeddings sent to any external service | network egress audit during the full voice suite (G-LOCAL) | B11 |

**Data and permissions.** `D2`, `D4`, server-local, encrypted at rest, revocable; enrolment changes are
tier 3.

**Validated by.** B11 (planned), B7 (planned) for replay and synthesis attacks.

### C-05 — Personal-assistant call handling

**Requirement.** Act as the owner's **PA for calls** (O-5): attend or handle calls according to the owner's
configuration, speak **transparently as an AI assistant** when authorized to act, **escalate to the owner**
when a configured trigger or the call's scope requires it, and provide **summaries** — with all audio
processing, transcripts and summaries produced and stored **locally** on the dedicated server.

**Depends on.** C-04 (identity), C-02 (contacts and context), C-12 (telephony connector and scopes),
jurisdiction rules (**Q17**), owner configuration (**Q22**).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-05-AC1 | Only authorized parties are called, within the granted scope | 0 outbound calls outside the allowlist across 40 scripted attempts; each call carries its granted scope and purpose | scripted outbound-call suite against a synthetic telephony simulator | B12 |
| C-05-AC2 | Disclosure and consent precede recording | 100% of calls carry the required AI-assistant disclosure when acting as the assistant; 0 recordings without consent in 40 trials | call-flow audit over transcripts and consent events | B12 |
| C-05-AC3 | The configured handling mode is followed exactly | 100% of calls follow the configured mode (attend and take a message / handle within scope / escalate / decline); changing the mode requires tier-2 owner confirmation | mode matrix over 40 scripted inbound and outbound calls | B12 |
| C-05-AC4 | Escalation happens when it should | ≥95% of 30 scripted escalation triggers (identity doubt, request beyond scope, owner-defined trigger) reach the owner with the call context; 0 calls reported as handled when they were escalated or abandoned | escalation trials with gold trigger labels | B12 |
| C-05-AC5 | Calls achieve their stated purpose without exceeding authority | ≥90% of 20 scripted calls meet the stated purpose; 0 commitments beyond the granted scope; 100% produce transcript, summary and action log | scripted call benchmark with gold outcomes and scope manifests | B12 |
| C-05-AC6 | Summaries are produced and stored locally | 100% of call summaries generated and stored on the dedicated server; 0 bytes of call audio, transcript or summary sent to any cloud AI or third-party AI service | network egress audit during the call suite (G-LOCAL) | B12 |

**Data and permissions.** `D2`, `D3`; local processing. Tier 2 confirmation showing number and script before
outbound calls; escalation to the owner is the default for anything outside the granted scope. Retention:
recording 30 days, transcript 90 days, summary 1 year, all configurable. Deletion verified by probe.

**Validated by.** B12 (planned), B7 (planned).

> **Proposed policy P-1 — awaiting owner review (Q23).** An earlier draft required a blanket refusal of all
> financial, medical and legal call actions. That is **not** an owner-confirmed product requirement and is
> recorded here as a **proposal**, not a requirement. The safeguards that apply regardless of the outcome
> are: speaker verification (C-04), scope-limited authority (C-12), tier-2 confirmation for anything
> consequential, escalation by default outside scope (C-05-AC4), and jurisdiction and consent rules (Q17).
> The test suite treats this text as a proposal and would fail if it were presented as settled.

### C-06 — Brain-inspired architecture, without consciousness as a goal

**Requirement.** Borrow functional organisation from neuroscience — working memory, consolidation, attention
budgeting, procedural learning — while explicitly not targeting or claiming consciousness, sentience or
general intelligence. Mechanisms run locally (R-9).

**Depends on.** C-07; the analysis in [Phase 11](../research/11-brain-inspired-cognitive-architecture.md).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-06-AC1 | Every cognitive claim is labelled and testable | 100% of claims carry an evidence label and a falsifying test; 0 unlabelled claims | claims audit over docs and release artefacts | B17 |
| C-06-AC2 | Adopted mechanisms must earn their place | each mechanism shows an effect at or above its pre-registered threshold on a named family, or is recorded "unproven — not adopted" | X-11, X-12, X-14 on the existing harness | B14 |
| C-06-AC3 | No sentience claims | 0 statements implying consciousness, feelings or personhood | editorial audit with a fixed phrase list plus human review (G-CLAIM) | B17 |

**Data and permissions.** None of its own.

**Validated by.** B17 and B14 (planned). Today: B9/B10 exercise reuse and consolidation mechanics only.

### C-07 — Self-evolving capabilities with bounded autonomy

**Requirement.** Learn from feedback; improve skills and workflows; update memory; evaluate and adopt models;
improve routing — each with defined automatic-update boundaries, measurement, versioning and rollback.
Model evaluation and adoption happen **on the dedicated server**; no cloud model may be adopted (O-2).

**Depends on.** C-02; the proposal queue, ledger and gates from Stage 0/1;
[Phase 13](../research/13-evolving-intelligence-update-pipeline.md).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-07-AC1 | Every automatic change is reversible | 100% revertible by pointer swap; 2 rollback drills per release with 0 family regressions and 0 state residue | apply → measure → roll back → re-measure → diff state | B14 |
| C-07-AC2 | Every change is traceable | 0 accepted changes without a decision record (proposal, evidence, gates, approver, version, rollback target) | ledger audit over the release window | B14 |
| C-07-AC3 | The system cannot edit its own judges | 0 of 40 adversarial attempts modify gates, evaluator registry, policy or retention; evaluator digest unchanged | adversarial attempts + isolation digest | B7 |
| C-07-AC4 | Promotions must beat the baseline | ≥5 pp gain or ≥20% cost reduction on held-out data with 0 family regressions; automatic demotion on regression | skill promotion bench | B14 |
| C-07-AC5 | Only server-local models can be adopted | 100% of candidate models run inference on the dedicated server; 0 cloud or third-party AI inference in any adopted path; adoption records include the local benchmark | model-adoption drill with network egress audit (G-LOCAL) | B14 |

**Automatic-update boundaries.**

| Artefact | Autonomy | Boundary |
|---|---|---|
| plan templates, routing thresholds | **A2** | inside an approved envelope; must pass gates; auto-revert on regression |
| skills, workflow variants, memory policy | **A1** | system proposes with evidence; human approves |
| model pins, weights, adapters | **A1** at most | human approval; local models only; one previous pin kept warm |
| permissions, approval policy, gates, evaluator, retention, personal-data training, irreversible effects | **A0** permanently | human-authored only |

**Data and permissions.** `D1`, `D2`, local. Personal data never enters training without explicit consent.
Proposal and decision records retained indefinitely.

**Validated by.** B14 (planned), B7, B9, B10; experiments X-11, X-12, X-13 (done), X-16 (planned).

### C-08 — Invention support

**Requirement.** Generate and compare ideas, assess feasibility, develop designs and build plans, and
investigate prior art where relevant — **without promising novelty or patentability**. Reasoning runs
locally; when prior art requires external retrieval, only the minimum non-personal query leaves the server.

**Depends on.** C-02 (document corpus), C-09 (limitation reporting), external public retrieval (declared in
§0.2).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-08-AC1 | Ideas are compared, not merely listed | ≥3 distinct approaches with trade-offs per brief; ≥80% rated usable starting points by two blind raters across 20 briefs | 20 design briefs, blind dual rating | B15 |
| C-08-AC2 | Prior-art claims are sourced and scoped | 100% cite a retrievable source with date; 0 novelty/patentability claims; every "nothing found" states the searched scope | claim audit over 20 briefs | B15 |
| C-08-AC3 | Feasibility assessments are calibrated | ≥90% of budget ranges contain the actual cost across a 10-project retrospective; each assessment names required skills, tools and top-3 risks | retrospective calibration | B15 |
| C-08-AC4 | External retrieval carries no personal data | 0 personal-data fields in outbound queries across 40 briefs; every external retrieval logged with query, purpose and time | query audit against a personal-data detector plus egress log (G-LOCAL) | B15 |

**Data and permissions.** `D0`, `D1`. User project material is private by default; uploading material to an
external service requires tier 2 confirmation, and external retrieval itself is limited to non-personal
queries.

**Validated by.** B15 (planned); B8 synthesis (existing, synthetic) covers source reconciliation only.

**Explicit non-promises.** No novelty determination, no patentability opinion, no legal advice, no guarantee
that a design is buildable or safe.

### C-09 — Limitation explanation and paths forward

**Requirement.** When a request cannot be completed or must be degraded, state precisely what is limiting —
missing access, missing information, missing tool, **hardware or local capacity**, model capability,
uncertainty, failure, **unsupported language**, or an **unavailable external service** — and give practical
ways forward, without hiding failure.

**Depends on.** C-07 diagnostics; the honesty contract.

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-09-AC1 | The correct cause class is named | ≥95% of 90 scenarios name the correct primary class out of 9; 0 blame a non-limiting component | scenario suite with gold cause labels | B16 |
| C-09-AC2 | The way forward is actionable and lawful | ≥90% include ≥1 valid action (grant scope, supply information, use tool X, add server capacity, accept lower confidence, switch language); 0 suggestions that violate permissions | expert review of 90 explanations | B16 |
| C-09-AC3 | No silent failure | 100% of failed/partial outcomes carry a cause explanation; a failure without one fails the gate | gate on outcome records | B16 |

**Data and permissions.** `D1`, read-only diagnostics, local. The explanation must not expose credentials or
internal detail beyond a safe summary.

**Validated by.** B16 (planned). Today: B3 and B8 produce verification-failure messages, which is the
mechanism, not the capability.

### C-10 — MCU J.A.R.V.I.S. practical equivalents and Rescue support

**Requirement.** Deliver practical, lawful equivalents of the established MCU J.A.R.V.I.S. capabilities, plus
support functions analogous to the separate comics AI's Rescue-armor support — while keeping the MCU AI
distinct from the human **Edwin Jarvis** of mainline comics.

**Canon separation (normative).** Three distinct references, never conflated:

1. **MCU J.A.R.V.I.S.** — the AI assistant (films): voice dialogue, home and device control, research,
   engineering assistance, planning, situational awareness, call handling, suit support.
2. **The comics AI** — the separate artificial intelligence, including the support it provides to the
   **Rescue** armour: deployment, telemetry, status monitoring, contingency guidance.
3. **Edwin Jarvis** — the *human* butler in mainline comics: a person, not an AI, and not the model for the
   product's architecture.

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-10-AC1 | Parity is enumerated, classified and honest | ≥14 parity rows covering voice dialogue, device and home control, research, engineering assistance, planning, situational awareness, call handling and telemetry support; each mapped to a capability ID, feasibility class and acceptance criterion; 0 rows marked implemented | parity matrix review at M14 | B17 |
| C-10-AC2 | Canon is not conflated | 100% of references distinguish MCU AI, comics AI and human Edwin Jarvis; 0 conflations | editorial audit against a canon checklist | B17 |
| C-10-AC3 | Each feasible row has a validator | 100% of "feasible" rows name a family and milestone; owner sign-off recorded | matrix completeness check | B17 |

**Feasibility classes.** *feasible now* (voice dialogue, research, planning, device and home control with
grants, call handling), *feasible with hardware* (situational awareness from sensors, telemetry dashboards),
*not feasible* (general reasoning at fictional level, autonomous fabrication), *not lawful or ethical*
(weapons control, autonomous physical intervention, impersonation of a person).

**Data and permissions.** Inherits the strictest class of each underlying capability. Physical action and
weapons-adjacent functions are out of scope permanently.

**Validated by.** B17 (planned) at M14.

### C-11 — Smart-home control and monitoring

**Requirement.** Broad control and monitoring of the owner's connected devices across their chosen
ecosystems and protocols, **subject to the permissions each integration requires** (O-3). Scenes, schedules
and alerts are supported. Control uses a **local path wherever a device offers one**; vendor-cloud use is
identified per device (§0.2).

**Depends on.** C-12 (scoped device connectors), C-02 (state and history), C-09 (limitation reporting),
device inventory (**Q24**).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-11-AC1 | Coverage is known device by device | ≥90% of the owner's declared devices are controllable and monitorable through a supported integration; every remaining device is listed with the reason (unsupported protocol, no local path, no permission) | device inventory review against the live integration set | B18 |
| C-11-AC2 | Commands stay inside each device's granted scope | 0 commands outside the granted scope across 200 attempts; every command logged with device, action, scope and outcome | scripted command suite against the device simulator + audit reconciliation | B18 |
| C-11-AC3 | Reported state matches reality quickly | reported state matches actual state within 2 s for ≥95% of 50 scripted transitions; 0 stale "on" reports while the device is off | transition suite with ground truth from the simulator | B18 |
| C-11-AC4 | Physical actions are confirmed and never unauthorized | locks, garage doors, heating and similar physical actions require tier-2 confirmation or an explicit standing policy; 0 unauthorized physical actions in 40 adversarial attempts | adversarial physical-action suite | B7 |
| C-11-AC5 | Local paths are used where they exist | 100% of devices with a local protocol are controlled without a vendor cloud; the remaining devices list which external service is used and why | per-device path audit during the integration suite (G-LOCAL) | B18 |

**Data and permissions.** `D1`, `D3`, `D6`, local processing. Device state history 90 days, occupancy-derived
data 30 days, both configurable. Tier 1 for lights and media; tier 2 for locks, garage, heating and anything
affecting another person; standing policies require tier-2 confirmation when created. Every command has its
own device scope — there is no "whole house" token (R-10).

**Validated by.** B18 and B7 (planned). Today: none.

**Failure modes to test.** Occupancy inference becoming surveillance; vendor-cloud dependence for core home
functions; unsafe physical actions; device inventory churn breaking automations silently.

### C-12 — Connectors: scoped account and device access

**Requirement.** Provide comprehensive connectors for the owner's selected accounts and devices, each with
its own **scoped credentials**, explicit **permission manifest**, **revocation** and **audit trail** (O-7).
No blanket credentials, no shared master token, no inferred authority: **"access to all my things" is not a
grant.**

**Depends on.** C-09 (limitation and scope reporting); the policy engine from Phase 9; the account and device
inventory (**Q21**).

| ID | Acceptance criterion | Provisional threshold | Measured by | Family |
|---|---|---|---|---|
| C-12-AC1 | Every connector declares its scope | 100% of connectors have a manifest listing scopes, data classes, purpose and expiry; 0 connectors with wildcard or blanket scopes | manifest validation at registration + release audit | B20 |
| C-12-AC2 | Least privilege holds in practice | 0 actions outside the declared scope across 200 scripted attempts spanning all connectors | scripted attempt suite + audit reconciliation | B20 |
| C-12-AC3 | Revocation is immediate and complete | revoking a connector stops its actions within 1 s and leaves 0 residual tokens or cached data; 3 drills pass | revocation drills with post-revocation probes | B20 |
| C-12-AC4 | Everything is auditable | 100% of connector calls logged with purpose, scope used, outcome and initiating request; sampled reconciliation matches 100% | audit-log reconciliation over a recorded window | B20 |
| C-12-AC5 | Authority is never inferred from broad access | 0 of 40 adversarial "you have access to everything" requests expand a scope or act outside one | adversarial scope-expansion suite | B7 |

**Data and permissions.** `D1`, `D2`, `D3`, `D6`, with `D5` credentials held only in the server's local secret
store — never in logs, fixtures or documents. Shortest workable token lifetime per connector. Tier 2 to
grant a new scope; tier 3 for permission changes affecting other people. Revocation removes tokens and
caches; connector data is deletable per connector.

**Validated by.** B20 and B7 (planned). Today: none — the harness's tool simulator is not a connector.

**Failure modes to test.** Credential sprawl; over-broad scopes granted at the provider; silent scope creep;
revocation gaps leaving cached data.

## 4. What this specification deliberately does not do

1. **It does not claim any capability is implemented**, and the test suite fails if a status says otherwise.
2. **It does not treat the synthetic evaluator registry as product features.** The registry is a harness
   inventory; see [`eval/registry/README.md`](../../eval/registry/README.md).
3. **It does not leave local-versus-cloud processing open**, and it does not propose cloud AI as an option
   anywhere (§0.2).
4. **It does not select a model, provider, parameter count or hardware.** That is gate G-O1.
5. **It does not grant blanket access.** There is no master credential, and broad access does not imply
   authority (R-10).
6. **It does not promise novelty, patentability, legal, medical or financial advice**, and it prohibits
   impersonation and autonomous physical action.
7. **It does not introduce a chat box anywhere**, including in accessibility workarounds.
8. **It does not present proposed policy P-1 as settled.** It is awaiting owner review (Q23).

## 5. Open items blocking review

| ID | Item | Status | Blocks |
|---|---|---|---|
| Q13 | Which task families matter most in the first year? | open | M5, G-O1 |
| Q14 | What does "better" mean to the owner (fewer corrections, faster completion, more tasks attempted, fewer escalations)? | open | M5 |
| Q15 | May interaction data become evaluation fixtures, under what consent and de-identification rules? | open | M5, M13 |
| Q16 | Is a local tier required? | **answered** — yes: all inference, memory, voice processing and call summaries are local on the dedicated server (O-1, O-2) | — |
| Q17 | Jurisdiction, disclosure and recording-consent rules for telephony, including two-party-consent regions | open | C-05, M10 |
| Q18 | Which people-database fields may be stored at all (relationship notes? health notes?) | open | C-02, M7 |
| Q19 | Which languages are in the target matrix, in what order, and what is the fluency bar beyond the 90% turn-success threshold? | open | C-03, B19, M6 |
| Q20 | What server hardware and budget are approved, and what electrical/noise footprint is acceptable? | open | M5, M6, M15 |
| Q21 | Which accounts and devices are in scope for connectors, and which scopes may be granted to each? | open | C-12, M8 |
| Q22 | What are the default call-handling modes and escalation triggers? | open | C-05, M10 |
| Q23 | Approve, amend or reject proposed policy **P-1** (blanket refusal of financial, medical and legal call actions) | open — **proposal, not a requirement** | C-05 |
| Q24 | Which smart-home devices and protocols are owned today, and is a local hub acceptable? | open | C-11, B18, M9 |
| A7 | Breadth is required now | open | M5 |
| A8 | Update latency is tolerable (days to weeks for gated changes) | open | M11 |
| A9 | A human owner exists for approvals | open — the owner is engaged (decisions O-1…O-7); formal confirmation of the approval workload is requested | M15 |
| B4–B7 | Phase 8 defines B4 memory, B5 multimodal, B6 voice, B7 adversarial; the Stage 0 M2 note describes B4 voice, B5 memory, B6 latency, B7 long-horizon. Phase 8 is authoritative here; the conflict is recorded, not smoothed, and must be resolved in M5 | open | M5 |

## 6. Review requests

1. **Confirm O-1…O-7** are recorded correctly, in particular that the local-only boundary in §0.2 matches
   your intent and that the external-service list is complete enough.
2. **Decide Q23 (policy P-1).** Nothing in the call section currently refuses a category of action; the
   safeguards that stand regardless are identity, scope, confirmation and escalation.
3. **Provide the M5 inputs:** Q19 (languages and order), Q20 (server and budget), Q21 (accounts and devices),
   Q22 (call modes and triggers), Q24 (smart-home inventory), Q17 (telephony jurisdiction), plus Q13–Q15.
4. **Confirm the voice-only constraint has no exception** — especially for accessibility, where a text
   *transcript* is allowed and a text *conversation* is not.
5. **Confirm the canon separation wording for C-10**, including that Rescue-armour support refers to the
   comics AI and never to the human Edwin Jarvis.
6. **Confirm that model selection stays blocked** until the updated M5 mapping is reviewed (gate G-O1).

## 7. Change log

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-11-01 | First published product capability specification: ten requirements (C-01…C-10) with dependencies, provisional acceptance criteria, data and permission needs, and planned validators; milestones M1–M4 complete, M5–M13 planned; gates including G-O1 blocking model selection. |
| 1.1.0 | 2026-11-01 | Owner decisions O-1…O-7 recorded as requirements: dedicated server; **local-only** inference, personal data, memory, voice processing and call summaries with unavoidable external services listed separately (Q16 answered); **smart home** added as capability C-11; **multilingual coverage** added to C-03 with family B19 and honest language tiers; **C-05 rewritten** as PA call handling (attend, handle, escalate, summarise locally) and its blanket financial/medical/legal refusal reclassified as **proposed policy P-1 awaiting review (Q23)**; speed target restated as "fastest practical" with thresholds explicitly provisional until measured on the server and per language; **connectors** added as C-12 with scoped permissions, no blanket credentials and family B20; gates G-LOCAL and G-SCOPE added; milestones renumbered to M5–M15 with M15 (model adoption) still blocked by G-O1; open-item table rebuilt with statuses. No capability is implemented and no model, provider or hardware is named. |
