# `docs/product/` — product requirements for J.A.R.V.I.S.

This directory holds the **product** view of the project: what J.A.R.V.I.S. must do, in what order it will be
built, and what would count as having done it. It is deliberately separate from `docs/research/` (which asks
what is true and what is feasible) and from `eval/` (which asks whether the evaluator is trustworthy).

> **Nothing here is implemented.** There is no application, no user interface, no model and no user data in
> this repository. Every capability below is `specified`, not delivered. The home holographic interface is a
> paragraph of specification — no display technology has been chosen — and no client application exists for
> any device target. The synthetic evaluator registry in
> `eval/registry/` is a **test-harness inventory** of simulators and is not evidence that any product
> capability exists — see [`eval/registry/README.md`](../../eval/registry/README.md).

## Contents

| Document | What it is | Version |
|---|---|---|
| [Capability specification](capability-specification.md) | Normative requirements: 14 capabilities (C-01…C-14) with dependencies, measurable acceptance criteria, data and permission needs, and the future evaluation family and milestone that validate each; deployment and processing boundary; normative telephony platform limits; owner decisions O-1…O-12; proposals PR-1/PR-2 marked as proposals | 1.2.0 |
| [Capability roadmap](capability-roadmap.md) | Milestones M5–M16, exit and kill criteria, seven gates — including G-O1, which blocks model selection, and G-LOCAL, which forbids personal data reaching cloud AI | 1.2.0 |
| [Device support matrix](device-support-matrix.md) | Every declared device target (PC, tablet, iPhone, Android phone, VR, Ray-Ban glasses) plus EDITH Glass as a separate future project: interaction modes, integration boundary, platform dependency and permissions, and what is unverified; telephony limits per channel; scoped-access rules; AETHERION recorded as out of scope. **0 of 7 targets verified** | 1.0.0 |
| [`capabilities.json`](capabilities.json) | Machine-readable index of the same 14 capabilities, 16 milestones, 7 gates, 18 open items (one now resolved) and the device matrix; checked by `eval/tests/test_product_spec.py` | 1.2.0 |
| [M5 coverage matrix](coverage-matrix.md) | Working draft mapping all 14 capabilities to current evidence, planned evaluation families, local data boundary, outstanding owner inputs, and criterion-level coverage (§8) | Draft |
| [B4–B7 crosswalk](family-crosswalk.md) | Supersession register resolving the Phase 8 vs Stage-0 family numbering conflict; Phase 8 numbering retained, M2/M3 labels superseded by name only | 1.0.0 |
| [Evaluation family backlog](family-backlog.md) | Per-family implementation plan for B4–B7 and B11–B22: task types, synthetic fixtures, scoring, planted regressions, privacy constraints, spec thresholds, harness changes — generated from [`family-backlog.json`](family-backlog.json). Two families (B4, B16) now have small **implemented evaluator slices** (B4 has three); no family is implemented | 1.2.0 |
| [`ac-coverage.json`](ac-coverage.json) | All 61 acceptance criteria mapped exactly once to a planned suite, named audit, human review or owner-input block; no criterion is validated | 1.4.0 |

## M5 artefacts

The three M5 artefacts above move the coverage mapping from "which family covers which capability" to "what each family would have to build and measure". They are mostly planning documents: B4 and B16 now have small evaluator slices (`eval/tasks/b4`, `eval/tasks/b4d` — deletion drills with backup/restore, including interrupted restores and retries — `eval/tasks/b4w`, a synthetic write-ahead-log durability store crashed at evaluator-chosen byte offsets, and `eval/tasks/b16`) with live gates and planted regressions, the other fourteen families have no task set, no family is marked `implemented`, and the backlog deliberately avoids naming a model, provider, parameter count or hardware. See the [crosswalk](family-crosswalk.md) for the naming resolution and [`ac-coverage.json`](ac-coverage.json) for the criterion-level gaps.

## The requirement set at a glance

| ID | Capability | Milestone | Validator (planned) |
|---|---|---|---|
| C-01 | Learn and apply preferences and relevant personal context | M7 | B13, B4, B7 |
| C-02 | People database and modular stores with source, freshness, sensitivity, permissions, review/correction/deletion | M7 | B4, B7 |
| C-03 | Voice-first commands and responses — **no chat boxes**, multilingual with honest per-language tiers, fastest practical speed | M6 | B6, B19, G-UX |
| C-04 | Speech recognition and authorized-speaker verification with a safe fallback, all local | M6 | B11, B7 |
| C-05 | **PA for calls**: attend or handle as configured, speak transparently as an AI, escalate, summarise locally | M10 | B12, B7 |
| C-06 | Brain-inspired AI **without consciousness as a goal** | M14, M11 | B17, B14 |
| C-07 | Self-evolving capabilities with automatic-update boundaries, measurement, versioning and rollback (server-local models only) | M11 | B14, B7, B9, B10 |
| C-08 | Invention support: ideas, feasibility, designs, prior art — no novelty or patentability promises, no personal data in external queries | M13 | B15, B8 |
| C-09 | Explain what is limiting it (9 cause classes), and practical ways forward | M12 | B16 |
| C-10 | Practical MCU J.A.R.V.I.S. equivalents plus comics-AI Rescue support, with the MCU AI kept distinct from human Edwin Jarvis | M14 | B17 |
| C-11 | **Smart-home control and monitoring**, subject to each integration's permissions, local path where available | M9 | B18, B7 |
| C-12 | **Connectors** for the owner's accounts and devices: scoped, revocable, auditable — **no blanket credentials** | M8 | B20, B7 |
| C-13 | **Home holographic interface**: a voice-first room-scale display, specified and **not built** — no display technology, optics, prototype or purchase; no chat box on it | M16 | B21, B7 |
| C-14 | **Device surfaces and platform limits**: PC, tablet, iPhone, Android phone, VR, Ray-Ban glasses; EDITH Glass separate and undefined; platform limits stated, never promised | M16 | B22, B7 |

## Owner decisions this set is built on (2026-11-01)

Recorded as **O-1…O-7** in the [specification §0.1](capability-specification.md#01-owner-decisions-recorded-on-2026-11-01):

1. **O-1** Dedicated server.
2. **O-2** All AI inference, personal-data processing, memory, voice processing and call summaries are
   **local**; unavoidable external services (telephone network, public retrieval, device vendor clouds,
   updates) are listed separately and no personal data goes to cloud AI. The local-versus-cloud question is
   **closed**, and gate **G-LOCAL** enforces it per release.
3. **O-3** Smart home is a **core** requirement (C-11).
4. **O-4** Multilingual voice targets fluent use across all languages, with language-specific evaluation and
   honest reporting of weak or unsupported languages (B19).
5. **O-5** J.A.R.V.I.S. is the owner's **PA for calls**, with local summaries (C-05).
6. **O-6** Speed target is the **fastest practical** natural conversation; latency thresholds stay
   provisional until measured on the server and per language.
7. **O-7** **No blanket credentials** and no inferred unlimited authority (C-12, G-SCOPE).
8. **O-8** **Everyday assistance** across the owner's life is the scope; no priority ranking between task
   classes was given, and none is invented.
9. **O-9** **Voice-first, no chat boxes, on every surface** — including the holographic surface and every
   device.
10. **O-10** The primary spatial display is a **home holographic interface**, specified and not built.
11. **O-11** Declared targets: PC, tablet, iPhone, Android phone, VR, Ray-Ban glasses. **EDITH Glass** is a
    separate future project with undefined requirements, and retail glasses are never called EDITH.
12. **O-12** **AETHERION** is a separate, postponed hardware research effort and is out of scope here.

## How this set was produced

The requirements were supplied directly by the project owner on 2026-11-01, in the owner's own words, and
were **not** derived from web research. They are recorded without softening: "absolutely no chat boxes",
"brain-inspired AI without consciousness as a goal", "no promising novelty or patentability" are normative
constraints, not aspirations. Where a requirement needs data the project does not have (jurisdiction rules,
server budget, device inventory, language priorities), it is listed as an open question (Q13–Q26) rather
than assumed.

One earlier draft went further than the owner did: it required a blanket refusal of financial, medical and
legal call actions. That is now **proposed policy P-1**, awaiting the owner (Q23) — not a requirement.

Two further items are **proposals, not decisions**, and say so in the text and in the index:

- **PR-1 — language matrix.** Fifteen languages (English, Spanish, Mandarin, Hindi, Arabic, French,
  Portuguese, Bengali, Russian, Japanese, German, Urdu, Indonesian, Turkish, Swahili) offered as a starting
  point for **Q19**. No language is claimed to work, no order is proposed, and the fluency bar is still the
  owner's to set.
- **PR-2 — server capacity tiers.** 16–32 cores; 128 GB memory lower and 256 GB preferred; one GPU with at
  least 32 GB of memory lower and 96 GB preferred; separate fast storage; wired networking; a UPS; encrypted
  local backups with a tested restore. It names **no vendor, model or purchase**, and **Q20 and G-O1 stay
  open/blocked**. Primary sources are linked in the specification and in the research addendum.

## What this plan now promises, and what it refuses to promise

| It promises | It refuses |
|---|---|
| A written, measurable requirement for a **home holographic interface**, with five acceptance criteria, a kill criterion and no hardware selected | That the interface exists, works, is demonstrated, or that a display technology has been chosen — it refuses even to imply a date |
| A **device matrix** that states, for each target, its modes, boundary, dependencies and what is unverified | That any target is supported or verified; there is no client for any of them |
| That surfaces are **separate**: the home surface is not VR, and Ray-Ban glasses are not EDITH Glass | Any claim that retail glasses are EDITH, or that EDITH requirements are known |
| **Everyday assistance** as the workload scope (O-8) | A priority ranking of task classes, or a claim that any has been delivered |
| A **language proposal** and a **server proposal**, both sourced and both labelled as proposals | Fluency in any of the fifteen languages, an approved budget, a chosen vendor or a purchase |
| **Platform-honest telephony:** Android screening and dialer roles, iPhone VoIP, an owner-controlled line | Ordinary iPhone cellular-call audio, at any milestone, ever |
| **Scoped, revocable, auditable** access per device (O-7, R-10) | Blanket access, "all my things", or authority inferred from a broad grant |
| Honest degradation: a missing display or device is reported, not simulated (C-13-AC4, C-14-AC5) | Silent success, or a fabricated projection |

## Relationship to the research and the evaluator

| Layer | Question it answers | Where |
|---|---|---|
| Product | What must the system do, and how will we know? | this directory |
| Research | What is true, what is feasible, what evidence exists? | [`docs/research/`](../research/README.md) |
| Evaluation | Can the measurement itself be trusted? | [`eval/`](../../eval/README.md) |

The evaluator's capability registry is an **inventory of what the harness can simulate** (calendar, mail,
files, retrieval, extraction, reconciliation, scheduling, memory consolidation, verification, approval).
Product capabilities are always referred to by their `C-` identifiers; registry entries are never used as a
stand-in for a product requirement. The mapping from capability to *evaluation family* (a fixture set, not a
product feature) is in the [roadmap §3](capability-roadmap.md#3-capability-to-validation-map).

## Rules for changing these documents

1. **Status may only move from `specified` to something else with evidence.** A capability becomes
   `validated` only when a named family passes its pre-registered thresholds with recorded runs; the word
   "implemented" is reserved for shipped product code that does not exist yet, and the test suite rejects it
   in the machine-readable index.
2. **Thresholds are pre-registered in M5.** Changing one after seeing a result requires a change-log note
   explaining what was learned — not an edit in place.
3. **No model, provider, parameter count or hardware** may be named in these documents before gate G-O1
   passes.
4. **The registry stays a harness artefact.** If a product capability and a registry entry share a name, the
   registry entry is still only a simulator.
5. **Owner decisions are recorded, not paraphrased away.** If a decision changes, it gets a new `O-` row and
   a change-log entry; earlier rows stay visible.
6. **Proposals are labelled.** A policy the owner has not decided is marked `proposed — awaiting owner
   review`, and the test suite requires that marking.
7. **Every change updates `capabilities.json` and the Markdown together**; `eval/tests/test_product_spec.py`
   fails if ids, statuses, milestones or the summary table drift apart.

## Verification

```bash
python3 -m unittest eval.tests.test_product_spec -v   # 35 spec/index consistency checks
python3 -m unittest discover -s eval/tests -t .       # full suite, including Stage 0/1 tests
```

The spec test enforces: fourteen capabilities with unique ids; every capability has a requirement, ≥2
acceptance criteria with a metric and measurement, dependencies, data classes, permission verbs, a
processing location and a planned milestone; every referenced family is either built (`B1`, `B2`, `B3`,
`B8`, `B9`, `B10`) or declared planned; no capability status is `implemented`; the local-processing boundary
exists and forbids cloud AI; G-O1 is blocked and blocks M15; the call policy is marked as a proposal; the
the Markdown summary table and the JSON agree; the device matrix covers all seven targets and marks none
verified; the holographic interface is separate from VR and both glasses targets; proposals stay marked as
proposals with no language claimed and G-O1 still blocked; and no model, vendor or accelerator is named.
