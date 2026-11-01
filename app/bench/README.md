# `app/bench` — local voice workload benchmark

A **measurement tool**, not a capability. It runs an operator-supplied speech adapter on a fixed
workload on this machine, records what it observed, and refuses to say anything it did not observe.

**Measurement status at commit `21515d8`:** the tool is verified (241 app tests, 456 evaluator tests
green); the pipeline has been run end to end on a **development sandbox with test doubles**, which
the result labels `host_role: development`, `comparable_to_owner_server: false`,
`decision_eligible: false`; **no owner-server speech measurement exists**, because no operator
adapter, model or licensed pack is present in this environment. See
`docs/benchmarks/benchmark-status.md`, and `docs/benchmarks/operator-runbook.md` for the procedure on
the owner's server.

What it will not do, by construction:

- **select** a model, a provider, an accelerator, a server or a language tier (gate **G-O1** stays
  blocked; Q19 and Q20 stay open);
- **download** anything, or fall back to a default model — an adapter path and a model path must be
  named on the command line;
- **reach the network**, including loopback, unless the operator explicitly asks for loopback-only
  IPC and accepts that it is recorded as a deviation;
- **enable voice processing in the J.A.R.V.I.S. application** — voice input and output stay
  unavailable in the server (`/v1/status`, `/v1/limitations`), and nothing here changes that.

## 1. Run it

```bash
# 1. Write the plan only. Nothing is loaded, nothing is measured, no adapter is needed.
python3 -m app.bench --plan-only \
    --asr-adapter /path/to/local/asr_adapter.py \
    --languages en --out bench-out

# 2. A real run on this machine: latency, throughput, memory, failures.
python3 -m app.bench \
    --asr-adapter /path/to/local/asr_adapter.py \
    --asr-model /path/to/local/asr-model.bin \
    --tts-adapter /path/to/local/tts_adapter.py \
    --tts-model /path/to/local/tts-model-dir \
    --repeat 3 --warmup 1 \
    --out bench-out

# 3. A run that measures word error rate: needs a *licensed* fixture pack (see §3).
python3 -m app.bench \
    --asr-adapter /path/to/local/asr_adapter.py \
    --fixtures /path/to/licensed-pack \
    --languages en,hi \
    --repeat 25 --warmup 1 \
    --host-role development \
    --out bench-out
```

`bench-out/` then contains:

| Path | Content |
| --- | --- |
| `plan.json` | the fixed plan: task list shape, fixture hashes, adapter/model hashes, the exact command, the plan hash |
| `fixtures/` | the generated synthetic WAV files (deterministic for a given seed) |
| `logs/asr.log`, `logs/tts.log`, `logs/turn.log` | the adapters' own output, kept out of the protocol stream |
| `result.json` | the machine-readable result (`jarvis.voice-bench/1`) |
| `report.md` | the same result as prose and tables |

Every option:

| Flag | Meaning |
| --- | --- |
| `--asr-adapter PATH` | local file implementing the recognition contract; no default |
| `--tts-adapter PATH` | local file implementing the synthesis contract; optional |
| `--turn-adapter PATH` | optional local turn component; without it the end-to-end phase states that recognised text went straight to synthesis |
| `--asr-model PATH`, `--tts-model PATH` | local model file or directory; nothing is downloaded |
| `--asr-model-sha256`, `--tts-model-sha256` | expected hash; a mismatch is fatal, not a warning |
| `--fixtures DIR` | licensed fixture pack (see §3) |
| `--languages CODES` | comma-separated codes from the PR-1 proposal; **nothing is scheduled by default** |
| `--repeat N` | measured rounds per item, 1–50, default 3 |
| `--warmup N` | warm-up rounds per item, discarded before measurement, 0–20, default 1 |
| `--seed N` | fixture and bootstrap seed, default 20261101 |
| `--host-role development\|dedicated_server` | where this run happens; only the owner's dedicated server is decision-relevant (Q20 open) |
| `--timeout S` | per-item deadline, default 60 s |
| `--allow-loopback-ipc` | permit loopback connections for a local inference daemon; recorded as a deviation |
| `--no-hash-models` | skip hashing a very large model directory; recorded as `hash_mode: not_computed` |
| `--include-hostname` | record the host name; off by default |
| `--host-facts PATH` | operator-declared facts for a host that cannot be probed |
| `--criteria PATH` | pre-registered bar for the decision (§6) |
| `--out DIR` | output directory, default `bench-out` |
| `--plan-only` | write the plan and exit |

## 2. Exit codes

| Code | Meaning |
| --- | --- |
| 0 | a run finished and its result document validates |
| 2 | the command line or a supplied file was unusable (bad argument, unusable criteria file, refused fixture pack) |
| 3 | the result document failed its own validation and was **not** written |
| 4 | the run measured nothing (every item failed) |
| 5 | an adapter attempted network access; the run was abandoned and the result is `violated` |
| 6 | no adapter was supplied, or a named adapter/model path does not exist |

Exit code 6 is separate because it is the most likely first mistake: there is no default model.

## 3. Fixtures

**Synthetic** (always generated, never downloaded). Nine fixtures covering a tone, a noisy tone, a
clipped tone, a quiet tone, white noise, silence, a chirp, speech-shaped noise and noisy
speech-shaped noise. They are byte-identical for a given seed. They contain **no speech**, so they
measure latency, throughput, memory and robustness — never word error rate, entity accuracy or task
success.

**Licensed** (operator-supplied directory with `pack.json`):

```json
{
  "schema": "jarvis.voice-fixtures/1",
  "items": [
    {
      "id": "en.clean.001",
      "file": "en/clean_001.wav",
      "language": "en",
      "transcript": "Turn the kitchen lights off.",
      "entities": [{"kind": "device", "text": "kitchen lights"}],
      "condition": "clean",
      "licence": "CC0-1.0",
      "source": "recorded by the owner"
    }
  ]
}
```

Requirements: 16-bit PCM WAV, a declared licence (CC0 / CC-BY / public domain / self-generated /
own recording / Apache-2.0 / MIT), a transcript, a language from the PR-1 proposal, and
`condition` ∈ `clean`, `noisy_10db`, `quiet_-40dbfs`, `clipped`. The loader **refuses** personal
audio (`personal: true`), missing licences, unknown languages, paths that escape the pack, empty
transcripts, duplicate ids and non-16-bit audio. Private user audio is out of scope for this tool.

## 4. What is measured, and where the numbers come from

| Metric | Source | Gate before it is reported |
| --- | --- | --- |
| Latency p50 / p95 | adapter call, per phase, per language, per condition | `n ≥ 20` per cell |
| First output | first streamed item, or completion for a non-streaming adapter | `n ≥ 20` per cell |
| Throughput | audio seconds ÷ wall seconds, real-time factor medians | at least one sample |
| Peak memory | `getrusage` maximum RSS **inside the adapter worker** | at least one worker |
| Word error rate | token edit distance against the licensed transcript | `n ≥ 20` items for an interval |
| Entity accuracy | declared entities present as whole phrases | `n ≥ 10` for an interval |
| Task success | every declared entity present and the output non-empty | `n ≥ 10` for an interval |
| Cold / warm | first measured item vs the rest, per phase | cold is one observation, never a percentile |
| Failures | every non-OK item, with a code and message | always reported |
| Power, thermal, GPU | `/sys` and `/proc` only, when the host exposes them | `not_available` with a reason when it does not |

Below a gate the value is **not reported**: percentiles become `null` with
`not_measured: "insufficient_samples"`, and the raw point estimate is kept under `below_gate` (per
language) or in the item list so the arithmetic is auditable. Nothing is extrapolated.

Timing boundaries are in `plan.json → timing_boundaries` and repeated in every report: a phase times
the adapter call itself; the end-to-end phase times the parent's wall clock across recognition and
synthesis, including local IPC and excluding the network (there is no network).

## 5. The result document

`result.json` carries, at the top level: `schema`, `bench_version`, `run`, `identity` (plan hash,
git state, exact command), `host` (probed inventory), `host_role` **and** `host_role_source`
(`operator_declaration` — the tool cannot tell what machine it is on), `comparable_to_owner_server`,
`decision_eligible`, `plan`, `methodology`, `adapters` (hashes and hash modes), `phases`, `overall`,
`languages`, `conditions`, `items`, `failures`, `network`, `not_measured`, `claims`, `gate_status`,
`model_selection`, `decision`.

`decision_eligible` means *fit to be judged*, not *decided*: the owner's own host, a completed run,
no boundary breach and at least one scheduled language. A development-host run is always `false`.

Three rules it enforces on itself, checked by `results.validate_result` before anything is written:

1. **`not_measured` is required.** Every metric the benchmark knows about appears, holding `null`
   when measured or a non-empty reason when not. Both directions are checked: a reason cannot hide a
   measurement, and a measurement cannot be hidden behind a reason.
2. **`claims` are fixed booleans**, all `false` where a claim would be a lie
   (`voice_capability_implemented`, `model_selected`, `provider_selected`, `hardware_purchased`,
   `statistical_rate_established`, `language_fluency_claimed`,
   `results_apply_to_other_languages`), with `benchmark_is_not_a_capability: true`.
3. **Vocabulary is closed.** A language's `tier` is always `not_assigned`, `fluency_claim` is always
   `null`, `gate_status["G-O1"]` is always `blocked`, `model_selection` is always `not_performed`,
   and the decision is one of `not_evaluated`, `rejected`, `not_decidable`,
   `eligible_for_owner_review`.

Validation also refuses a document containing something credential-shaped or an absolute personal
path (`/home/<user>/…`), so a result can be published without redaction.

## 6. Decisions: `app.bench.decision`

A run answers one question — *did this clear a bar the owner wrote down in advance?*

- No criteria file → `not_decidable`. There are no default thresholds.
- A development host, a failed run, a scheduled language with no measured metric, or a sample count
  below the criteria minimum → `not_decidable`.
- A breached threshold, or any denied network attempt → `rejected`.
- Everything cleared, on a declared dedicated server, with no failures → `eligible_for_owner_review`
  (and the document's `decision_eligible` becomes `true`).

Even then, G-O1 is still blocked and the decision block lists what remains: the M5 workload review,
G-W and G-C, the local-boundary acceptance, the dedicated-server baseline and per-language latency,
and owner approval (A9). `decision.compare()` exists only to refuse: this tool never ranks
candidates.

The criteria file format (see `criteria.example.json` — **placeholders, not a bar**):

```json
{
  "schema": "jarvis.voice-bench-criteria/1",
  "criteria_id": "owner-workload-2026-11",
  "min_samples_per_language": 20,
  "languages_required": ["en"],
  "thresholds": {"wer_mean_max": 0.08, "latency_p50_ms_max": 1500,
                 "latency_p95_ms_max": 3000, "task_success_min": 0.90},
  "notes": "why these numbers"
}
```

A criteria file naming a product, a tier or a winner is refused: a bar is a measurement threshold,
not a purchase order.

## 7. Local-only enforcement

The adapter runs in its own process. Before the adapter file is imported, the worker installs
`AdapterSandbox`, which:

- patches `socket.connect` / `connect_ex` through the application's egress guard with
  `allow_loopback=False`;
- refuses name resolution (`getaddrinfo`, `gethostbyname`, `gethostbyname_ex`,
  `create_connection`) — a look-up is already an outbound request;
- counts attempts outside the adapter, so an adapter that swallows the exception still cannot hide
  it;
- redirects the adapter's `stdout` to `stderr`, so adapter chatter cannot corrupt the protocol.

One attempt ends the run: the result is `violated`, the decision is `rejected`, and the exit code is
5. `--allow-loopback-ipc` relaxes loopback only and is recorded in `network.loopback_allowed`.

## 8. The adapter contract

An adapter is a single Python file named explicitly (there is no discovery):

```python
ADAPTER = {"name": "...", "version": "...", "kind": "asr",   # "asr" | "tts" | "turn"
           "languages": ["en"], "streaming": False, "notes": "..."}

def transcribe(request: dict) -> dict:   # kind == "asr":  {"text": "...", "language": "en"?}
    ...

def synthesise(request: dict) -> dict:   # kind == "tts":  {"audio_bytes": N, "sample_rate": R}
    ...

def respond(request: dict) -> dict:      # kind == "turn": {"text": "..."}
    ...
```

`request` carries `audio_path`, `text`, `language`, `condition`, `sample_rate` and `repeat`.
Streaming variants (`transcribe_stream`, `synthesise_stream`) are used only when
`ADAPTER["streaming"]` is true and the function exists; the first yielded item defines first output.
A declared `ADAPTER["languages"]` list is recorded as a **claim**, never as a measurement.

Runnable doubles used by the tests live in `app/tests/bench_adapters/` — they return fixed strings
and prove the harness wiring; their numbers are not evidence of anything.

## 9. Statistics

Pre-registered in `stats.py` and echoed into every result:

- `MIN_SAMPLES_LATENCY = 20`, `MIN_SAMPLES_RATE = 10`, `MIN_SAMPLES_WER = 20`;
- percentiles by linear interpolation over the sorted sample;
- proportions with a Wilson 95% interval; mean WER with a percentile bootstrap (1000 resamples,
  seed 20261101 — reproducible for a given label);
- every number is returned beside its `n`.

These are **descriptive statistics over a fixed fixture set**. They do not estimate behaviour on
unseen audio, in a real room, or against an adversary, and no test in this repository establishes a
statistical rate.

## 10. Tests

```bash
PYTHONPATH=/home/user/JARVIS python3 -m unittest app.tests.test_bench_units
PYTHONPATH=/home/user/JARVIS python3 -m unittest app.tests.test_bench_run
```

`app/tests/test_bench_units.py` covers statistics gates, the language matrix, fixture refusals,
hashes, the sandbox, scoring, result validation, the decision protocol and the report.
`app/tests/test_bench_run.py` runs the command end to end against the doubles: a valid run, a
missing adapter, an unusable criteria file, denials at request time and at import time, an adapter
that always fails, per-language isolation, the sample gate, and plan reproducibility.

The methodology in prose, including what this benchmark deliberately does not measure, is
`docs/benchmarks/voice-workload-benchmark.md`.
