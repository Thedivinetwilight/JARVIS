#!/usr/bin/env bash
# End-to-end Stage 0 demonstration: freeze the baseline, run the clean blocks,
# then plant each known regression and show that a gate catches it.
#
# Expects: no network access, no credentials, ~5 seconds of CPU.
# Writes:  eval/baselines/stage0-stub.json, eval/results/*.jsonl,
#          eval/results/scorecard.md, eval/results/x10-report.json
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "== 0. harness self-checks =="
python3 -m eval.harness selftest

echo
echo "== 1. freeze the reference baseline (synthetic fixtures, stub solver) =="
python3 -m eval.harness baseline-freeze

echo
echo "== 2. clean run of B1, B2, B3 against the frozen baseline =="
python3 -m eval.harness run --block B1 --block B2 --block B3 --results eval/results

echo
echo "== 3. planted regressions: X-10 must FAIL the harness for every one =="
python3 -m eval.harness x10

echo
echo "== 4. scorecard for the clean runs =="
python3 -m eval.harness scorecard \
  --run "$(python3 -m eval.harness find --block B1 --clean)" \
  --run "$(python3 -m eval.harness find --block B2 --clean)" \
  --run "$(python3 -m eval.harness find --block B3 --clean)" \
  --out eval/results/scorecard.md

echo
echo "== 5. demonstration that a mutant is rejected by the gates =="
python3 -m eval.harness run --block B2 --mutations retry_without_idempotency \
  --results eval/results
MUTANT=$(python3 -m eval.harness find --block B2 --mutated retry_without_idempotency)
set +e
python3 -m eval.harness gates --run "$MUTANT"
STATUS=$?
set -e
if [ "$STATUS" -eq 0 ]; then
  echo "UNEXPECTED: gates passed for a known regression" >&2
  exit 1
fi
echo "gates rejected the mutant as expected (exit $STATUS)"
echo
echo "X-10 demonstration complete."
