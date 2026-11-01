# Benchmark status — what is verified, what is measured, what is missing

Status as of branch `arena/bbffddd4-jarvis`, commit `21515d8` (benchmark `app.bench 0.1.0`,
result schema `jarvis.voice-bench/1`).

Three claims are kept separate on purpose. They are not degrees of the same thing, and the third
cannot be inferred from the first two:

| # | Claim | State | Evidence |
| --- | --- | --- | --- |
| 1 | **Tool verified** — the command, the sandbox, the sample gates, the result validator, the decision protocol and every refusal path behave as documented | **done** | `app/tests/test_bench_units.py` (64 tests), `app/tests/test_bench_run.py` (23 tests), 241 app tests and 456 evaluator tests green |
| 2 | **Development-host plumbing measured** — the pipeline runs end to end on a hosted development sandbox with test doubles: real subprocesses, real timing, real peak RSS, real denial counters | **done, and explicitly not speech evidence** | doubles run on this sandbox: `completed`, 63 samples, 0 failures, peak worker RSS 28.6 MB, `network.denied = true`, 0 attempts, WER `not_measured` |
| 3 | **Owner-server speech measurements** — a real local speech adapter and a licensed English audio pack, measured on the owner's designated dedicated server | **NOT OBTAINED** | none of the three required inputs exists in this environment (see §2) |

> Claim 2 is **not** claim 3. A test-double run is plumbing. It is never eligible evidence, it can
> never be cited as a measurement of a speech system, and the result document it produces says so in
> its own `not_measured` block and `claims` flags. The tool marks such a run
> `host_role: development`, `comparable_to_owner_server: false`, `decision_eligible: false`.

## 1. Why no real measurement was taken

The three inputs the benchmark requires were checked for before any run, not assumed:

| Input | Found? | Where it was looked for |
| --- | --- | --- |
| The owner's designated dedicated server | **no** — this is a hosted development sandbox (`e2b.local`, 2 vCPU, 3.9 GB RAM, Debian 12, Python 3.11.2, no accelerator attribute, no thermal zone, no power meter) | host identity, CPU/memory, `/proc/driver`, `/sys/class/thermal`, `/sys/class/power_supply`, `/sys/class/drm` |
| An operator-supplied local speech adapter (and its model) | **no** | whole workspace: no `*adapter*` outside `app/bench/` and `app/tests/bench_adapters/` (test doubles only); no model artifacts (`*.onnx`, `*.gguf`, `*.safetensors`, `*.pt`, `*.bin`) |
| A licensed English audio pack | **no** | whole workspace: no `pack.json`, no `*.wav`/`*.flac`/`*.mp3`/`*.opus`; `/srv`, `/opt` and `/data` empty |

Because at least one was missing, the benchmark was **not** run against anything real. Nothing was
downloaded, no cloud service was contacted, and no model, provider or server was chosen. A
simulated or invented result would have been the one thing this tool exists to prevent.

## 2. What was actually executed here

Both items below ran on the hosted sandbox and produced scratch output under `/tmp`, which is
**deliberately not committed**: a result document that is not a real measurement must not sit in the
repository looking like one. Both are reproducible in seconds with the commands shown.

| Run | Command (abbreviated) | Outcome |
| --- | --- | --- |
| Plan only, inputs absent | `python3 -m app.bench --plan-only --asr-adapter /srv/owner/asr_adapter.py --asr-model /srv/owner/model --languages en` | exit 0, 36 tasks planned, adapter/model recorded `exists: false`, `plan_hash` `b5aaf44d…` — planning works without the inputs, which is how the owner can pre-register a plan |
| Plan only, pack missing | same plus `--fixtures /srv/owner/english-pack` | exit **2**, `error: no pack.json in /srv/owner/english-pack` — a fixture pack must exist; nothing ran |
| Doubles run (plumbing) | `python3 -m app.bench --asr-adapter app/tests/bench_adapters/asr_fixed.py --tts-adapter app/tests/bench_adapters/tts_signal.py --repeat 3 --warmup 1` | exit 0, `completed`, 63 samples (asr 27, tts 9, e2e 27), 0 failures, peak worker RSS 28.6 MB, `host_id host_26c69b1d4051`, `not_decidable`, `decision_eligible: false` |

The doubles' latency figures (sub-millisecond adapter calls, real-time factors in the tens of
thousands) are a property of a fixed-output stub, not of any recogniser. They are recorded here as
proof that the harness measures and reports, and for no other purpose.

## 3. Not eligible evidence — a list to check any future claim against

Never cite the following as a measurement of a speech system:

- any run using a file from `app/tests/bench_adapters/` (fixed-output doubles);
- any run on this sandbox (`host_id host_26c69b1d4051`) or any other development host — the tool
  labels such runs `comparable_to_owner_server: false`;
- any `--plan-only` output — it contains no measurement at all;
- any run whose `host_role` the operator set to `dedicated_server` on a machine that is not the
  owner's server (the role is an operator declaration, recorded as
  `host_role_source: "operator_declaration"`, and the tool cannot verify it);
- any run that ended `violated` (a denied network attempt), `failed` (nothing measured) or
  `incomplete` (some items failed).

## 4. What is still needed, and from whom

| Item | Owner of the input | Blocks |
| --- | --- | --- |
| The designated dedicated server, with the benchmark's host facts recorded | owner (Q20 open) | claim 3, C-03 latency thresholds, G-O1 |
| A local speech adapter file implementing the contract, plus its local model path | owner / vendor evaluation, supplied explicitly — never chosen by this tool | claim 3 |
| A licensed English audio pack (`pack.json`, ≥ 20 items per cell that needs a percentile) | owner (licensing decision) | WER, entity accuracy, task success |
| A written criteria bar (`criteria` JSON: the owner's own numbers and why) | owner | any verdict other than `not_decidable` |
| The M5 workload review, G-W and G-C evidence, acceptance of the local-first boundary, approval A9 | owner | G-O1, and therefore any voice implementation |

Q19 (languages), Q20 (server), G-O1 (model/accelerator/purchase) and A9 (approval) all remain open.
Nothing in this repository, and nothing in this status page, moves them.

## 5. Reproduce the verification (claim 1), not a measurement

```bash
PYTHONPATH=/home/user/JARVIS python3 -m unittest app.tests.test_bench_units   # 64 tests
PYTHONPATH=/home/user/JARVIS python3 -m unittest app.tests.test_bench_run     # 23 tests
PYTHONPATH=/home/user/JARVIS python3 -m unittest app.tests.test_boundaries    # 14 tests
```

## 6. How to change this page

Claim 3 changes **only** when a run made with the owner's adapter and licensed pack, on the owner's
server, produces a `result.json` whose `host_role` is `dedicated_server`, whose `run.status` is
`completed`, and whose `decision` is `rejected`, `not_decidable` or `eligible_for_owner_review` with
its reasons attached. Even then, update claim 3 with the run id, the plan hash, the commit and the
file locations — and leave Q19, Q20, G-O1 and A9 open unless the owner separately closes them.

Procedure for producing that run: `docs/benchmarks/operator-runbook.md`.
Methodology and its change control: `docs/benchmarks/voice-workload-benchmark.md`.
