# Configuration reference

Read from environment variables, optionally from `.env` in the working directory
(existing environment variables take precedence). Invalid values stop start-up with an
error naming the variable (values are never echoed). `jarvis doctor` prints the
effective configuration with secrets shown only as `set`/`unset`.

| Variable | Default | Validation / meaning |
|---|---|---|
| `JARVIS_HOME` | `~/.jarvis` | data directory (database `jarvis.sqlite3`, `backups/`, API token `api_token`). Created with mode 0700; the database, its WAL/SHM, backups and exports are 0600. An existing directory keeps its mode (`jarvis doctor` reports it). |
| `JARVIS_WORKSPACE` | `.` | primary read-only workspace root |
| `JARVIS_WORKSPACE_ROOTS` | empty | extra workspace roots, `os.pathsep`-separated (e.g. `/home/me/notes:/home/me/projects`). Duplicates are collapsed; every root is resolved and must exist as a directory to be scanned. |
| `JARVIS_ALLOWLIST` | empty | comma-separated **bare hostnames** for `web.get` / `web.summarize` (e.g. `example.com,docs.python.org`). No schemes, paths, ports or wildcards; subdomains of a listed host are allowed. Empty = the retrieval tools are not registered at all. |
| `JARVIS_WEB_ALLOW_LOOPBACK` | `false` | authorise loopback targets and non-standard ports (local testing only) |
| `JARVIS_WEB_MAX_BYTES` | `524288` | response cap, 1 B–4 MiB |
| `JARVIS_WEB_TIMEOUT` | `8` | seconds, 0.5–60 |
| `JARVIS_SCHEDULER_INTERVAL` | `15` | seconds between scheduler ticks in `jarvis serve`, 0–3600; `0` disables the background loop (ticks then only happen via `jarvis schedule tick` or `POST /v1/schedules/tick`) |
| `JARVIS_PRINCIPAL_NAME` | `Sivaram` | the person JARVIS serves (charter, prompts) |
| `JARVIS_NETWORK_ENABLED` | `false` | required for any non-loopback provider |
| `JARVIS_PROVIDER` | `auto` | `auto` · `none` · `gemini` · `openai_compatible`; `auto` = Gemini if key (+network) else OpenAI-compatible if base URL set |
| `JARVIS_PROVIDER_TIMEOUT` | `30` | seconds, 0.1–600 |
| `JARVIS_GEMINI_API_KEY` | empty | secret |
| `JARVIS_GEMINI_MODEL` | `gemini-2.5-flash` | model name (availability not verified) |
| `JARVIS_GEMINI_BASE_URL` | Google endpoint | https, or http on loopback only |
| `JARVIS_OPENAI_BASE_URL` | empty | e.g. `http://127.0.0.1:11434/v1` for Ollama |
| `JARVIS_OPENAI_MODEL` | empty | required with a base URL |
| `JARVIS_OPENAI_API_KEY` | empty | secret; optional for local servers |
| `JARVIS_AUTONOMY_LEVEL` | `2` | 0–3 implemented; 4–5 rejected |
| `JARVIS_ALLOW_SENSITIVE_TO_PROVIDER` | `false` | allow personal/sensitive memories in model context |
| `JARVIS_AFFECT_RETENTION_DAYS` | `30` | 0–3650; 0 = appraisals expire immediately |
| `JARVIS_SESSION_IDLE_MINUTES` | `30` | idle time before a new session starts |
| `JARVIS_HOST` / `JARVIS_PORT` | `127.0.0.1` / `8765` | API bind (`127.0.0.1`, `::1` or `localhost`); other hosts are refused. Every route except `GET /health` needs `Authorization: Bearer <token>`; the token is created on first `jarvis serve` in `<JARVIS_HOME>/api_token` (mode 0600). Delete the file to rotate it. |
| `JARVIS_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`; JSON-lines records on stderr (redacted) |

Files JARVIS creates in `<JARVIS_HOME>` besides the database: `api_token` (legacy operator
token, 0600) and `server_secret` (HMAC signing key for scoped tokens, 0600). Both are
recreated on demand if deleted; deleting `server_secret` invalidates every paired client
token, deleting `api_token` rotates the legacy operator token.

Provider retry and fallback are deliberately not configurable environment variables in this
build: `ResilientProvider` retries transient errors twice with exponential backoff
(0.5 s → 8 s) and then falls back to the next configured provider.

Development vs production: there is no separate production mode yet; the defaults are the
safe (local, offline, approval-gated) configuration.

Reproducible setup:
```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
```
