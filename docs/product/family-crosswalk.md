# B4–B7 Family Naming Crosswalk (M5)

- **Status:** resolution note for the naming conflict recorded in [`coverage-matrix.md`](coverage-matrix.md) §6. It adds a mapping; it does not edit the historical documents it maps.
- **Rule:** supersession by name only. The earlier evidence stays valid *as evidence of what it measured*, under the old label. No counts, hashes or results are re-attributed.
- **Authoritative numbering:** [`08-evaluation-and-improvement.md`](../research/08-evaluation-and-improvement.md) (Phase 8), which is the version the product specification v1.1.0 and the M5 build plan use.

## 1. The conflict

Two numbering schemes exist in the repository:

| Source | B4 | B5 | B6 | B7 |
|---|---|---|---|---|
| `docs/research/08-evaluation-and-improvement.md` (Phase 8) | Memory | Multimodal | Voice | Adversarial |
| `docs/research/stage-0-m2-frozen-baseline.md`, `docs/research/stage-0-m3-planted-regression.md` | Voice | Memory | Latency/cost sweep | Long-horizon |

Both were written by the same project and both are internally consistent. The conflict is a naming collision, not a contradiction in findings: the Stage-0 documents measured what their B-numbers said they measured.

## 2. Supersession register

Format follows the Phase-8 register in [`15-revised-roadmap-supersession-and-next-step.md`](../research/15-revised-roadmap-supersession-and-next-step.md). Status vocabulary: `retained`, `amended`, `superseded`.

| ID | Original location | Original decision | Status | Replacement | Why |
|---|---|---|---|---|---|
| M5-B-1 | Phase 8, §5 | B4 = memory stores (working, episodic, profile, document, procedural), 50 tasks + 6 scenarios | retained | B4 (product backlog) | Chosen as authoritative; the product spec's C-01/C-02 need store-level evidence and this block is the richer definition |
| M5-B-2 | Phase 8, §5 | B5 = multimodal (4 document classes, screenshots, 5 videos), 30 tasks | retained | B5 (marked supporting) | Kept, but no acceptance criterion names it; retained as a supporting suite for C-02 and C-08 pending M5 review |
| M5-B-3 | Phase 8, §5 | B6 = voice (clean/noisy, with/without tools), 20 sessions | retained | B6 (product backlog) | Directly serves C-03; latency now measured inside B6 rather than as a separate family |
| M5-B-4 | Phase 8, §5 | B7 = adversarial (injection, poisoned memory, approval bypass), 40 tasks | retained | B7 (product backlog) | Broadened to also carry scope escalation, disclosure and physical-action confirmation for C-02/C-07/C-11/C-12 |
| M5-B-5 | Stage-0 M2 frozen baseline, ~L92–93 | B4 = voice | superseded | B6 (name only: identical measurement intent, new number) | Stage-0 evidence is unchanged and is cited as B6-era evidence |
| M5-B-6 | Stage-0 M2/M3 notes | B5 = memory | superseded | B4 (name only: identical measurement intent, new number) | Stage-0 memory observations remain valid as memory observations |
| M5-B-7 | Stage-0 M3 planted regression, L108 | B6 = latency/cost sweep | superseded | absorbed into B6 (response latency) and B19 (per-language latency) | A standalone sweep family has no product acceptance criterion; latency is now a property of the voice and language suites plus the M5 envelope re-derivation |
| M5-B-8 | Stage-0 M2/M3 notes | B7 = long-horizon, "not implemented" | amended | B7 = adversarial; long-horizon becomes a named future suite | The old B7 never had a task set. Long-horizon behaviour is a real requirement but not an acceptance criterion today, so it is preserved as a named future suite instead of silently disappearing |

## 3. What did not change

- No Stage-0 or Stage-1 run, hash, baseline or result file was edited, renamed or re-scored.
- The historical documents keep their original wording; this register is the only place the mapping is stated.
- B1, B2, B3, B8, B9 and B10 keep their meanings and their committed task sets.
- The registry remains a 13-entry harness inventory; nothing here re-labels a simulator as a product connector or device.

## 4. Consequence for M5

The conflict recorded as "open" in `coverage-matrix.md` §6 is resolved by name. The remaining M5 work is unchanged: owner inputs, pre-registration of task sets, envelope re-derivation and review. G-O1 stays blocked.

**Falsification test:** if any document still uses B4/B5/B6/B7 in the Stage-0 sense after this crosswalk without pointing to it, the crosswalk has failed and should be reopened rather than re-worded.
