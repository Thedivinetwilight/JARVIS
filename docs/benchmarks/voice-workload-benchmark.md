# Local voice workload benchmark — pre-registered methodology

Status: **measurement tooling only.** Not a capability, not a decision, not a model selection.

- Implementation: `app/bench/` (see `app/bench/README.md` for the command and flags).
- Result schema: `jarvis.voice-bench/1`; benchmark version `0.1.0`.
- Gates this document touches: **G-O1 stays blocked**, G-W and G-C stay open, Q19 (languages) and
  Q20 (server) stay open. Nothing here closes, opens or reinterprets them.

This document is written **before** any measurement is taken from it, which is the point: the repeat
count, the warm-up, the audio conditions, the sample gates, the timing boundaries and the decision
vocabulary are fixed here so that a later result cannot quietly redefine the question to fit the
number. Changing any of them is a methodology change and requires a benchmark version bump with a
note in this file.

## 1. The question, and the questions this refuses to answer

**Answers:** *for an adapter and model the operator names explicitly, on this machine, with this
fixed workload, what was the latency, throughput, peak memory, error rate and failure behaviour —
per language, per audio condition, cold and warm?*

**Does not answer, and never claims:**

- whether speech recognition or synthesis should be adopted for the product (that is G-O1, and it
  needs the M5 workload, a budget, and the owner's approval);
- which model, provider, accelerator, server or vendor is better — candidates are never ranked, and
  this tool has no notion of a winner;
- whether a language is "supported" or "fluent" — only that specific fixtures in that language
  produced specific numbers;
- whether the assistant's reasoning is any good — the optional turn phase times a component; it does
  not evaluate answers;
- anything about speaker verification, false accept/reject, barge-in, wake-word accuracy, real
  rooms, real microphones, long-run stability, or concurrent users.

A completed run changes nothing about the product: voice input and output remain unavailable in
`app/jarvis`, `/v1/status` is untouched, and the result document's `claims` block states this in
fixed booleans.

## 2. Host recording

The result records what the host exposes and nothing more:

| Fact | Method | When absent |
| --- | --- | --- |
| CPU model, physical and logical cores, architecture | `/proc/cpuinfo`, `os.cpu_count` | recorded as available facts only |
| Memory total | `/proc/meminfo` | `not_available` with a reason |
| OS, kernel, Python runtime | `/etc/os-release`, `platform` | `not_available` with a reason |
| GPU and video memory | `/proc/driver/*/gpus/*` and DRM sysfs attributes | `not_available`; **no vendor tool is called and nothing is estimated** |
| Thermal zones | `/sys/class/thermal`, `/sys/class/hwmon` | `not_available` with a reason |
| Power | `/sys/class/power_supply/*/power_now` or powercap energy counters | `not_available`; never derived from a model number or a TDP |
| Storage | `statvfs` on the working directory | `not_available` with a reason |
| Host name | only with `--include-hostname` | `not_recorded` |

`host_id` is a truncated digest of machine identity, so two runs can be compared without publishing
a host name. An operator who cannot probe a locked-down server may supply a facts file; those values
are labelled `operator_declared` and are never mixed into probed values.

**Host role is a declaration, not a detection.** `--host-role development` (the default) marks the
run as *not* comparable to the owner's server and never decision-eligible; `dedicated_server` marks
it comparable. The result records `host_role_source: "operator_declaration"` for exactly this
reason, and the report repeats it. A development-host result may inform a plan; it may never stand
in for a dedicated-server measurement.

## 3. Fixtures

**Synthetic** — nine generated fixtures (tone, noisy tone, clipped tone, quiet tone, white noise,
silence, chirp, speech-shaped noise, noisy speech-shaped noise; 16 kHz mono 16-bit PCM),
byte-identical for a given seed. They contain no speech and carry no transcript, so they can measure
**latency, throughput, memory and robustness** and cannot measure word error rate, entity accuracy
or task success. The result says so, per item and in `not_measured`.

**Licensed** — an operator-supplied `pack.json` with file, language, transcript, entities, licence,
source and condition per item. Refused outright: personal audio, missing or unrecognised licence,
a language outside the proposed matrix, a path escaping the pack, an empty transcript, duplicate
ids, audio that is not 16-bit PCM. Nothing is downloaded and no private user audio is in scope.

**Conditions** are properties of the audio: `clean`, `noisy_10db`, `quiet_-40dbfs`, `clipped`.
Latency is reported per condition so a "clean-only" impression cannot be formed by accident.

## 4. Phases and timing boundaries

| Phase | Runs when | Timed from → to |
| --- | --- | --- |
| `asr` | an ASR adapter is supplied | adapter invocation start (whole fixture delivered) → adapter returns text; harness↔worker IPC excluded |
| `tts` | a TTS adapter is supplied | adapter invocation start (whole sentence delivered) → adapter returns audio; IPC excluded |
| `turn` | a turn adapter is supplied | adapter invocation start → adapter returns text; the turn component is optional and is not a model of the assistant |
| `e2e` | ASR **and** TTS are supplied | parent wall clock from posting audio to recognition to receiving first synthesised audio; local IPC included, network excluded (there is none) |

The end-to-end phase is a pipeline measurement of recognition→synthesis. Without a turn adapter the
result states `turn_component: "none: recognised text passed to synthesis, no assistant reasoning"`.
These are **not** the C-03 end-of-speech→first-audio thresholds: C-03 requires a bar per language on
the owner's dedicated server, and this tool measures; it does not certify.

## 5. The sample plan, fixed in advance

- `--repeat` measured rounds per item, default **3**, allowed 1–50;
- `--warmup` discarded rounds per item, default **1**, allowed 0–20;
- items are ordered deterministically (warm-up first, then phase, language, fixture, condition,
  round), so "cold" and "warm" mean the same thing in every run;
- the seed (default **20261101**) fixes both the synthetic fixtures and the bootstrap intervals;
- an item that exceeds `--timeout` (default 60 s) is killed, recorded as a failure, and — after more
  than three restarts of that adapter — ends the run.

The executed plan, its task count, the fixture hashes and the exact command are written to
`plan.json` before measurement, with a `plan_hash` over the canonical plan. The same configuration
produces the same `plan_hash`; the hash excludes timestamps and the output directory.

## 6. Statistics and the gates that gate them

| Quantity | Method | Reported only when |
| --- | --- | --- |
| Latency p50, p95 | linear interpolation over the sorted sample | `n ≥ 20` in that cell |
| Proportions (task success, entity accuracy) | point value + Wilson 95% interval | interval when `n ≥ 10` |
| Mean word error rate | mean + percentile bootstrap interval (1000 resamples, fixed seed) | interval when `n ≥ 20` items |
| Throughput | audio seconds ÷ wall seconds; real-time factor median | at least one sample |
| Peak memory | `getrusage` maximum RSS inside the adapter worker | at least one worker |
| Cold latency | first measured item of a phase | always, as a single observation with no percentile |

Below a gate the number is **not reported**: the percentile fields are `null` and `not_measured`
carries `insufficient_samples`. Per-language point estimates that did not clear their gate are kept
under `below_gate` with `gate_cleared: false` so the arithmetic is auditable and cannot be quoted as
a result. Every reported number carries its `n`.

**No statistical rate is established.** These are descriptive statistics over a fixed fixture set.
Nothing here estimates performance on unseen audio, in a real room, under load, or against an
adversary, and the tests in this repository establish behaviour, not rates.

## 7. Per-language rules

PR-1 (fifteen proposed languages) is an **unapproved evaluation matrix**. Therefore:

1. a language is measured **only** from its own licensed fixtures; there is no code path that copies
   a result between languages, including between closely related ones;
2. a language that was not scheduled is `not_run`, with a reason, and carries no numbers;
3. a language whose metrics did not clear the gates is `not_measured`, with the point estimates kept
   separately as `below_gate`;
4. every entry's `tier` is `not_assigned` and its `fluency_claim` is `null` — a support tier needs a
   bar the owner has not set (C-03-AC4, Q19);
5. the matrix is read from `docs/product/capabilities.json` and never written back.

## 8. Local-only enforcement

Deny by default, fail closed, and record:

- the adapter runs in a separate process; the sandbox is installed **before** the adapter is
  imported, so import-time network access is caught too;
- `socket.connect`, `connect_ex`, `getaddrinfo`, `gethostbyname`, `gethostbyname_ex` and
  `create_connection` are refused; a name lookup counts as an outbound request;
- attempts are counted outside the adapter, so swallowing the exception cannot hide the attempt;
- **one attempt ends the run**: result `violated`, decision `rejected`, exit code 5;
- `--allow-loopback-ipc` permits loopback only (a local inference daemon), is recorded in
  `network.loopback_allowed`, and still refuses every other destination;
- the adapter's `stdout` is redirected to `stderr` so it cannot inject into the protocol;
- a worker log digest is recorded with the result so the recorded run can be checked against the
  log it produced.

## 9. The decision protocol

Vocabulary is closed: `rejected`, `not_decidable`, `eligible_for_owner_review` (plus
`not_evaluated` for a document that has not been through the protocol).

- **`rejected`** — a bar in the operator's criteria file was breached, or any network attempt
  occurred.
- **`not_decidable`** — no criteria file; a development host; a run that did not complete; a
  scheduled language without a gate-cleared metric; a sample count below the criteria minimum; a
  criteria file that is unusable or that names a product.
- **`eligible_for_owner_review`** — every bar cleared, on a declared dedicated server, no failures,
  no network attempts.

`decision_eligible` on the document is narrower than the verdict: it means the run is *fit to be
judged* (the owner's host, completed, no boundary breach, a scheduled language). It never means
"chosen".

Even an eligible run leaves G-O1 blocked. The decision block lists what still stands between it and
any voice implementation:

1. the M5 workload review, complete and reviewed;
2. G-W and G-C passed with their own evidence;
3. the local-first boundary accepted;
4. a dedicated-server baseline **and** per-language latency measured on the owner's host;
5. owner approval (A9).

There are no default thresholds. A missing or unusable criteria file yields `not_decidable`, never a
pass.

## 10. What a published result may and may not say

May say: *"On host `host_…` (development), with adapter `X` v`Y` and model hash `Z`, run
`run_…`/plan `…`, the following was measured: …"* — with the `not_measured` list intact and the
command from `identity.reproduce_command`.

May not say: that voice works, that a language is supported or fluent, that a model or vendor was
selected, that a server specification is decided, that hardware should be bought, or that G-O1 has
moved. The result document is validated to refuse those statements.

## 11. Known limits of this document and this tool

- The host role cannot be verified; it is a declaration, recorded as one.
- The synthetic fixtures are not speech, so an all-synthetic run measures plumbing and hardware, not
  recognition.
- The turn phase is optional and does not represent assistant reasoning.
- Only one adapter is loaded per phase per run: the tool measures, it does not rank.
- Peak memory is the worker's maximum RSS; the harness process is not included.
- Power and thermal are only recorded when the kernel exposes them; most development machines expose
  neither, and the result says so instead of guessing.
- A single-machine, single-tenant run says nothing about concurrency, long-duration stability or
  user-perceived quality.

## 12. Change control

Any change to the sample gates (`20/10/20`), the repeat defaults, the warm-up rule, the timing
boundaries, the conditions, the decision vocabulary or the `claims` block is a change to the
pre-registered methodology. It requires: a benchmark version bump, a dated note in this section, and
a re-run of the suites. Editing a number in code without editing this document is a defect.
