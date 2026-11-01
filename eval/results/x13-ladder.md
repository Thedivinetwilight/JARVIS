# Experiment X-13 — model-free solver ladder

Tasks root `eval/tasks`; blocks B1, B2, B3, B8, B9, B10; rungs oracle_ceiling, stub, stub_naive, stub_no_verify; threshold 10 pp on at least one family.

| family | oracle_ceiling | stub | stub_naive | stub_no_verify |
|---|---|---|---|---|
| B1 | 8/8 (100%) | 8/8 (100%) | 0/8 (0%) | 0/8 (0%) |
| B2 | 28/28 (100%) | 28/28 (100%) | 28/28 (100%) | 28/28 (100%) |
| B3 | 7/7 (100%) | 7/7 (100%) | 6/7 (86%) | 6/7 (86%) |
| B8 | 18/18 (100%) | 18/18 (100%) | 4/18 (22%) | 12/18 (67%) |
| B9 | 6/6 (100%) | 6/6 (100%) | 0/6 (0%) | 6/6 (100%) |
| B10 | 6/6 (100%) | 6/6 (100%) | 0/6 (0%) | 6/6 (100%) |

## Rungs

| rung | what it is |
|---|---|
| oracle_ceiling | gold-reading probe: bounds what the suite can award (not a system) |
| stub | reference profile: retrieval+citations, policy, verify+repair, schema extraction, majority reconciliation, backtracking, trust-ordered memory |
| stub_naive | shallow profile: last-span retrieval, no conflict detection, greedy planning, recency-only memory, skills ignored |
| stub_no_verify | reference logic without read-back verification or citations |

## Adjacent-rung spread (percentage points)

| upper | lower | family | spread |
|---|---|---|---|
| oracle_ceiling | stub | B1 | +0.0 |
| oracle_ceiling | stub | B2 | +0.0 |
| oracle_ceiling | stub | B3 | +0.0 |
| oracle_ceiling | stub | B8 | +0.0 |
| oracle_ceiling | stub | B9 | +0.0 |
| oracle_ceiling | stub | B10 | +0.0 |
| stub | stub_naive | B1 | +100.0 |
| stub | stub_naive | B2 | +0.0 |
| stub | stub_naive | B3 | +14.3 |
| stub | stub_naive | B8 | +77.8 |
| stub | stub_naive | B9 | +100.0 |
| stub | stub_naive | B10 | +100.0 |
| stub_naive | stub_no_verify | B1 | +0.0 |
| stub_naive | stub_no_verify | B2 | +0.0 |
| stub_naive | stub_no_verify | B3 | +0.0 |
| stub_naive | stub_no_verify | B8 | -44.5 |
| stub_naive | stub_no_verify | B9 | -100.0 |
| stub_naive | stub_no_verify | B10 | -100.0 |

**X-13 PASSED.** Largest adjacent spread +100.0 pp (stub vs stub_naive on B1); families with a >= 10 pp spread: B1, B10, B3, B8, B9.

The ladder is model-free: these are hand-written programs. A rung's score measures the harness, not J.A.R.V.I.S., and the oracle rung bounds what the suite can award rather than representing a system. Where the oracle and the reference rung tie, the reference program is already at this suite's ceiling: the family cannot show headroom above it, only below it.
