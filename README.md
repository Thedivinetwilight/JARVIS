# JARVIS

**Personal AI operations system** — persistent project memory, task orchestration, a permissioned tool runtime, audit trails, and model-provider abstraction. Built to run on Python 3.10+ including constrained environments such as Android/Termux.

> Current release target: **Core v0.2**. This branch adds a real, testable foundation, not a claim of autonomous general intelligence. Provider calls and external integrations require explicit configuration.

## What is implemented
- SQLite-backed projects, tasks, memories, and append-only audit events.
- Centralized permission policy with deny-by-default execution controls.
- Deterministic tools: time, calculator (AST allowlist), project/task/memory operations, and workspace file listing/reading.
- Gemini REST provider using the Python standard library; no mandatory SDK or native dependency.
- CLI for initialization, project/task management, memory, status, and assistant turns.
- Explicit separation between model proposals and tool execution.
- Unit tests and a local configuration path that works without cloud access.

## Quick start
```bash
python -m venv .venv
# Android/Termux can use its existing Python environment if venv setup is inconvenient.
. .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
python -m jarvis init
python -m jarvis status
python -m jarvis project add AXIOM "Adaptive eXecutive Intelligence Orchestration Machine"
python -m jarvis task add "Inspect current integration failure" --project AXIOM
python -m jarvis serve
```

Open `http://127.0.0.1:8765/health` for the local health endpoint. The API binds to loopback by default.

## Configuration
Settings are read from environment variables and optional `.env`:
- `JARVIS_HOME`: data directory (default `~/.jarvis`)
- `JARVIS_WORKSPACE`: root for explicitly permitted file tools (default current directory)
- `JARVIS_NETWORK_ENABLED`: default `false`
- `JARVIS_GEMINI_API_KEY`: provider key; never commit it
- `JARVIS_GEMINI_MODEL`: default `gemini-2.5-flash`
- `JARVIS_HOST`: default `127.0.0.1`
- `JARVIS_PORT`: default `8765`

Cloud calls are unavailable unless network access is explicitly enabled and a key is configured. The core still works without a model provider.

## Safety model
- No arbitrary shell-execution tool is exposed to the model.
- Model output is untrusted input. Tool names and arguments are validated against registered schemas.
- Read-only tools are separate from write tools; write actions are gated by the central policy.
- Network access is disabled by default.
- Filesystem operations are restricted to the configured workspace and reject traversal.
- Secrets are loaded from environment/config and redacted from routine logs.
- Every tool decision and result is recorded in the audit log.
- This is a local developer foundation, not a hardened multi-user service. Keep the API on loopback; do not expose it to a LAN or the internet without authentication, TLS, and a security review.

## Development
```bash
pytest
python -m compileall src
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SECURITY.md](docs/SECURITY.md), and [docs/ROADMAP.md](docs/ROADMAP.md).

## Direction
1. Stable memory and project recovery.
2. Reliable tool execution and verification.
3. Human-controlled workflow orchestration.
4. Optional voice, desktop integrations, and specialized agents only after core reliability is measured.
