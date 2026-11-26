# Security model and current limits

## Controls (implemented and tested)
- **Network off by default.** Remote model endpoints are only used when
  `JARVIS_NETWORK_ENABLED=true`; plain-http endpoints are allowed only on loopback;
  base URLs with embedded credentials are rejected.
- **Data locality.** Remote models never receive personal or sensitive memories, earlier
  conversation episodes, or personal turns unless `JARVIS_ALLOW_SENSITIVE_TO_PROVIDER=true`.
- **Secrets.** API keys are sent in headers (Gemini: `x-goog-api-key`), never in URLs;
  configured keys and credential-shaped text (API keys, tokens, private keys,
  "password is …") are redacted before anything is stored, logged or returned; memory
  writes containing credentials are refused; `Settings` never prints secrets.
- **Policy enforced in code.** `Policy.decide` is a pure function of risk, origin,
  approval state, autonomy level and configuration; it takes no model output or emotional
  appraisal. Dangerous tools are never executable. Model- or kernel-proposed writes become
  proposals that need user approval; irreversible operations (e.g. `memory.forget`) need
  approval even when the user asked directly.
- **Untrusted content.** Model output, retrieved memories, files and tool responses are
  data. Context sent to models is labelled "data, not instructions"; proposals are
  validated against exact schemas; unknown tools are rejected.
- **Filesystem.** Workspace tools resolve paths and require containment; `workspace.read`
  refuses `.env*`, keys/certificates, `.git`, `.ssh`, credential/secret-named files,
  databases, binary and non-UTF-8 files, and files over 64 KiB; content is redacted.
- **Local API.** Loopback-only bind (127.0.0.1, ::1 or localhost; anything else is refused);
  every route except `GET /health` needs `Authorization: Bearer <token>`. HEAD, PATCH, PUT,
  DELETE and OPTIONS are refused (501/405) without returning data or acting. The token is 32
  random bytes in `<JARVIS_HOME>/api_token` (mode 0600), compared in constant time, never
  printed, and redacted if it is pasted into a conversation. Loopback alone is not an access
  control, because other OS users and, on Android, other apps can connect to 127.0.0.1. `Host` must be
  loopback (DNS-rebinding defence); any `Origin` must be loopback (`null` is refused); POST
  bodies must be `application/json`; CORS preflights are never granted; bodies ≤16 KiB;
  `no-store`; request logging disabled; model proposals are never executed by `/v1/ask`, and
  approving them needs the token. Verified with real HTTP against `jarvis serve`.
- **Private data files.** Directories JARVIS creates are mode 0700. The database (and its WAL
  and SHM, which inherit its mode), migration backups and exports are 0600; an existing
  world-readable database is tightened on open. Directories you created yourself are left as
  they are and reported by `jarvis doctor`. Without this, the default umask (022) would let
  every other account on a shared machine read all memories directly.
- **Integrity.** Migrations back up first and run transactionally; newer schemas are
  refused; integrity check on open; operations are journaled before execution and
  interrupted ones are never assumed successful. State transitions that depend on an
  earlier read (approve, deny, cancel) are conditional inside the write transaction, so a
  concurrent caller can never overwrite an executed operation's outcome.
- **Erasure (`forget`, approval required).** Policy in `src/jarvis/erasure.py`. In one
  transaction: every version is tombstoned (value, key, evidence overwritten); the turns
  that created or corrected it, JARVIS's replies that used or quoted it, the user's
  questions about it and the forget request are erased; linked event payloads and journal
  entries lose their content fields; pending proposals that target it are cancelled; derived
  copies (session summaries, JARVIS text, journal rows, v0.2 audit rows) are scrubbed by
  verbatim match — multi-word phrases as substrings, single words only structurally, so
  unrelated history is not erased on a keyword; FTS5 segments are merged. Afterwards the WAL
  is truncated. A conversation cycle still running when the forget commits withholds its
  reply instead of quoting the forgotten memory or storing it.
  The journal never stores memory or file content in the first place. Verified on isolated
  databases by scanning every table (including FTS5 shadow tables), FTS `MATCH`/vocabulary
  and the raw database and WAL bytes, after reopen and restart.

### What deletion does and does not guarantee
1. App-visible memory: erased (search, recall, episodes, resume, API).
2. Active records and indexes: erased.
3. Recoverable bytes in the database and WAL: erased in the tested cases, via `secure_delete`
   (freed cells and pages are zero-filled) and FTS `optimize` — FTS5's own `secure-delete`
   needs SQLite ≥ 3.42 (tested build: 3.40.1). If another connection blocks the WAL
   checkpoint, the report says so; SQLite deletes the WAL when the last connection closes.
4. Backups and external copies: **not erased**. Pre-migration backups and exports keep the
   data; the forget report lists backups that may contain it. Copies already sent to a
   remote model provider cannot be recalled. This is possible only with network mode on,
   and personal or sensitive memories are withheld from remote models by default.
5. Physical storage: **not guaranteed**. Deletion is logical plus in-file overwrite, not a
   secure erase of the device (SSD remapping, filesystem journals, swap, snapshots).
Also retained on purpose: the person's own messages that are not linked to the memory but
mention it, verbatim or by sharing distinctive words (for example a partial restatement).
They are counted and their event ids reported, not deleted on a word match, and the reply
warns that they can still come up in conversation. Restatements in different words cannot
be detected.

## Known limitations
Single-user local prototype. The HTTP API uses one static per-installation token and no
TLS, so it must stay on loopback; any process running as the same OS user can read the
token (and the database itself). On Windows, POSIX modes do not apply; data files rely on
the user profile's ACLs. Redaction is pattern-based and cannot recognise arbitrary
private information. Backups made before a deletion still contain the deleted data. Events
are append-only by convention, not cryptographically chained; `forget` deliberately
rewrites the content of linked events. Path checks cannot defeat a hostile local
process racing symlinks. Timed-out in-process handlers keep running in a daemon thread
until they finish (their outcome is reported as unknown). Do not enable network mode on an
untrusted device.

## Required before wider deployment
1. Per-client credentials and CSRF protection for any browser UI; TLS if ever off-loopback.
2. OS credential store instead of `.env`.
3. Tamper-evident (hash-chained) event log and backup encryption/retention policy.
4. Subprocess/container sandbox with resource limits before any code execution (R5).
5. Dependency pinning and vulnerability scanning in CI (blocked on CI billing).
6. Security tests for symlink races and concurrent writers.


## Mission 001: scoped clients, egress and audio (2026-11-26)

### Two token systems
| Token | Where it lives | What it grants | Rotation |
|---|---|---|---|
| legacy static token | `<JARVIS_HOME>/api_token` (0600) | operator-equivalent access to every route | delete the file |
| scoped client token (`jvs_<client>.<body>`) | shown once to the client at pairing; the server keeps only an HMAC-SHA256 digest | only the scopes in that client's manifest | `jarvis pair revoke <client_id>` |

A token is created only by pairing: the operator runs `jarvis pair code` (or
`POST /v1/clients/pairing`), the device redeems it with `POST /v1/pairing`. Codes are
single-use, expire (default 10 minutes), and redemption is throttled after 10 failures in
10 minutes. Device tokens **cannot** be issued with admin scopes (`clients.read`,
`clients.revoke`, `clients.pair`, `audit.read`) or `plans.write`; attempt returns 400.
The signing secret is a random value in `<JARVIS_HOME>/server_secret` (0600), generated on
first start, never printed, and registered with the redactor.

### Route → scope
Every route's required scope lives in one table in `api.py` (`required_scope`); an unknown
route is treated as needing `status.read` rather than being implicitly public.
`GET /health` and `POST /v1/pairing` are the only public routes; both still require the
Host/Origin checks. Authentication without the scope is 403 `scope_denied`.

### Egress
`egress.EgressGuard` replaces `socket.socket.connect` and `socket.create_connection` so that
any library (urllib, http.client, a future adapter) is subject to the same rule: loopback is
allowed, everything else is denied unless the caller opened
`GUARD.declare(service, host, port)` for that exact destination. The guard is installed when
`Jarvis` is constructed, so it also applies to the CLI, not only to `jarvis serve`. Model
provider calls are wrapped in a single-destination declaration and a blocked call surfaces as
a `ProviderRefused` error (never a silent success). Declarations are thread-local. There is no
environment variable that disables the guard.

### Audio
Audio sessions store raw bytes locally with per-chunk (256 KiB), per-session (10 MiB) and TTL
(default 30 minutes) limits. Revoking a client deletes all of its chunks in the same
transaction that marks the sessions purged. There is **no** speech recognition or synthesis:
`/v1/audio/capabilities` reports both as false, and the manifests mark both capabilities
unavailable. `forget` does not textually scan audio bytes and says so.

### Reminders and plans
A reminder is queued only for the client named at creation and delivered only when that client
polls; there is no push channel. Plan execution runs through the tool registry, so policy
applies per step: a plan executed under a model proposal pauses in `awaiting_approval` for
writes and resumes only after the operation was approved. Retries are bounded
(`MAX_RETRIES_PER_STEP = 2`, `MAX_STEPS = 12`) and a denied or validation failure is never
retried.

### Remaining risks (unchanged in nature, newly relevant)
- Same-user processes can read the token files and the database; the API boundary is only as
  strong as file permissions.
- The API still has no TLS; it must stay on loopback.
- Pairing codes are short and human-entered; the throttle is in-process, so restarting the
  server resets it. A paired device's first token is trusted until revoked.
- Audio blobs are the largest personal data this build stores; their TTL is the only retention
  control.
