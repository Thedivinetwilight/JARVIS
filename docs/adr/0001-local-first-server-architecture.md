# ADR 0001 — A local-first Python stdlib server for the J.A.R.V.I.S. foundation build

- **Status:** accepted (implementation in `app/`)
- **Date:** 2026-11-01
- **Deciders:** owner (architecture direction), implementation agent (this record)
- **Supersedes:** nothing. **Superseded by:** nothing.
- **Related:** `docs/architecture/threat-boundaries.md`, `docs/product/capability-specification.md`
  v1.2.0 §0.2, §2, §3 (C-03, C-04, C-12, C-13, C-14), `docs/research/09-security-and-permissions.md`,
  `docs/runtime-state/` (evaluator evidence, unchanged by this work)

## 1. Context

The repository contains a specification (`docs/product/`, v1.2.0), research notes (`docs/research/`)
and a zero-dependency evaluation harness (`eval/`, 456 tests) whose evidence files are historical
and must not change. Nothing in the repository ran as a *service*: there was no configuration
loader, no authentication, no persistence, no network boundary and no deployment path.

The owner's stated direction constrains the answer tightly:

- processing and personal data stay on a local machine; nothing goes to a cloud AI service;
- the client protocol is voice-first, with **no text chat box and no text conversation endpoint**;
- the home holographic interface stays the primary *planned* spatial display, but only its
  client/API boundary may be built;
- web retrieval, telephony and device-vendor clouds stay switched off until individually scoped
  and approved;
- no model, provider, accelerator or purchase decision may be made (gate **G-O1** is blocked);
- and no capability may be claimed as implemented when it is not.

The build had to be something a single owner can run, audit and reason about on one machine today.

## 2. Decision

Build a **single-process, local-only Python 3.11 service using only the standard library**, in a new
top-level application area `app/`, with the following shape:

| Concern | Decision |
| --- | --- |
| Language/runtime | Python 3.11 (`tomllib`, `sqlite3`, `ssl`, `http.server`); no third-party dependency, no package manager file |
| Layout | `app/jarvis/` (modules), `app/tests/` (unittest), `app/deploy/` (config examples + systemd unit) |
| Configuration | one explicit TOML file; unknown sections/keys are refused; startup validation refuses unsafe combinations |
| Persistence | SQLite via `sqlite3` with ordered SQL migrations and recorded SHA-256 checksums; no ORM |
| Transport | `http.server.ThreadingHTTPServer`, loopback by default, TLS required for any non-loopback bind |
| Auth | server secret (≥32 bytes) from `JARVIS_SERVER_SECRET` or a 0600 file; HMAC-SHA256 token digests; single-use pairing codes; per-manifest scopes with expiry; revocation |
| Egress | a process-wide guard that patches `socket.connect`/`connect_ex` and denies everything except loopback and an in-scope declaration for a declared external service |
| Voice boundary | audio sessions in (`audio/*` only), spoken audio out — which **refuses** with a stable code because no synthesiser exists |
| Reporting | `/healthz`, `/readyz`, `/v1/status`, `/v1/limitations`, `/v1/manifests`, `/v1/surfaces`, `/v1/egress/policy` |
| Capability claim | a mirrored register of C-01…C-14 whose *implementation* value is `not implemented` for every entry, test-enforced against `docs/product/capabilities.json` |
| Real capability | one model-free everyday-assistance capability — local tasks and reminders — with no natural-language claim |
| Tests | `unittest` only, deterministic (`FixedClock`), sockets only in `app/tests/test_http.py` |

## 3. Alternatives considered and rejected

1. **Extend `eval/`.** Rejected: `eval/` is a measurement harness over documents and slices; its
   evidence files are historical records. A server inside it would blur evidence and product, and
   the owner asked for a separate area. `eval/harness/gates.py` therefore keeps its B21/B22-free
   shape.
2. **A web framework (Flask/FastAPI) with a dependency set.** Rejected: it adds a supply chain, a
   wheel/vendoring story and its own auth idioms for no gain on a single-machine service. The
   standard library's `http.server` is adequate for one owner, and it keeps the trust boundary
   inspectable: about 4 100 lines of application code including comments, every line
   readable.
3. **A TypeScript/Node client-plus-server monorepo.** Rejected for now: it presumes a client
   application shell that the specification does not authorise (no client app exists, C-14 is
   specified only), and it would double the runtime surface before any capability works.
4. **A reverse proxy plus service mesh.** Rejected: there is one process, one owner and one port.
   TLS termination belongs in the process or in front of it, not in an infrastructure stack.
5. **Storing tokens in plaintext for easier debugging.** Rejected: digests only. There is no
   debugging need that justifies a credential at rest.
6. **Selecting a speech or language model to make the voice path "work".** Rejected: gate G-O1 is
   blocked, no hardware or budget decision exists, and the specification forbids claiming a
   capability that has not been selected and measured. The unavailable adapter contracts are the
   honest answer.
7. **A permissive CORS/bind for convenience.** Rejected: no browser client exists; a browser origin
   that can reach this server could read a local owner's tasks and audio metadata.

## 4. Consequences

**Positive**

- One process, one file of configuration, one SQLite file, one secret. The owner can read the whole
  system, back it up with `cp`, and stop it with a signal.
- Every security decision sits in `App.request` and is testable without a socket; the HTTP layer is
  a thin adapter.
- Default-deny egress is enforced by the interpreter's socket layer, so an accidental library call
  fails loudly instead of quietly reaching the internet.
- Migration failures, secret problems and unsafe configuration are refused at startup, not at the
  first request.
- The capability register cannot drift from the published specification without a test failing.

**Negative / accepted costs**

- `http.server` is single-process and thread-pooled; it is not built for thousands of clients. That
  is a documented limit, not an oversight.
- `socket.connect` patching is process-wide and lexical (per-thread declarations); C extensions that
  bypass Python's socket layer would not be guarded. No such extension exists here, and adding one
  would require a new decision.
- SQLite is a single-writer store; the audio path writes files next to it and only metadata in it.
- No async, no worker queue, no horizontal scaling. If the owner later needs concurrent transcription
  of many streams, this decision will need revisiting — which is exactly when a new ADR is due.
- Python's standard library has no streaming TLS story for large uploads beyond what `ssl` provides;
  audio chunks are bounded (default 1 MiB) instead.

## 5. What this decision deliberately does not settle

- The model, the accelerator, the budget, the language list, the server host and the device
  inventory — all remain owner decisions (Q13, Q15, Q17–Q26; PR-1/PR-2 pending). Gate **G-O1**
  stays blocked.
- The home holographic display technology (C-13) — only the protocol boundary exists.
- EDITH Glass, VR and Ray-Ban retail glasses — distinct targets, none of them a target of this
  server (EDITH is `not-a-target`).
- AETHERION — untouched.
