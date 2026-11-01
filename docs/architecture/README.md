# Architecture records

This directory holds the *decisions* and *boundaries* of the running system. The product
specification, capability registers and research notes live elsewhere and are not duplicated here.

| Document | What it answers |
| --- | --- |
| [`threat-boundaries.md`](threat-boundaries.md) | What the local server protects, from whom, and what it explicitly does not protect |
| [`../adr/0001-local-first-server-architecture.md`](../adr/0001-local-first-server-architecture.md) | Why `app/` is a Python-stdlib, single-process, local-only service, and what that costs |
| [`../../app/README.md`](../../app/README.md) | How to configure, run and test the server; the endpoint and limitation surface |

Adjacent, unchanged material:

- `docs/product/` — the specification (v1.2.0), capability register, roadmap, coverage and device
  matrix. These describe intent and limits; they do not describe this server's internals.
- `docs/research/` — the research notes, including `09-security-and-permissions.md`, which this
  build implements only where it has code to show for it.
- `docs/runtime-state/` — historical evaluator evidence. Not produced or modified by the server work.
- `eval/` — the measurement harness over those documents. It never imports `app/`.
