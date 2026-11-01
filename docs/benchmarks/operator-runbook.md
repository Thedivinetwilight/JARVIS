# Operator runbook — running the benchmark on the owner's dedicated server

For the operator who has **the owner's designated server**, **a local speech adapter with its
model**, and **a licensed English audio pack**. If any of the three is missing, stop here: a run
without them cannot produce evidence (`docs/benchmarks/benchmark-status.md`).

Nothing in this runbook downloads a model, contacts a cloud service, or selects a model, provider,
server or language tier. Every path is named explicitly by you.

## 0. Preconditions, checked before anything else

```bash
# on the server, from the repository root
python3 -V                              # 3.11+ ; the benchmark is standard-library only
git log --oneline -1                    # expect 21515d8 (app.bench 0.1.0)
df -h .                                 # room for fixtures + logs + results (tens of MB)
ls -l /path/to/asr_adapter.py /path/to/model /path/to/english-pack/pack.json
```

If the adapter lives in a local inference **daemon** rather than in-process, plan to add
`--allow-loopback-ipc` and accept that it is recorded as a deviation in `network.loopback_allowed`.
Everything else stays denied.

## 1. Verify the tool on that host (expected: all green)

```bash
PYTHONPATH="$PWD" python3 -m unittest app.tests.test_bench_units    # expect: Ran 64 tests ... OK
PYTHONPATH="$PWD" python3 -m unittest app.tests.test_bench_run      # expect: Ran 23 tests ... OK
PYTHONPATH="$PWD" python3 -m unittest app.tests.test_boundaries     # expect: Ran 14 tests ... OK
```

If these fail, stop. The numbers in step 4 would be untrustworthy for a reason unrelated to speech.

## 2. Pre-register the plan (no adapter is loaded, nothing is measured)

Use the **exact flags** you will use in step 4. `--plan-only` only hashes what it is given.

```bash
PYTHONPATH="$PWD" python3 -m app.bench --plan-only \
  --asr-adapter /path/to/asr_adapter.py --asr-model /path/to/model \
  --tts-adapter /path/to/tts_adapter.py --tts-model /path/to/tts-model \
  --fixtures /path/to/english-pack \
  --languages en --repeat 25 --warmup 1 \
  --host-role dedicated_server \
  --out plan-prepared

python3 -c "import json;d=json.load(open('plan-prepared/plan.json'));\
print(d['plan_hash']);print(d['task_count'],'tasks');\
print({k:v['sha256'] for k,v in d['artifacts']['adapters'].items()})"
```

Record the `plan_hash` in the run log **before** measuring. This is the prepared plan.

Notes that will save a failed run:

- A `--fixtures` directory without a readable `pack.json` is refused with exit code 2 and nothing
  runs. The pack must be the licensed one, not a placeholder.
- `--plan-only` records a missing adapter or model as `exists: false` and still writes a plan. The
  real run refuses them with exit code 6, so check the hashes printed above are not `null`.
- A `plan_hash` excludes timestamps and the output directory by design. If it changes between two
  runs of the same flags, an input changed — see step 5.

## 3. Optional one-item smoke run (wiring only, **not evidence**)

```bash
PYTHONPATH="$PWD" python3 -m app.bench \
  --asr-adapter /path/to/asr_adapter.py --asr-model /path/to/model \
  --repeat 1 --warmup 0 --out smoke
```

Expect exit 0 and a `result.json` whose percentiles are `null` with
`not_measured: "insufficient_samples"`. That is correct: one sample is not a percentile. If exit is
5, the adapter attempted network access — resolve that before going further; do not add
`--allow-loopback-ipc` unless a local daemon genuinely needs it.

## 4. The measured run

```bash
PYTHONPATH="$PWD" python3 -m app.bench \
  --asr-adapter /path/to/asr_adapter.py --asr-model /path/to/model \
  --tts-adapter /path/to/tts_adapter.py --tts-model /path/to/tts-model \
  --fixtures /path/to/english-pack \
  --languages en --repeat 25 --warmup 1 \
  --host-role dedicated_server \
  --criteria /path/to/owner-criteria.json \
  --out run-001
```

- `--repeat 25 --warmup 1` is a **starting point**, not a bar: a percentile is reported only at
  `n ≥ 20` per cell, and a cell is per phase × language × condition, so 25 measured rounds of a
  20-item pack gives 500 samples per phase. The methodology default is `--repeat 3`; choose with the
  gates in `docs/benchmarks/voice-workload-benchmark.md` §6 in front of you.
- `--criteria` is **the owner's file**, never one from this repository. Without it the decision is
  `not_decidable` with reason *"no criteria file was supplied"*, which is the correct outcome when
  nobody has written a bar. `app/bench/criteria.example.json` is a format example only.
- Leave `--include-hostname` off unless the owner asks for it; the result then carries `host_id`
  only.

Expect one of the documented exit codes: `0` (result written), `2` (bad argument or file, nothing
measured), `3` (result failed its own validation and was **not** written), `4` (nothing measured),
`5` (network attempt: result written as `violated`, decision `rejected`), `6` (missing adapter or
model path).

## 5. Check the run against the prepared plan

```bash
python3 - <<'PY'
import json
prepared = json.load(open("plan-prepared/plan.json"))
actual   = json.load(open("run-001/plan.json"))
print("prepared:", prepared["plan_hash"])
print("actual  :", actual["plan_hash"])
print("MATCH" if prepared["plan_hash"] == actual["plan_hash"] else "MISMATCH")
PY
```

**If it matches:** the run measured the plan you pre-registered. Note both hashes in the log.

**If it does not match, stop and diff before trusting the numbers.** The hash covers the seed, the
repeat and warm-up counts, the scheduled languages, the loopback flag, the adapter and model hashes,
the fixture hashes and the plan structure. It deliberately excludes timestamps and the output
directory, so those cannot cause a mismatch. Check, in order:

1. adapter or model bytes changed (`artifacts.*.sha256` in both plans);
2. fixture files changed (`fixtures.synthetic[*].sha256`, `fixtures.licensed[*].sha256`);
3. different `--repeat`, `--warmup`, `--seed`, `--languages`, `--fixtures` or `--allow-loopback-ipc`;
4. a different `app.bench` version (`bench_version` in both plans);
5. a different repository commit — `git log --oneline -1` in both runs' `identity.git.commit`.

## 6. What to send back

Return these files, and only these:

| File | Why |
| --- | --- |
| `run-001/result.json` | the machine-readable result (`jarvis.voice-bench/1`) |
| `run-001/report.md` | the same result as tables, for the owner |
| `run-001/plan.json` | the plan and the exact command (`identity.reproduce_command`) |
| `plan-prepared/plan.json` | the pre-registered plan, for the hash comparison in step 5 |
| `run-001/logs/*.log` | adapter output; empty is normal and is itself recorded |
| `sha256sums.txt` | `sha256sum` of the six files above, so the transfer can be checked |
| the criteria file that was used | a verdict is meaningless without the bar it was measured against |
| your run log (step 2 hash, step 5 comparison, exit code, wall-clock) | reproduces the operator's reasoning |

**Do not send:** audio files, model weights, tokens, pairing codes, or a `--include-hostname`
result unless the owner asked for one. `result.json` is validated to contain no absolute personal
paths and no credential-shaped strings, but a quick `grep -c '/home/' run-001/result.json` costs
nothing (expect `0`).

## 7. How the result will be read

- `host_role: dedicated_server` **and** `comparable_to_owner_server: true` — the run can be compared
  with other dedicated-server runs; this is the only class of run that can support a decision.
- `decision: not_decidable` is normal on a first run: no criteria file, a language below the sample
  gate, or a missing metric all produce it, and each reason is listed.
- `decision: rejected` means a bar was breached or the adapter attempted network access. That is a
  finding, not a failure of the run.
- `decision: eligible_for_owner_review` means the run cleared the owner's bar; it does **not** mean
  anything was selected. Gate G-O1 stays blocked until the M5 workload review, G-W and G-C, the
  local-first boundary acceptance, the dedicated-server baseline and approval (A9) are all
  satisfied — the result document lists these five in
  `decision.outstanding_before_voice_implementation`.

Q19 (languages), Q20 (server) and G-O1 remain open regardless of what any run reports. Voice
processing stays unavailable in the J.A.R.V.I.S. application until the owner closes those items.
