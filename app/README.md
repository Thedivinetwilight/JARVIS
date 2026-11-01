# `app/` — the local J.A.R.V.I.S. server (foundation build)

A single-process, local-only service written in Python 3.11 **using only the standard library**.
It is the runtime boundary the specification describes: configuration, pairing and revocation,
scoped capability manifests, local storage, default-deny networking, health/limitation reporting,
and a voice-first client protocol with **no text chat box anywhere**.

It is not a model, not a speech system, not a client application, and not the holographic display.

- Decision record: [`docs/adr/0001-local-first-server-architecture.md`](../docs/adr/0001-local-first-server-architecture.md)
- Threat boundaries: [`docs/architecture/threat-boundaries.md`](../docs/architecture/threat-boundaries.md)
- What the product promises: [`docs/product/capability-specification.md`](../docs/product/capability-specification.md) v1.2.0

## 1. What works end to end today

| Works, and is tested | Does **not** exist, and is reported as unavailable |
| --- | --- |
| Explicit TOML configuration with startup refusal of unsafe settings | Any model, provider or accelerator (gate **G-O1** is blocked) |
| Operator token, single-use pairing codes, HMAC-digested tokens, revocation | Speech recognition or synthesis (`speech_asr_unavailable`, `speech_tts_unavailable`) |
| Per-manifest least-privilege scopes with expiry, auditable grants | Any natural-language or text-chat entry point (`text_conversation_not_supported`) |
| SQLite persistence with checksum-verified migrations | Push, spoken or on-screen reminders (`reminder_delivery_unavailable`) |
| Default-deny outbound network policy, loopback only | Web retrieval, telephony, device-vendor clouds, update downloads (all disabled) |
| Append-only audit trail with secret redaction | Any device client application (PC, tablet, phone, VR, glasses) |
| Health, readiness, status, limitation, manifest, surface and egress reporting | Holographic rendering, optics or display technology (C-13 is boundary-only) |
| Audio sessions: `audio/*` chunks stored locally, bounded, expiring, purged on revocation | Spoken audio **output** — the endpoint refuses rather than substituting text |
| Local tasks and reminders (structured input, model-free) | Understanding of spoken or typed sentences |
| A local reminder queue: one explicitly named client retrieves and acknowledges its events | Any notification the server initiates — nothing is pushed, spoken, rendered or sent |

The capability register in `app/jarvis/capabilities.py` mirrors `docs/product/capabilities.json` and
reports **every** product capability C-01…C-14 as `not implemented`. A test fails if that mirror
drifts.

## 2. Requirements

- Python **3.11+** (`tomllib`, `sqlite3`, `ssl`, `http.server`; `sqlite3` 3.40+ recommended).
- No third-party packages, no virtual environment, no package manager file.
- A writable data directory owned by the service user.

## 3. Quick start (trial on one machine)

```bash
cd /path/to/JARVIS
export JARVIS_SERVER_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"

python3 -m app.jarvis check-config --config app/deploy/config.trial.toml
python3 -m app.jarvis migrate      --config app/deploy/config.trial.toml
python3 -m app.jarvis operator     --config app/deploy/config.trial.toml   # prints a token ONCE
python3 -m app.jarvis serve        --config app/deploy/config.trial.toml
```

`config.trial.toml` resolves `data_dir` to `<repo>/var/jarvis` (already git-ignored). The trial
configuration binds `127.0.0.1:7420`.

In another terminal:

```bash
TOKEN='jvs_...'                       # the operator token printed above
curl -s localhost:7420/healthz | python3 -m json.tool
curl -s -H "Authorization: Bearer $TOKEN" localhost:7420/v1/limitations | python3 -m json.tool
```

## 4. Pairing a voice client, then using it

Everything privileged happens on the server console, never over HTTP.

```bash
# 1. Mint a single-use code (default scopes: tasks, reminders, status, manifests, audio capture)
python3 -m app.jarvis pair-code --config app/deploy/config.trial.toml \
    --label-hint "kitchen tablet" --platform tablet
# -> {"pairing_code": "ABCD-EFGH-JKLM", ...}

# 2. The device redeems it exactly once; the token is shown once and stored as a digest
curl -s -X POST localhost:7420/v1/pairing -H 'content-type: application/json' \
     -d '{"code":"ABCD-EFGH-JKLM","label":"kitchen tablet","platform":"tablet"}'

# 3. Structured task (NOT a chat message: title/notes/due_at only)
curl -s -X POST localhost:7420/v1/tasks -H "Authorization: Bearer $DEVICE" \
     -H 'content-type: application/json' \
     -d '{"title":"buy milk","due_at":"2026-11-02T09:00:00Z"}'

# 4. Audio session in (audio/* only; text/plain is refused with text_input_not_supported)
curl -s -X POST localhost:7420/v1/audio/sessions -H "Authorization: Bearer $DEVICE"
curl -s -X POST localhost:7420/v1/audio/sessions/$SID/audio -H "Authorization: Bearer $DEVICE" \
     -H 'content-type: audio/wav' --data-binary @clip.wav
curl -s -X POST localhost:7420/v1/audio/sessions/$SID/end  -H "Authorization: Bearer $DEVICE"

# 5. Spoken output: refused, honestly (503 speech_tts_unavailable) — no text is substituted
curl -s localhost:7420/v1/audio/output -H "Authorization: Bearer $DEVICE"
```

## 5. HTTP surface (26 routes)

Every route except `/healthz`, `/readyz` and `POST /v1/pairing` requires
`Authorization: Bearer jvs_…`. There is no route that accepts free text for interpretation and no
route that forwards data off the machine.

| Method & path | Scope | Manifest gate | Purpose |
| --- | --- | --- | --- |
| `GET /healthz` | — | — | liveness (public, no secrets) |
| `GET /readyz` | — | — | ready only if migrations applied, data dir writable and egress guard installed |
| `POST /v1/pairing` | — | — | redeem a single-use pairing code for a device token |
| `GET /v1/status` | `status.read` | — | server, storage, capability and limitation summary |
| `GET /v1/limitations` | `status.read` | — | 11 stable-coded limitations |
| `GET /v1/config` | `status.read` | — | public configuration (never the secret) |
| `GET /v1/manifests` | `manifests.read` | — | capability/connector/surface manifests and scopes |
| `GET /v1/egress/policy` | `status.read` | — | deny-by-default policy and counters |
| `GET /v1/surfaces` | `status.read` | — | display surfaces, all `implemented: false` |
| `GET /v1/audio/capabilities` | `status.read` | — | ASR/TTS/text-conversation availability (all false) |
| `GET /v1/audio/output` | `speech.output` | `speech.tts` | spoken output — **503 by design** |
| `POST /v1/audio/sessions` | `audio.capture` | `audio.session` | open an audio session |
| `POST /v1/audio/sessions/{id}/audio` | `audio.capture` | `audio.session` | append one `audio/*` chunk |
| `POST /v1/audio/sessions/{id}/end` | `audio.capture` | `audio.session` | close the session, report bytes |
| `GET /v1/tasks` | `tasks.read` | `tasks` | list (`status=open\|completed\|all`, `limit`) |
| `POST /v1/tasks` | `tasks.write` | `tasks` | create (`title`, `notes`, `due_at`, `source`, `deliver_to`) |
| `POST /v1/tasks/{id}/complete` | `tasks.write` | `tasks` | complete (idempotent) |
| `DELETE /v1/tasks/{id}` | `tasks.write` | `tasks` | soft-delete and drop the reminder |
| `GET /v1/reminders/due` | `reminders.read` | `reminders` | due reminders (`now`, `limit`); says whether each is routed |
| `POST /v1/reminders/{id}/ack` | `reminders.write` | `reminders` | acknowledge a reminder (idempotent) |
| `GET /v1/reminders/inbox` | `reminders.receive` | `reminders` | this client's queued events (poll; nothing is pushed) |
| `POST /v1/reminders/inbox/{id}/ack` | `reminders.receive` | `reminders` | acknowledge one delivery (idempotent) |
| `GET /v1/clients` | `clients.read` | — | paired clients, digests never returned |
| `GET /v1/clients/me` | `status.read` | — | the calling client |
| `POST /v1/clients/{id}/revoke` | self, or `clients.revoke` | — | revoke; stops the token, deletes audio, closes queued events |
| `GET /v1/audit` | `audit.read` | — | audit trail (`limit`, `after_id`) |

Every response carries `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`. There are
no CORS headers, because no browser client exists. Methods are `GET`, `POST` and `DELETE`; anything
else is `405` from the adapter.

Error shape is always `{"error": {"code", "message", "detail"?}, "request_id": "…"}`, with stable
codes such as `token_missing`, `token_expired`, `token_revoked`, `scope_denied`,
`manifest_unavailable`, `text_input_not_supported`, `session_expired`, `body_too_large`.

### Reminder delivery: a local queue, never a push

Only a reminder whose target was named explicitly at creation is queued, and only for that client.
`deliver_to` must name an existing, unrevoked client holding `reminders.receive`; a reminder without
a target stays **pending and unrouted**, with the reason reported in the task, the due list and
`/v1/status`. Nothing is ever inferred — no default recipient, no device preference, no recurrence.

The queue is a table with `UNIQUE (reminder_id, client_id)`, so retries, restarts and repeated polls
cannot duplicate an event. Reading an event counts as one attempt; further reads inside
`retry_interval_minutes` are the same attempt, and exceeding `max_delivery_attempts` closes the
event as `failed`. An unacknowledged event older than `delivery_window_minutes` is closed as
`expired`. Both are reported in `held_back` with a reason, never silently dropped. Revoking the
target cancels its open events immediately.

Timestamps are stored in UTC. `reminders.display_timezone` renders a wall clock with the offset in
force at that instant, so the two 01:30s of a daylight-saving fall-back are two distinct instants
(each delivered once), and a time that does not exist on a spring-forward day is never invented.
A naive `due_at` is recorded as `due_time_basis: "assumed_utc"` rather than presented as local time.

The audit trail records `reminder_delivery.enqueued`, `.attempt`, `.acknowledged`, `.expired`,
`.failed`, `.cancelled` and `.reconciled` with ids, counts and reasons only — never a reminder title
or note. No response describes a queued event as spoken, displayed, pushed or notified, and the
limitation `reminder_delivery_unavailable` keeps saying so. Full rationale:
`docs/architecture/reminder-delivery.md`.

## 6. Configuration

`app/deploy/config.example.toml` is the production shape; `app/deploy/config.trial.toml` is the
same thing with a repository-local data directory. Neither contains a secret.

| Section | Key | Meaning |
| --- | --- | --- |
| `server` | `bind`, `port` | loopback by default; a non-loopback bind requires TLS |
| | `tls_cert`, `tls_key` | must be set together and exist; TLS 1.2 minimum |
| | `request_timeout_s`, `max_request_bytes` | per-request socket timeout and body cap |
| `storage` | `data_dir` | created `0700`; may not live inside `eval/` or `docs/` |
| | `database` | file name inside `data_dir` (no paths) |
| | `audio_retention_days` | audio older than this is purgeable with `purge-audio` |
| | `max_audio_bytes_per_session`, `max_audio_chunk_bytes` | hard byte budgets |
| | `audio_session_ttl_minutes` | session expiry |
| `auth` | `device_token_ttl_days`, `operator_token_ttl_days` | token lifetime |
| | `pairing_code_ttl_minutes`, `pairing_max_failed_attempts` | pairing window and throttle |
| | `secret_file` | optional 0600 alternative to the environment variable |
| `external_services` | `web_retrieval`, `telephony`, `device_vendor_cloud`, `update_downloads` | **must be `false`**; `true` refuses startup |
| `adapters` | `speech_asr`, `speech_tts` | **must be `unavailable`**; anything else refuses startup |
| `logging` | `level` | `debug`/`info`/`warning`/`error` |

Rules that are enforced, not documented-and-hoped:

- unknown sections or keys are refused, so a typo cannot silently become a default;
- the secret is read **only** from `JARVIS_SERVER_SECRET` (≥32 bytes, not a placeholder) or a 0600
  file, never from the TOML file, and never written to the database, the logs or a response;
- startup is refused for: a non-loopback bind without TLS, an enabled external service, a selected
  speech adapter, an unwritable or misplaced data directory, or a bad secret.

## 7. CLI

```bash
python3 -m app.jarvis <command> --config <path>
```

| Command | Effect |
| --- | --- |
| `check-config` | validate configuration, print the public view, exit non-zero on refusal |
| `migrate` | apply pending migrations (idempotent, checksum-verified) |
| `serve` | run the server (`--port`, `--no-migrate`); SIGTERM/SIGINT shut down cleanly |
| `operator` | create the operator token (printed once) |
| `pair-code` | mint a single-use pairing code (`--label-hint`, `--platform`, `--scopes`, `--ttl-minutes`) |
| `clients` | list paired clients |
| `revoke --client-id` | revoke a client and delete its stored audio |
| `audit [--limit]` | print recent audit records |
| `limitations` | print the limitation catalogue |
| `purge-audio` | delete audio past the retention window |
| `status` | storage, clients, manifests, egress and task counts |

Exit codes: `0` ok, `2` configuration refused, `3` database not migrated, `4` other refusal.

## 8. Storage layout

```
<data_dir>/
├── jarvis.sqlite3           0600, WAL; clients, pairing codes, grants, audit, tasks, reminders, sessions
└── audio/<client_id>/<session_id>/chunk-0001.bin   0700 directories
```

Migrations are ordered SQL files in `app/jarvis/migrations/` with a recorded SHA-256 checksum.
Editing an applied migration is a hard error, not silent drift. **Back up by copying the data
directory while the service is stopped** (or use SQLite's backup API).

## 9. Deployment

`app/deploy/jarvis.service` is a hardened systemd unit: `NoNewPrivileges`, `ProtectSystem=strict`,
`PrivateTmp`, `PrivateDevices`, a single `ReadWritePaths=/var/lib/jarvis`, `UMask=0077` and a
restricted address-family set. `JARVIS_SERVER_SECRET` belongs in `/etc/jarvis/jarvis.env`
(mode `0600`), never in the unit file or the repository.

TLS is required for any non-loopback bind. Terminate TLS in-process (`tls_cert`/`tls_key`) or keep
the bind on loopback and use an SSH tunnel; do not expose this server to a network without a new
decision record.

## 10. Tests

```bash
# this application area, including the benchmark (290 tests)
PYTHONPATH=/home/user/JARVIS python3 -m unittest discover -s app/tests -t .

# the existing evaluator harness (456 tests) — must stay green, and is never modified by this work
PYTHONPATH=/home/user/JARVIS python3 -m unittest discover -s eval/tests -t .
```

Coverage of the required areas, by module:

| Area | Module |
| --- | --- |
| configuration and startup refusal | `test_config.py` |
| migrations, checksums, foreign keys | `test_storage.py` |
| pairing, token lifetime, revocation | `test_auth.py` |
| scope enforcement, body bounds, audit | `test_api_security.py` |
| default-deny egress and adapters | `test_egress.py` |
| tasks and reminders | `test_tasks.py` |
| reminder delivery: schema, access, restart, DST, retries, revocation, redaction, egress | `test_reminder_delivery.py` |
| audio protocol and spoken-output refusal | `test_audio.py` |
| health/readiness/limitations, capability parity, surfaces | `test_reporting.py` |
| the HTTP adapter over real sockets | `test_http.py` |
| the operator CLI, including a real `serve`/SIGTERM cycle | `test_cli.py` |
| the static boundaries (no vendor names, no outbound client, no UI assets) | `test_boundaries.py` |
| the benchmark's building blocks (gates, matrix, fixtures, hashes, decisions) | `test_bench_units.py` |
| the benchmark command end to end, including denials and failures | `test_bench_run.py` |

The tests are deterministic (`FixedClock`, temporary data directories, port 0) and prove behaviour
on the inputs they use. They establish **no statistical rate** for anything — not for
authentication guessing, not for false accepts in speech, because there is no speech system.

## 11. Benchmark: measurement tooling, not a capability

`app/bench/` runs an operator-supplied local speech adapter on a fixed workload and records what it
observed. It is deliberately separate from the server: it is not wired into `app/jarvis`, no route
uses it, and running it changes nothing about what the product can do. Voice input and output remain
unavailable.

```bash
# write the plan only: no adapter needed, nothing measured
PYTHONPATH=/home/user/JARVIS python3 -m app.bench --plan-only --languages en --out bench-out

# a real run: latency, throughput, memory, failures (synthetic audio, no speech to recognise)
PYTHONPATH=/home/user/JARVIS python3 -m app.bench \
    --asr-adapter /path/to/local/asr_adapter.py --asr-model /path/to/local/model.bin \
    --repeat 3 --warmup 1 --out bench-out

# a run that measures word error rate: needs a licensed fixture pack, per language
PYTHONPATH=/home/user/JARVIS python3 -m app.bench \
    --asr-adapter /path/to/local/asr_adapter.py --fixtures /path/to/licensed-pack \
    --languages en,hi --repeat 25 --warmup 1 --out bench-out
```

It refuses, by construction: a default model or any download (an adapter and model path must be
named), network access of any kind — including loopback unless explicitly allowed and recorded — a
language outside the PR-1 proposal, personal or unlicensed audio, and any wording that a model,
vendor, server or language tier has been chosen. An adapter that attempts a connection ends the run
with exit code 5, a `violated` result and a `rejected` decision.

Output per run: `plan.json` (hashed plan and the exact command), `fixtures/`, `logs/`,
`result.json` (`jarvis.voice-bench/1`) and `report.md`. Every metric the benchmark knows about
appears in `not_measured` holding either `null` or a reason; percentiles below their sample gate are
withheld rather than printed. A development-host run says so and is never decision-eligible.

Full detail: `app/bench/README.md` (usage, exit codes, adapter contract) and
`docs/benchmarks/voice-workload-benchmark.md` (the pre-registered methodology). Current measurement
status — tool verified, development-host plumbing measured with test doubles, **owner-server speech
measurement not obtained** — is `docs/benchmarks/benchmark-status.md`; the procedure for the owner's
server is `docs/benchmarks/operator-runbook.md`. Gate **G-O1** stays blocked; Q19 and Q20 stay open.

## 12. What is deliberately absent, and why

- **No text chat endpoint, no chat UI, no `POST /v1/chat`.** The product is voice-first; a text
  conversation surface would contradict the specification (R-6, gate G-UX). Requests to such paths
  return `404 route_not_found`; `text/plain` on the audio route returns `415
  text_input_not_supported`.
- **No model, no vendor, no accelerator, no purchase.** Gate G-O1 is blocked; the owner has not
  supplied the workload baseline, budget or language priorities. Nothing here recommends one.
- **No speech.** The adapter contracts exist (`speech.py`: `Protocol` for recognition and
  synthesis) and the only implementation refuses. The benchmark in `app/bench/` measures a candidate
  adapter on its own; it does not install one, and a completed benchmark is not a voice capability. A configured adapter name other than
  `unavailable` is refused at startup, so a future adapter must arrive with a decision, not a
  config edit.
- **No holographic, VR, Ray-Ban or EDITH implementation.** EDITH Glass is `not-a-target`; surfaces
  are `boundary-only` with `implemented: false`. AETHERION is untouched.
- **No natural-language task entry claim.** Tasks take an explicit title, note and due time. Turning
  a spoken sentence into that structure needs the speech adapters and a model; both are absent.

## 13. Owner inputs still required

Nothing here decides these, and the server keeps reporting them as open:

- **Q13, Q15, Q17–Q26** remain open (`docs/product/capability-specification.md` §5). Q16 is answered
  (local-only) and is not reopened.
- The hardware/workload baseline for gate **G-O1**, the budget, and the language priorities
  (PR-1) — needed before any model or speech adapter can be selected.
- The account and device inventory, the smart-home inventory, and EDITH hardware requirements —
  none may be invented by the implementation.
- Explicit, per-service approval before `web_retrieval`, `telephony`, `device_vendor_cloud` or
  `update_downloads` is enabled; each needs its own scope, adapter and audit path first.
