# Repository audit — JARVIS (performed 2026-10-17)

> A pre-release verification and security audit of the Genesis 0.1 release (2026-10-17)
> is recorded separately in [PRE_RELEASE_AUDIT.md](PRE_RELEASE_AUDIT.md).

This audit records what actually existed before Genesis 0.1 work began, how it was
verified, and what was wrong with it. It is evidence-based: every finding below was
reproduced with a command, not inferred from file names.

## 1. Repository state found

| Item | Finding |
|---|---|
| Default branch `main` | 1 commit (`210379a Initial commit`): `README.md` (2 lines) and a Python `.gitignore`. No code. |
| Remote branch `feat/jarvis-core-v0.2` | 3 commits (`8fb69be`, `57fa036`, `54b9e6b`), ~880 lines: the only implementation. Open **draft PR #1**. |
| Checkpoint `81c2e8b` | **Does not exist** in this repository (checked locally and after fetching all remote refs). |
| Uncommitted changes | None. |
| CI | GitHub Actions workflow (Python 3.10–3.13). **Every run "failed" without executing**: the job annotation reads *"The job was not started because recent account payments have failed or your spending limit needs to be increased."* The PR's tests had therefore never run anywhere. |
| Visibility / licence | Private repository; no licence file. |

Decision: build on PR #1 rather than `main` (see ADR-0001). The session branch was
fast-forwarded to `54b9e6b`, preserving the three original commits and their authorship.

## 2. Actual architecture of Core v0.2 (as found)

Language: Python ≥3.10, **standard library only** (deliberate: Android/Termux target).
Persistence: one SQLite file (`~/.jarvis/jarvis.sqlite3`), WAL mode.

| Module | Responsibility (verified by reading and running) |
|---|---|
| `config.py` | `Settings` dataclass from env / `.env`. |
| `db.py` | `Store`: `projects`, `tasks`, `memories` (UNIQUE(category,key) upsert), `audit`. |
| `security.py` | `Policy.decide`: deny dangerous; network off by default; writes need explicit intent. |
| `tools.py` | `ToolRegistry`: exact-schema validation, 8 deterministic tools, audit before/after, AST-allowlist calculator. |
| `provider.py` | `GeminiProvider` over `urllib`, key in the URL query string. |
| `core.py` | `Jarvis.ask`: offline canned reply, or one Gemini call; JSON tool calls returned as proposals. |
| `cli.py` | argparse CLI + loopback-only `http.server` JSON API. |
| `tests/test_core.py` | 14 tests. |

## 3. Baseline (actually executed)

Environment: Linux x86_64, Python 3.11.2, SQLite 3.40.1 (FTS5 available).

```
$ python -m pytest -v          # on a pristine export of 54b9e6b
14 passed in 0.10s
$ python -m compileall -q src  # OK
```

This was the first recorded execution of the v0.2 suite. Python 3.10/3.12/3.13 could
not be executed in this sandbox (interpreter downloads blocked) — **not verified**.

Exercised by hand (CLI and HTTP): `init`, `status`, `project add`, `task add`,
`memory save/search`, `ask` (offline), `serve` + `GET /health`, `GET /v1/status`,
`POST /v1/ask`, invalid JSON → 400, non-loopback bind refused. All worked as documented,
except the defects below.

## 4. Defects found (each reproduced)

| # | Defect | Evidence | Status |
|---|---|---|---|
| D1 | **Memory retrieval failed for natural questions.** `search_memories` used one `LIKE '%<entire query>%'`; `Jarvis.context()` passed the whole user message, so the model almost never received relevant memories. | `memory search "what voltage does the solar controller use"` → `[]` with a matching memory stored. EXP-001: Recall@5 = **0.033**, empty results for 96.7% of queries. | Fixed (FTS5/BM25; Recall@5 0.933). |
| D2 | **Corrections destroyed history.** Upsert overwrote the value; no provenance of the previous state. | Saved 24V → 12V → 48V: one row remained (`'Correction: it is 48V'`). | Fixed (supersession chain). |
| D3 | **No schema versioning or migrations** (`user_version` = 0, `CREATE TABLE IF NOT EXISTS` only). | `PRAGMA user_version` → 0. | Fixed (migration framework + backup). |
| D4 | **`repr(Settings)` printed the API key** (default dataclass repr). | Inspection of `config.py`. | Fixed (`field(repr=False)`, `describe()`). |
| D5 | **`python -m jarvis …` (the README quick start) did not work**: no `__main__.py`. | `No module named jarvis.__main__`. | Fixed. |
| D6 | **Local API was reachable cross-site**: `POST /v1/ask` accepted `text/plain` bodies and any `Host`/`Origin`, so any web page could trigger model calls (CSRF) and DNS-rebinding was possible. | Code inspection; demonstrated fixed behaviour with curl (415/403). | Fixed (Host/Origin/Content-Type checks). |
| D7 | **Connections never closed** (`with sqlite3.connect()` commits but does not close). Python 3.13 emits ResourceWarnings for this. | Code inspection; regression test added. | Fixed. |
| D8 | **Audit rows written in a separate transaction** from the change they describe; a crash between them loses the record. | Code inspection (`add_project`, `add_task`, …). | Fixed (same transaction). |
| D9 | **Gemini API key sent in the URL**; Google documents the `x-goog-api-key` header. | `provider.py`; Gemini API reference. | Fixed (header). |
| D10 | **README claimed "workspace file listing/reading"; only listing existed.** | Tool list in `tools.py`. | Fixed (`workspace.read`, credential files refused). |
| D11 | **Conversation was not recorded at all**: no session or episodic history, so nothing carried across sessions except manual memories. | `ask()` wrote nothing on success. | Fixed (experience log, sessions). |
| D12 | **Tasks could be marked done with no evidence and any transition was allowed** (e.g. cancelled → done). | `set_task_status` accepted any listed status. | Fixed (state machine + evidence). |

## 5. Reusable components (kept and extended)

Kept: the stdlib-only stance, SQLite store and its v0.2 public methods, `Policy`
semantics and reason strings, `ToolRegistry` exact-schema validation, `safe_calculate`,
`_safe_args` redaction idea, Gemini adapter, CLI commands and output formats, loopback
API endpoints, the original test file (unchanged), docs structure, CI workflow.

Replaced internals (with justification in DECISIONS.md): memory table → versioned
`memory_items`; `audit` → unified `events`; `Jarvis.ask` → cognitive kernel cycle.

## 6. What was missing relative to the Genesis specification

Explicit cognitive state; identity; sessions; episodic log; provenance and epistemic
status; contradiction handling; goals; task dependencies; completion evidence; operation
journal and crash recovery; approval workflow; autonomy levels; emotional appraisal;
self-model; provider abstraction; evaluation harness. All of these now have a first
implementation — see STATUS.md for exactly what is verified.
