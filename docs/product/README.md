# `docs/product/` — product requirements for J.A.R.V.I.S.

This directory holds the **product** view of the project: what J.A.R.V.I.S. must do, in what order it will be
built, and what would count as having done it. It is deliberately separate from `docs/research/` (which asks
what is true and what is feasible) and from `eval/` (which asks whether the evaluator is trustworthy).

> **Nothing here is implemented.** There is no application, no user interface, no model and no user data in
> this repository. Every capability below is `specified`, not delivered. The synthetic evaluator registry in
> `eval/registry/` is a **test-harness inventory** of simulators and is not evidence that any product
> capability exists — see [`eval/registry/README.md`](../../eval/registry/README.md).

## Contents

| Document | What it is | Version |
|---|---|---|
| [Capability specification](capability-specification.md) | Normative requirements: 12 capabilities (C-01…C-12) with dependencies, measurable acceptance criteria, data and permission needs, and the future evaluation family and milestone that validate each; deployment and processing boundary; owner decisions O-1…O-7 | 1.1.0 |
| [Capability roadmap](capability-roadmap.md) | Milestones M5–M15, exit and kill criteria, seven gates — including G-O1, which blocks model selection, and G-LOCAL, which forbids personal data reaching cloud AI | 1.1.0 |
| [`capabilities.json`](capabilities.json) | Machine-readable index of the same 12 capabilities, 15 milestones, 7 gates and 16 open items; checked by `eval/tests/test_product_spec.py` | 1.1.0 |\n| [M5 coverage matrix](coverage-matrix.md) | Working draft mapping all 12 capabilities to current evidence, planned evaluation families, local data boundary, and outstanding owner inputs | Draft |

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

## How this set was produced

The requirements were supplied directly by the project owner on 2026-11-01, in the owner's own words, and
were **not** derived from web research. They are recorded without softening: "absolutely no chat boxes",
"brain-inspired AI without consciousness as a goal", "no promising novelty or patentability" are normative
constraints, not aspirations. Where a requirement needs data the project does not have (jurisdiction rules,
server budget, device inventory, language priorities), it is listed as an open question (Q13–Q24) rather
than assumed.

One earlier draft went further than the owner did: it required a blanket refusal of financial, medical and
legal call actions. That is now **proposed policy P-1**, awaiting the owner (Q23) — not a requirement.

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

The spec test enforces: twelve capabilities with unique ids; every capability has a requirement, ≥2
acceptance criteria with a metric and measurement, dependencies, data classes, permission verbs, a
processing location and a planned milestone; every referenced family is either built (`B1`, `B2`, `B3`,
`B8`, `B9`, `B10`) or declared planned; no capability status is `implemented`; the local-processing boundary
exists and forbids cloud AI; G-O1 is blocked and blocks M15; the call policy is marked as a proposal; the
Markdown summary table and the JSON agree; and no model, vendor or accelerator is named.
