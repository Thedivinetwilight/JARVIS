# Architecture records

This directory holds the *decisions* and *boundaries* of the running system. The product
specification, capability registers and research notes live elsewhere and are not duplicated here.

| Document | What it answers |
| --- | --- |
| [`threat-boundaries.md`](threat-boundaries.md) | What the local server protects, from whom, and what it explicitly does not protect |
| [`reminder-delivery.md`](reminder-delivery.md) | Why reminder delivery is a local poll-and-acknowledge queue rather than a push channel, and what it deliberately cannot do |
| [`../adr/0001-local-first-server-architecture.md`](../adr/0001-local-first-server-architecture.md) | Why `app/` is a Python-stdlib, single-process, local-only service, and what that costs |
| [`../../app/README.md`](../../app/README.md) | How to configure, run and test the server; the endpoint and limitation surface |
| [`../benchmarks/voice-workload-benchmark.md`](../benchmarks/voice-workload-benchmark.md) | The pre-registered methodology of the local measurement tool: what it measures, what it refuses, and why a result is not a decision |
| [`../../app/bench/README.md`](../../app/bench/README.md) | How to run the benchmark: flags, exit codes, adapter contract, fixture pack format |
| [`../benchmarks/benchmark-status.md`](../benchmarks/benchmark-status.md) | What is verified, what has been measured, and what is still missing before any speech measurement exists |
| [`../benchmarks/operator-runbook.md`](../benchmarks/operator-runbook.md) | The exact steps, files and checks for running the benchmark on the owner's designated server |

Adjacent, unchanged material:

- `docs/product/` — the specification (v1.2.0), capability register, roadmap, coverage and device
  matrix. These describe intent and limits; they do not describe this server's internals.
- `docs/research/` — the research notes, including `09-security-and-permissions.md`, which this
  build implements only where it has code to show for it.
- `docs/benchmarks/` — the benchmark methodology. It documents a measurement tool that is not part
  of the server process and does not change any capability, gate or processing boundary.
- `docs/runtime-state/` — historical evaluator evidence. Not produced or modified by the server work.
- `eval/` — the measurement harness over those documents. It never imports `app/`.
