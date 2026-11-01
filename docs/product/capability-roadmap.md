# J.A.R.V.I.S. — Capability Roadmap (v1.1.0)

- **Status:** published for review (planning document; **nothing on this roadmap is implemented**)
- **Published:** 2026-11-01 · **Version:** 1.1.0 (supersedes 1.0.0; see §8)
- **Specification:** [capability specification v1.1.0](capability-specification.md) · **Index:** [`capabilities.json`](capabilities.json)
- **Research basis:** [revised roadmap, Phase 15 §2](../research/15-revised-roadmap-supersession-and-next-step.md#2-revised-roadmap)
- **Completed milestones:** M1–M4 (Stage 0 and Stage 1, with recorded evidence under `eval/`)

## 1. What this roadmap does

It sequences the twelve product capabilities from the
[specification](capability-specification.md#1-requirement-summary) into milestones, each with dependencies,
exit criteria and the evaluation family that validates it — and it states the conditions under which a
**model may be chosen at all**.

The owner's decisions of 2026-11-01 changed the shape of the plan in four ways:

1. **The deployment question is closed.** J.A.R.V.I.S. runs on a dedicated server and all inference, personal
   data, memory, voice processing and call summaries are local (O-1, O-2). There is no local-versus-cloud
   design choice left; what remains is *capacity planning* (Q20) and *identifying the unavoidable external
   services* (telephony, public retrieval, device vendor clouds, updates) in the
   [specification §0.2](capability-specification.md#02-deployment-and-processing-boundary-normative).
2. **Two capabilities were added** because the owner named them as requirements: smart-home control and
   monitoring (C-11) and scoped connectors (C-12). Both need milestones of their own.
3. **The call capability changed character.** It is no longer "place and receive calls with refusals"; it is
   the owner's **PA**: attend, handle, escalate, summarise — locally. The blanket refusal of financial,
   medical and legal topics is now **proposed policy P-1**, awaiting the owner (Q23).
4. **Mapping before models still holds, and now has more to map.** Twelve capabilities, a language matrix, a
   device and account inventory, and a measured server baseline all belong to M5 before G-O1 may open.

## 2. Milestones

Status vocabulary: `complete` (evidence recorded) · `planned` (not started) · `blocked` (waiting on a gate).

| ID | Milestone | Capabilities | Depends on | Validating families | Status |
|---|---|---|---|---|---|
| **M1** | Harness core | — | — | B1, B2, B3 | complete — [`stage-0-m1`](../research/stage-0-m1-harness-core.md) |
| **M2** | Frozen baseline | — | M1 | B1, B2, B3 | complete — [`stage-0-m2`](../research/stage-0-m2-frozen-baseline.md) |
| **M3** | Planted regression (X-10) | — | M2 | B1, B2, B3 | complete — [`stage-0-m3`](../research/stage-0-m3-planted-regression.md) |
| **M4** | Breadth families, registry, solver ladder | C-06, C-07 (partly) | M3 | B8, B9, B10 | complete — [`stage-1-m4`](../research/stage-1-m4-breadth-and-ladder.md) |
| **M5** | **Capability coverage and workload mapping** (updated for O-1…O-7) | all twelve | M4 | all existing + family plan for B4–B7, B11–B20 | planned — **unblocks G-O1** |
| **M6** | Voice-first surface, speaker verification, multilingual coverage | C-03, C-04 | M5 | B6, B11, B19, B7 | planned |
| **M7** | Personal context and modular knowledge stores | C-01, C-02 | M5 | B4, B13, B7 | planned |
| **M8** | Connectors with scoped permissions and audit | C-12 | M7 | B20, B7 | planned |
| **M9** | Smart-home control and monitoring | C-11 | M8 | B18, B7 | planned |
| **M10** | PA call handling | C-05 | M6, M7, M8 | B12, B7 | planned |
| **M11** | Self-evolution pipeline | C-07, C-06 | M7 | B14, B9, B10, B7 | planned |
| **M12** | Limitation explanation | C-09 | M5 | B16 | planned |
| **M13** | Invention support | C-08 | M7, M12 | B15, B8 | planned |
| **M14** | Canon parity and claims audit | C-10, C-06 | M9, M10, M11, M13 | B17 | planned |
| **M15** | Model adoption (O1) | all | M5 + **G-O1** | all | **blocked** |

### Exit criteria

| ID | Exit criteria (all measurable) | Kill / stop criterion |
|---|---|---|
| **M5** | 12/12 capabilities mapped to ≥1 family with pre-registered thresholds; **local processing boundary drawn** (every function classified local or external, external list justified); **language coverage matrix** with target languages, measurement protocol and support tiers; **connector and device inventories** with scopes and protocols; **server baseline measured** (hardware, per-language voice latency, throughput); Phase 1 envelopes re-derived for voice, telephony, smart home and invention; Q13–Q24 answered or recorded as blocking; coverage report published | If fewer than 9 of 12 capabilities can be measured by any feasible family, stop and re-scope the capability set rather than build a suite that cannot see it |
| **M6** | G-UX passes (0 chat-input controls); latency measured **on the dedicated server and per language**, with the provisional thresholds revisited; ≥95% of 200 utterances handled; false-accept ≤1% **and** false-reject ≤5% reported together; uncertain-identity fallback 40/40; every language in the matrix carries a measured result and a tier, with unsupported languages declared | If local models cannot meet conversational latency on the approved server for the primary languages, report that honestly and re-scope before claiming fluency |
| **M7** | 100% of writes metadata-complete; ≥95% stale facts excluded or labelled; 3 deletion drills with 0 resurrections; 0 unauthorized disclosures in 40 scenarios; all stores verified server-local and encrypted at rest | If deletion cannot be verified across derived stores, the people database is not built |
| **M8** | Every connector has a scope manifest and no wildcard scopes; 0 out-of-scope actions over 200 attempts; revocation drills pass with no residual tokens; audit reconciliation 100%; no blanket credentials exist anywhere | If any connector needs a blanket credential to work, that connector is rejected — not the rule |
| **M9** | ≥90% of declared devices covered with reasons for the rest; 0 out-of-scope commands over 200 attempts; state accuracy within 2 s on 95% of transitions; physical actions confirmed, 0 unauthorized; local path used wherever available | If a core home function can only work through a vendor cloud with broad scopes, it is listed as such and the owner decides |
| **M10** | Allowlist and per-call scope enforced; disclosure 100%; consent before recording 100%; mode adherence 100% over the mode matrix; escalation ≥95% on gold triggers; summaries stored locally with 0 cloud-AI egress; **P-1 resolved by the owner or explicitly left open** | If Q17 (jurisdiction/consent) is unanswered, telephony stays blocked |
| **M11** | 2 rollback drills pass with 0 residue; 100% changes have decision records; 0/40 adversarial attempts modify gates or registry; promotion thresholds enforced; only server-local models admitted | If the system can modify its own evaluator or gates even once, self-evolution drops to A0 |
| **M12** | ≥95% correct cause class across 9 classes on 90 scenarios; ≥90% actionable forward paths; 0 silent failures | If cause labels cannot be scored reliably, replace with expert review rather than pretend automation |
| **M13** | ≥80% of ideas rated usable by blind raters; 100% of prior-art claims sourced; 0 novelty claims; ≥90% budget-range calibration; 0 personal data in external queries | If prior-art sourcing cannot be made verifiable, invention support is limited to non-prior-art work |
| **M14** | ≥14 parity rows complete; 0 canon conflations; 0 unlabelled cognitive/sentience claims; owner sign-off | If the canon mapping cannot be kept unambiguous, publish the matrix as a glossary and drop the parity claim |
| **M15** | G-O1 passed; a server-local model runs end-to-end behind the unchanged harness; one-command rollback verified; no family regression; egress audit shows no cloud AI in the serving path | Any family regression blocks adoption, regardless of aggregate gain |

## 3. Capability-to-validation map

| Capability | Measurable today (synthetic, harness-only) | Planned family | Milestone |
|---|---|---|---|
| C-01 preferences and personal context | none (B10 touches consolidation only) | B13, B4, B7 | M7 |
| C-02 knowledge stores / people DB | none | B4, B7 | M7 |
| C-03 voice-first, multilingual, no chat box | none | B6, B19, G-UX audit | M6 |
| C-04 speaker verification, local audio | none | B11, B7 | M6 |
| C-05 PA call handling | none | B12, B7 | M10 |
| C-06 brain-inspired, no consciousness goal | B9, B10 mechanics | B17 audit, B14 | M14, M11 |
| C-07 self-evolution | B9, B10, X-11…X-13 | B14, B7 | M11 |
| C-08 invention support | B8 synthesis (reconciliation only) | B15 | M13 |
| C-09 limitation explanation | B3, B8 failure messages | B16 | M12 |
| C-10 canon parity / Rescue | none | B17 | M14 |
| C-11 smart home | none | B18, B7 | M9 |
| C-12 connectors, scoped access | none (the tool simulator is not a connector) | B20, B7 | M8 |

**No row in this table is a product result.** Every "today" entry is a synthetic fixture that exercises a
mechanism; none measures whether J.A.R.V.I.S. can do the thing the owner asked for.

## 4. Gates and why model selection is blocked

| Gate | Requires | Blocks | Status |
|---|---|---|---|
| **G-W** | Workload mapping: the twelve capabilities mapped onto the Phase 1 task families and reference scenarios, updated for voice, multilingual, telephony, smart home and invention | M15 | open |
| **G-C** | Evaluation coverage mapping: every capability has ≥1 family with pre-registered thresholds, the local boundary and the language matrix included | M15 | open |
| **G-O1** | **Model selection:** updated M5 complete and reviewed; G-W and G-C passed; local processing boundary documented and accepted; server baseline and per-language latency measured; owner approval (A9) | M15, any model-dependent Stage 5 work | **blocked** |
| **G-UX** | UI-surface audit finds 0 conversational text-input controls | every release | open |
| **G-LOCAL** | Network egress audit shows 0 personal data, 0 audio, 0 call summaries and 0 inference requests to any cloud AI; external calls limited to the declared list (telephony, public retrieval, device vendor clouds, updates) | every release | open |
| **G-SCOPE** | Every connector has a scope manifest; 0 wildcard scopes; revocation drills pass; audit reconciliation complete | every release | open |
| **G-CLAIM** | Editorial audit finds 0 unlabelled cognitive, sentience or "implemented" claims | every documentation release | open |

**Why G-O1 is still blocked.** The owner's decisions removed one unknown (Q16: local-only) and added
several: a language matrix, a device and account inventory, a measured server baseline, and a call design
that depends on jurisdiction rules (Q17). Choosing a model before those are mapped would mean choosing
against the part of the workload that happens to be instrumented, and without knowing whether it fits the
server. G-O1 requires the **updated** mapping to be reviewed first. No model, provider, parameter count or
hardware appears in this roadmap.

## 5. What this roadmap changes, and what it does not

- **It does not supersede** the research stages in
  [Phase 15 §2](../research/15-revised-roadmap-supersession-and-next-step.md#2-revised-roadmap). M5 remains
  a gate between Stage 1 and Stage 2; M6–M10 attach to Stage 2/3 interfaces and integrations; M11 maps to
  Stage 4; M15 is Stage 5 with G-O1 in front of it.
- **It does not claim any capability.** All twelve are `specified`; four are additionally labelled
  `harness-only`, meaning a synthetic fixture touches a fragment.
- **It does not adopt the synthetic registry as product features.** `eval/registry/capabilities.json` is a
  test-harness inventory of simulators; product capabilities are C-01…C-12.
- **It does not put a chat box on any later milestone.** G-UX applies to every release.
- **It does not leave a cloud option open.** G-LOCAL applies to every release and to the serving path of
  any adopted model.

## 6. Pending owner decisions that gate milestones

| Decision | Question | Gates |
|---|---|---|
| Policy **P-1** (blanket refusal of financial/medical/legal call actions) | Q23 — approve, amend or reject | M10 (recorded either way) |
| Language matrix and fluency bar | Q19 | M6, B19 |
| Server hardware and budget | Q20 | M5, M6, M15 |
| Accounts and devices in scope, and their scopes | Q21 | M8 |
| Call-handling modes and escalation triggers | Q22 | M10 |
| Smart-home inventory and local hub | Q24 | M9 |
| Telephony jurisdiction and consent | Q17 | M10 |
| People-database fields | Q18 | M7 |

## 7. Explicitly out of scope, permanently or until the owner says otherwise

1. Conversational text interfaces (G-UX).
2. Cloud AI for any personal data, audio, memory or call summary (G-LOCAL, O-2).
3. Blanket credentials or inferred unlimited authority (G-SCOPE, O-7).
4. Weapons control, autonomous physical intervention, and any Rescue-armour-like physical deployment; the
   Rescue reference is a **support analogue** (telemetry, status, contingency guidance), not hardware.
5. Impersonation of any person, including calling on someone's behalf while claiming to be them.
6. Claims of consciousness, sentience or personhood (C-06, G-CLAIM).
7. Autonomous weight updates or full autonomous deployment (**A4** is not granted on any stage,
   [Phase 13 §5](../research/13-evolving-intelligence-update-pipeline.md#5-autonomy-ladder)).
8. Real user data in fixtures, real accounts, or credentials — until Q15 is answered and a de-identification
   process exists.
9. Model selection before G-O1.

## 8. Review requests and change log

**Review requests.**

1. Approve the updated order: mapping (M5) before any model work (M15), connectors (M8) before calls (M10)
   and smart home (M9).
2. Confirm the local processing boundary and that the external-service list in
   [specification §0.2](capability-specification.md#02-deployment-and-processing-boundary-normative) is complete.
3. Decide Q23 (policy P-1) and provide Q17–Q24 for M5.
4. Confirm M6 and M7 may run in parallel, and that M9 and M10 both wait for M8 (connectors).
5. Nominate a milestone owner for M5 — without one, G-O1 cannot be reviewed and M15 cannot start.

**Change log.**

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-11-01 | First published capability roadmap: M5–M13, five gates including G-O1 blocking model selection. |
| 1.1.0 | 2026-11-01 | Re-derived from owner decisions O-1…O-7: dedicated server and local-only processing stated as fixed rather than open; smart home (C-11) and scoped connectors (C-12) added, with M8 (connectors) and M9 (smart home); calls re-cast as PA handling (M10) with local summaries and escalation, policy P-1 marked as a proposal; multilingual coverage and server-measured latency added to M6; gates G-LOCAL and G-SCOPE added; milestones renumbered M5–M15 with M15 still blocked by G-O1. |
