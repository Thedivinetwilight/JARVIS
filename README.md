# JARVIS

**A persistent, permissioned personal cognitive system** — it remembers what you tell it
(with provenance and corrections), tracks goals and tasks with evidence, keeps an auditable
record of everything it does, and picks up where you left off. Standard library only;
declared for Python 3.10+ (so far executed on 3.11 only — see STATUS); designed to run on a
laptop or Android/Termux (not yet run there).

> Release: **0.3.0 — Genesis 0.1 persistent cognitive kernel**, built on Core v0.2, plus the
> Mission 001 consolidation (scoped clients, reminders, plans, audio sessions, structured
> logging, default-deny egress), schema version 3.
> This is a tested foundation, not a claim of general intelligence or of feelings.
> Without a language model JARVIS still works (deterministically); with one configured it
> can also hold free-form conversation. See [docs/STATUS.md](docs/STATUS.md) for exactly
> what is verified.

## What it does today
- **Continuity:** persistent identity, sessions, and an experience log; `where were we?`
  rebuilds context after a restart (goals, progress, blockers, pending approvals,
  interrupted work, last session, recent ideas).
- **Memory you control:** `remember that …`, `idea: …`, corrections (`actually, …`,
  `correct #3 to …`) that keep history, conflicts kept visible, export, and `forget #N` /
  `forget my …` (with approval), which erases the memory and the copies JARVIS made of it
  from the database. Backups made earlier, exports and the storage hardware are outside its
  reach, and it says so — see [SECURITY](docs/SECURITY.md).
- **Goals and tasks:** `goal: … ; steps: a; b; c`, dependencies, blockers; a task is done
  only with your confirmation or a verified tool result.
- **Honest actions:** every operation is journaled before it runs and reported as
  requested / attempted / executed / observed / verified; crashes never turn into "success".
- **Safety:** network off by default; model suggestions wait for approval; irreversible
  operations need approval; secrets are redacted; the local API requires a token and
  rejects cross-site requests.
- **Emotional context:** stated feelings shape tone; guesses from wording are never
  asserted as facts; appraisals are private and expire.
- **Reminders:** `remind me to call mom in 2 hours` creates a persisted reminder; if you give
  no time it asks for one instead of guessing. Devices poll their own queue
  (`GET /v1/reminders/inbox`) and acknowledge; nothing is pushed.
- **Plans:** persisted multi-step plans (`jarvis plan list|show|execute`) run through the same
  permission-checked registry, retry transient failures a bounded number of times, pause for
  approval when a step is not allowed yet, and recover interrupted steps after a crash.
- **Scoped devices:** pair a phone or another machine with a single-use code; it receives a
  token limited to the scopes in its manifest, revocable immediately, stored only as a digest.
- **Default-deny egress:** every non-loopback socket connection is refused unless the code
  path declared exactly that destination (model providers are the only such path).
- **Models are optional and replaceable:** Gemini (opt-in, header auth) or any local
  OpenAI-compatible server such as Ollama.

## Quick start
```bash
python -m venv .venv && . .venv/bin/activate      # Termux can use its own Python
python -m pip install -e ".[dev]"
cp .env.example .env                               # optional; defaults are local + offline
python -m jarvis chat
```
```
you › goal: build the irrigation prototype; steps: choose sensors; design power budget; write firmware
jarvis › Goal #1 created: "build the irrigation prototype". Steps: #1 choose sensors; #2 design power budget; #3 write firmware.
you › remember that the irrigation pump draws 2A at 12V
jarvis › Noted as memory #2 (recorded as your statement; I haven't verified it independently).
you › done with task 1
jarvis › Marked task #1 done — your confirmation is recorded as the evidence. Goal #1: 1/3 done. Next: task #2 "design power budget".
you › task 2 is blocked by waiting for the pump datasheet
jarvis › Task #2 marked blocked (waiting for the pump datasheet). Goal #1: 1/3 done. Next: task #3 "write firmware".
--- next day, new process ---
you › where were we?
jarvis › Goal #1: "build the irrigation prototype" — 1/3 tasks done (33.3%).
  ✓ task #1 choose sensors (you confirmed)
  ✗ task #2 design power budget (blocked: waiting for the pump datasheet)
  · task #3 write firmware
Next: task #3 "write firmware".
```
(Transcript from a real run, lightly trimmed.)

Voice (Android/Termux with the Termux:API app): `jarvis voice` — listens, runs the same cycle,
speaks the answer (tested with simulated Termux commands; not yet on a real device).

Other commands: `jarvis ask "…"`, `resume`, `goal add|list|show`, `task add|list|status|depend|block`,
`memory save|search|correct|history|forget|conflicts|resolve|export`, `ops`, `approve N`,
`deny N`, `trace`, `events`, `state`, `capabilities`, `identity`, `doctor`,
`eval retrieval`, `pair code|list|revoke`, `reminder add|list|due`, `plan list|show|execute`,
`serve`.

The loopback API (`jarvis serve`) exposes `GET /health`, `/v1/status`, `/v1/state`, `/v1/goals`,
`/v1/tasks`, `/v1/operations`, `/v1/events`, `/v1/capabilities`, `/v1/manifests`, `/v1/clients`,
`/v1/memory/search?q=`, `/v1/reminders`, `/v1/reminders/due`, `/v1/reminders/inbox`,
`/v1/plans`, `/v1/plans/<id>`, `/v1/audio/capabilities`, `/v1/audio/sessions/<id>`,
`/v1/cycles/<id>`, `POST /v1/ask`, `POST /v1/pairing`, `POST /v1/clients/pairing`,
`POST /v1/clients/<id>/revoke`, `POST /v1/reminders`, `POST /v1/reminders/inbox/<id>/ack`,
`POST /v1/plans`, `POST /v1/plans/<id>/execute`, `POST /v1/audio/sessions`,
`POST /v1/audio/sessions/<id>/audio|end`, `POST /v1/operations/<id>/approve|deny`.

Two kinds of bearer token are accepted: the operator token in `~/.jarvis/api_token`, and
scoped per-client tokens obtained by pairing. Every route except `GET /health` and
`POST /v1/pairing` needs a token; every route also needs the matching scope. JARVIS keeps its
data directory and files readable only by you.

Using a model: set `JARVIS_OPENAI_BASE_URL=http://127.0.0.1:11434/v1` and
`JARVIS_OPENAI_MODEL=<model>` for a local Ollama server, or `JARVIS_NETWORK_ENABLED=true`
plus `JARVIS_GEMINI_API_KEY` for Gemini. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Development
```bash
python -m pytest                     # 251 tests (15 acceptance tests in tests/acceptance)
python -m jarvis eval retrieval      # EXP-001 on the pinned evaluation set
python -m compileall -q src
```

## Documentation
[STATUS](docs/STATUS.md) · [AUDIT](docs/AUDIT.md) · [COGNITIVE ARCHITECTURE](docs/COGNITIVE_ARCHITECTURE.md) ·
[ARCHITECTURE](docs/ARCHITECTURE.md) · [DECISIONS](docs/DECISIONS.md) · [ACCEPTANCE](docs/ACCEPTANCE.md) ·
[EXP-001](docs/experiments/EXP-001-retrieval.md) · [RESEARCH](docs/RESEARCH.md) ·
[SECURITY](docs/SECURITY.md) · [CONFIGURATION](docs/CONFIGURATION.md) · [ROADMAP](docs/ROADMAP.md) ·
[PRE-RELEASE AUDIT](docs/PRE_RELEASE_AUDIT.md)
