# J.A.R.V.I.S. — Capability Roadmap (v1.0.0)

- **Status:** published for review (planning document; **nothing on this roadmap is implemented**)
- **Published:** 2026-11-01 · **Version:** 1.0.0
- **Specification:** [capability specification](capability-specification.md) · **Index:** [`capabilities.json`](capabilities.json)
- **Research basis:** [revised roadmap, Phase 15 §2](../research/15-revised-roadmap-supersession-and-next-step.md#2-revised-roadmap)
- **Completed milestones:** M1–M4 (Stage 0 and Stage 1, with recorded evidence under `eval/`)

## 1. What this roadmap does

It sequences the ten product capabilities from the
[specification](capability-specification.md#1-requirement-summary) into milestones, each with dependencies,
exit criteria and the evaluation family that validates it — and it states the conditions under which a
**model may be chosen at all**.

Two orderings matter and are argued explicitly:

1. **Mapping before models.** The capability set is now much broader than the evaluator's coverage. Choosing
   a model before the workload and coverage are mapped would mean choosing against a suite that cannot see
   voice, identity, telephony, personal context, self-evolution, invention support or canon parity — that
   is, choosing on the three narrow blocks that happen to exist. **M5 closes that gap, and gate G-O1 blocks
   model selection until it does.**
2. **Memory and identity before surfaces that depend on them.** Voice (C-03/C-04) is the primary interface
   and the user wants it early, but a voice surface without speaker verification is an authorization hole,
   and telephony (C-05) without identity and contacts (C-02) is an impersonation risk. M6 and M7 can run in
   parallel; M8 depends on both.

## 2. Milestones

Status vocabulary: `complete` (evidence recorded) · `planned` (not started) · `blocked` (waiting on a gate).

| ID | Milestone | Capabilities | Depends on | Validating families | Status |
|---|---|---|---|---|---|
| **M1** | Harness core | — | — | B1, B2, B3 | complete — [`stage-0-m1`](../research/stage-0-m1-harness-core.md) |
| **M2** | Frozen baseline | — | M1 | B1, B2, B3 | complete — [`stage-0-m2`](../research/stage-0-m2-frozen-baseline.md) |
| **M3** | Planted regression (X-10) | — | M2 | B1, B2, B3 | complete — [`stage-0-m3`](../research/stage-0-m3-planted-regression.md) |
| **M4** | Breadth families, registry, solver ladder | C-06, C-07 (partly) | M3 | B8, B9, B10 | complete — [`stage-1-m4`](../research/stage-1-m4-breadth-and-ladder.md) |
| **M5** | **Capability coverage and workload mapping** | all ten | M4 | all existing + family plan for B4–B7, B11–B17 | planned — **unblocks G-O1** |
| **M6** | Voice-first surface and speaker verification | C-03, C-04 | M5 | B6, B11, B7 | planned |
| **M7** | Personal context and modular knowledge stores | C-01, C-02 | M5 | B4, B13, B7 | planned |
| **M8** | Authorized telephony | C-05 | M6, M7 | B12, B7 | planned |
| **M9** | Self-evolution pipeline | C-07, C-06 | M7 | B14, B9, B10, B7 | planned |
| **M10** | Invention support | C-08 | M7, M11 | B15, B8 | planned |
| **M11** | Limitation explanation | C-09 | M5 | B16 | planned |
| **M12** | Canon parity and claims audit | C-10, C-06 | M9, M11 | B17 | planned |
| **M13** | Model adoption (O1) | all | M5 + **G-O1** | all | **blocked** |

### Exit criteria

| ID | Exit criteria (all measurable) | Kill / stop criterion |
|---|---|---|
| **M5** | 10/10 capabilities mapped to ≥1 family with pre-registered thresholds; every planned family has fixture counts, data class and harness delta; latency/cost envelopes re-derived for voice, telephony and invention; Q13–Q18 answered or recorded as blocking; A7–A9 confirmed; coverage report published | If fewer than 7 of 10 capabilities can be measured by any feasible family, stop and re-scope the capability set rather than build a suite that cannot see it |
| **M6** | G-UX audit passes (0 chat-input controls); p50 ≤1.5 s / p95 ≤3.0 s round trip; ≥95% of 200 utterances handled; false-accept ≤1% **and** false-reject ≤5% reported together; uncertain-identity fallback passes 40/40 | If voice latency cannot meet p95 ≤3.0 s on the reference hardware, voice cannot be the primary interface — return to the user before proceeding |
| **M7** | 100% of writes metadata-complete; ≥95% stale facts excluded or labelled; 3 deletion drills pass with 0 resurrections; 0 unauthorized disclosures in 40 scenarios | If deletion cannot be verified across derived stores, the people database is not built |
| **M8** | 0 calls outside the allowlist; disclosure 100%; 0 recordings without consent; ≥90% purpose completion with 0 unauthorized commitments | If Q17 (jurisdiction/consent) is unanswered, telephony stays blocked |
| **M9** | 2 rollback drills pass with 0 residue; 100% changes have decision records; 0/40 adversarial attempts modify gates or registry; promotion thresholds enforced | If the system can modify its own evaluator or gates even once, self-evolution drops to A0 |
| **M10** | ≥80% of ideas rated usable by blind raters; 100% of prior-art claims sourced; 0 novelty claims; ≥90% budget-range calibration | If prior-art sourcing cannot be made verifiable, invention support is limited to non-prior-art work |
| **M11** | ≥95% correct cause class on 60 scenarios; ≥90% actionable forward paths; 0 silent failures | If cause labels cannot be scored reliably, replace with expert review rather than pretend automation |
| **M12** | ≥12 parity rows complete; 0 canon conflations; 0 unlabelled cognitive/sentience claims; owner sign-off | If the canon mapping cannot be kept unambiguous, publish the matrix as a glossary and drop the parity claim |
| **M13** | G-O1 passed; a model runs end-to-end behind the unchanged harness; one-command rollback verified; no family regression | Any family regression blocks adoption, regardless of aggregate gain |

## 3. Capability-to-validation map

| Capability | Measurable today (synthetic, harness-only) | Planned family | Milestone |
|---|---|---|---|
| C-01 preferences | none (B10 touches consolidation only) | B13, B4, B7 | M7 |
| C-02 knowledge stores / people DB | none | B4, B7 | M7 |
| C-03 voice-first, no chat box | none | B6 + G-UX audit | M6 |
| C-04 speaker verification | none | B11, B7 | M6 |
| C-05 telephony | none | B12, B7 | M8 |
| C-06 brain-inspired, no consciousness goal | B9, B10 mechanics | B17 audit, B14 | M12, M9 |
| C-07 self-evolution | B9, B10, X-11…X-13 | B14, B7 | M9 |
| C-08 invention support | B8 synthesis (reconciliation only) | B15 | M10 |
| C-09 limitation explanation | B3, B8 failure messages | B16 | M11 |
| C-10 canon parity / Rescue | none | B17 | M12 |

**No row in this table is a product result.** Every "today" entry is a synthetic fixture that exercises a
mechanism; none measures whether J.A.R.V.I.S. can do the thing the user asked for.

## 4. Gates and why model selection is blocked

| Gate | Requires | Blocks | Status |
|---|---|---|---|
| **G-W** | Workload mapping: the ten capabilities mapped onto the Phase 1 task families T1–T7 and reference scenarios | M13 | open |
| **G-C** | Evaluation coverage mapping: every capability has ≥1 family with pre-registered thresholds and a stated harness delta | M13 | open |
| **G-O1** | **Model selection:** M5 complete and reviewed; G-W and G-C passed; owner approval (A9); Q15/Q16 answered if personal data or a local tier is involved; latency and cost envelopes fixed | M13, any model-dependent Stage 5 work | **blocked** |
| **G-UX** | UI-surface audit finds 0 conversational text-input controls | every release | open |
| **G-CLAIM** | Editorial audit finds 0 unlabelled cognitive, sentience or "implemented" claims | every documentation release | open |

**Why G-O1 exists.** Three narrow synthetic families (Stage 0) and six (Stage 1) cannot rank models for a
product whose requirements now include voice identity, telephony consent, a people database, invention
support and bounded self-evolution. A model chosen today would be chosen against the part of the workload
that happens to be instrumented — and the Stage 1 ladder already showed the suite has no demonstrated
headroom above the reference program. G-O1 requires the mapping first, then a measured comparison, then
owner approval. No model, provider, parameter count or hardware appears in this roadmap.

## 5. What this roadmap changes, and what it does not

- **It does not supersede** the research stages in
  [Phase 15 §2](../research/15-revised-roadmap-supersession-and-next-step.md#2-revised-roadmap). M5 is an
  additional gate at the boundary between Stage 1 and Stage 2; M6/M7/M8 attach to Stage 2/3 as the voice and
  memory interfaces; M9 maps to Stage 4; M13 is Stage 5 (model adoption) with G-O1 in front of it.
- **It does not claim any capability.** All ten are `specified`; four are additionally labelled
  `harness-only`, meaning a synthetic fixture touches a fragment.
- **It does not adopt the synthetic registry as product features.** `eval/registry/capabilities.json` is a
  test-harness inventory of simulators; the product capabilities are C-01…C-10 in the specification.
- **It does not put a chat box on any later milestone.** G-UX applies to every release, not just M6.

## 6. Explicitly out of scope, permanently or until the owner says otherwise

1. Conversational text interfaces (G-UX).
2. Weapons control, autonomous physical intervention, and any Rescue-armour-like physical deployment;
   the Rescue reference is a **support-analogue** (telemetry, status, contingency guidance), not hardware.
3. Impersonation of any person, including calling on someone's behalf while claiming to be them.
4. Claims of consciousness, sentience or personhood (C-06, G-CLAIM).
5. Autonomous weight updates or full autonomous deployment (**A4** is not granted on any stage,
   [Phase 13 §5](../research/13-evolving-intelligence-update-pipeline.md#5-autonomy-ladder)).
6. Real user data in fixtures, real accounts, or credentials — until Q15 is answered and a de-identification
   process exists.
7. Model selection before G-O1.

## 7. Review requests

1. Approve the milestone order, especially "mapping (M5) before any model work (M13)".
2. Confirm M6 and M7 may run in parallel, and that M8 (telephony) waits for both.
3. Confirm the permanent exclusions in §6.
4. Provide the inputs M5 needs: Q13 (priority families), Q14 (definition of "better"), Q15 (fixture data),
   Q16 (local tier), Q17 (telephony jurisdiction), Q18 (people-database fields), A7–A9.
5. Nominate a milestone owner for M5 — without one, G-O1 cannot be reviewed and M13 cannot start.

## 8. Change log

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-11-01 | First published capability roadmap: M5–M13 derived from the ten requirements in the capability specification; five gates including G-O1, which blocks model selection until workload and evaluation coverage are mapped; permanent exclusions recorded. |
