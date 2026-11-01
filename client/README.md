# J.A.R.V.I.S. Windows PC companion

The first client for the local J.A.R.V.I.S. server: it pairs with **your own** server on your own
machine or home network, polls that server for reminders that were **explicitly routed to this PC**,
and shows them as **Windows notifications**. Nothing else.

- No chat box, no message thread, no text input of any kind.
- No speech recognition and no speech output; both are reported unavailable, because neither exists.
- No cloud, no vendor push service, no account, no telemetry, no update check.
- No second recipient: this client receives only what the server routed to it.

**Status: source complete, never run on Windows.** The development and CI hosts for this repository
are not Windows, so no toast has been raised, no Credential Manager entry has been written and no
sleep/resume has been observed on a real PC. Section 7 is the checklist that has to pass before
anyone may call this client working. Until then its device-matrix status says exactly that.

## 1. What it needs

| Requirement | Detail |
| --- | --- |
| Windows | Windows 10 or 11. **No version has been tested** — see §7. No Windows version is claimed as supported. |
| Python | 3.10 or newer, already installed, with `tk`-free standard library only. |
| The server | A reachable J.A.R.V.I.S. server (`app/`), with `reminders.display_timezone` configured — the client refuses to render times without it. |
| Notifications | Windows notifications enabled for the app id `JARVIS.Companion` (Settings → System → Notifications). |
| Optional | `winsdk` gives the primary toast backend; without it the client uses PowerShell's built-in WinRT projection. `pip install winsdk` is the **only** optional network step, and it is not required. |

No dependency on any package index is needed for a normal install: `install.ps1` creates a private
environment with `--without-pip` and copies the package into it.

## 2. Install

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

It creates `%LOCALAPPDATA%\JARVIS\Companion\{venv,bin}`, copies the package, writes the launcher
`bin\jarvis-companion.cmd` and registers the per-app notification id under
`HKCU\Software\Classes\AppUserModelId\JARVIS.Companion`.

It installs **no** service, scheduled task or autostart entry: whether the companion runs at login is
a decision the owner has not made (see §8). Run it yourself, or start it from a shortcut you create.

## 3. Pair

On the **server console** (never over HTTP), mint a single-use code with the narrowest scopes:

```bash
python3 -m app.jarvis pair-code --config <server config> \
    --label-hint "owner PC" --platform pc --scopes reminders.receive,status.read
```

On the PC:

```powershell
jarvis-companion pair --server http://127.0.0.1:7420 --code ABCD-EFGH-JKLM
```

The token is shown once by the server, written straight into **Windows Credential Manager**
(`JARVIS/companion/<host>:<port>`) and never appears in a file, a log line or the configuration. The
configuration file at `%LOCALAPPDATA%\JARVIS\Companion\companion.json` holds only the server URL, the
cadence, the label and the client id.

Plaintext `http://` is refused for any host other than this machine, because the device token is a
bearer credential. Use `https://` across the network (the server supports TLS), or
`--allow-insecure-http` on a link you already trust.

## 4. Run

```powershell
jarvis-companion run            # poll until stopped (Ctrl+C)
jarvis-companion run --once     # a single poll
jarvis-companion status         # configuration, credential, notification state, server, journal
jarvis-companion doctor         # every platform check, including the ones that are unverified
```

The cadence is **10 seconds by default and can never be slower**: `--poll-seconds` accepts 1–10 and
refuses anything above, because a slower cadence could not meet the owner's 10-second surfacing
target by construction. Polling is the only transport — the server never pushes.

**Polls are not delivery attempts.** The server counts an attempt per retry interval, not per request,
so a 10-second cadence does not consume the attempt budget: §9 of `docs/architecture/reminder-delivery.md`
describes the rule and `client/tests/test_integration_loopback.py` proves it against the real server.

## 5. Acknowledge, revoke, remove

```powershell
jarvis-companion ack --last                       # the reminder shown most recently
jarvis-companion ack dlv_1a2b3c4d5e6f             # a specific delivery
jarvis-companion revoke                           # ask the server to revoke, then delete the credential
jarvis-companion forget                           # delete the local credential only
powershell -ExecutionPolicy Bypass -File uninstall.ps1
```

A notification is **not** an acknowledgement. Showing a reminder leaves it `delivered` on the server,
where the attempt and expiry rules still apply; acknowledging is an owner action that closes it for
good and cancels the same reminder on any other target. If an acknowledgement cannot reach the server
it is queued (`stage: "queued_offline"`), retried on the next poll, and dropped with a reason if the
server has already closed the delivery (`stage: "delivery_closed"`).

`revoke` is the tidy path: the server marks the client revoked, closes its queued events, and then the
local credential is deleted. `forget` deletes only the local credential — the server still has the
client and the token stays valid until it is revoked there.

## 6. Security and privacy, stated plainly

| Question | Answer |
| --- | --- |
| Where is the token? | Windows Credential Manager, `CRED_TYPE_GENERIC`, `CRED_PERSIST_LOCAL_MACHINE`, written as UTF-16LE and protected by the owner's logon. Not in a file, log, command line or environment variable. |
| What is on disk? | `companion.json` (server URL, cadence, label, client id) and `journal.sqlite3` (delivery ids, timestamps, verdicts, ack state). **No reminder titles or notes** — the schema has no column for them. |
| What leaves the machine? | Nothing. The client talks only to the configured server, and only on `http(s)`; environment proxies are ignored, redirects are refused, cookies are never sent. |
| Audit | Every server-side hand-over is already in the server's audit trail; the client records the four stages locally (below) and logs ids, never contents. |
| Permissions | The pairing code should grant `reminders.receive` and `status.read`. Without `reminders.receive` the client refuses to keep the pairing. |
| Revocability | `revoke` (client), the server console (`python3 -m app.jarvis revoke --client-id …`), or deleting the Credential Manager entry. |

**Four stages, never conflated.** The client reports each separately, and never claims a later one:

1. `handed_over` — the server returned the event in this client's inbox;
2. `received` — the client parsed it and journalled it;
3. `os_accepted` — the OS notification API accepted the display request (or `denied` / `failed` /
   `unsupported`); whether a toast was actually drawn is the OS's business and is not claimed;
4. `acknowledged` — the owner acted and the server confirmed it.

## 7. Windows integration checklist — all of it unverified

Run these on a real Windows PC and record the results before claiming the client works. Nothing below
has been executed in this repository.

| # | Check | Expected |
| --- | --- | --- |
| 1 | `install.ps1` on a clean profile | private venv created, launcher written, `HKCU` app id registered |
| 2 | `jarvis-companion pair` | token stored in Credential Manager, `companion.json` has no token |
| 3 | `jarvis-companion doctor` | `credential_store` ok, `os_notifications` ok, `paired` ok, `display_zone` ok |
| 4 | A reminder routed to this PC, due now | toast appears within 10 s while the app runs |
| 5 | Notifications disabled in Settings | verdict `denied`, no toast, no acknowledgement, `status` says denied |
| 6 | Re-enable notifications, next poll | the same reminder is shown once, and only once |
| 7 | Two identical inbox payloads | exactly one toast |
| 8 | Laptop sleep/resume | next poll happens immediately and is logged `resumed_after_gap` |
| 9 | Server stopped mid-run | `server_unreachable`, exponential backoff ≤ 60 s, recovery without restart |
| 10 | `jarvis-companion ack --last` while the server is down | `queued_offline`, then confirmed after the server returns |
| 11 | Revoke from the server console | next poll stops with `token_revoked`; the credential is not silently reused |
| 12 | `uninstall.ps1` | launcher, environment, journal, app id and credential entry gone |
| 13 | Server in a non-local zone | the toast shows the server's zone name, not this PC's |

What the tests in `client/tests/` do establish is everything that does not need Windows: the real HTTP
contract against the real server, cadence, deduplication, acknowledgement policy, offline queuing,
revocation, time-zone handling and local-only behaviour.

## 8. Deliberately not decided here

Following `docs/product/reminder-delivery-decision.md` Part D, the client does not decide the owner's
open questions. It installs no autostart, schedules no notifications of its own, supports no other
platform, and offers no task list, audio capture or voice. Where it must behave and the question is
open, it does the smaller thing and says so.

## 9. Exit codes

| Code | Meaning |
| --- | --- |
| 0 | ok — for `status` and `doctor` this only means the report was produced; read `ok`, `failing` and `unverified` inside it, because `doctor` exits 0 even when it is describing a broken setup |
| 2 | usage or configuration (bad cadence, bad URL, insecure transport) |
| 3 | not paired (no configuration, or no credential) |
| 4 | no OS-protected credential store on this host |
| 5 | server unreachable |
| 6 | authentication refused (expired, revoked, missing scope, pairing refused) |
| 7 | protocol problem (server answer not understood, or no display zone) |
| 8 | the OS notification path refused or is unavailable |

## 10. Where the code is

```
client/
├── install.ps1, uninstall.ps1      offline installer and remover
├── pyproject.toml                  packaging metadata; no runtime dependencies
├── src/jarvis_companion/
│   ├── api.py                      HTTP: one host, no redirects, no proxies, typed failures
│   ├── cli.py                      pair · run · status · doctor · ack · revoke · forget
│   ├── config.py                   server, cadence (≤10 s), no secrets
│   ├── credentials.py              Windows Credential Manager, or an honest refusal
│   ├── notifier.py                 the notification boundary and its verdicts
│   ├── poller.py                   cadence, dedup, resume, ack policy
│   ├── service.py                  wiring: paths, store, journal, backend
│   ├── state.py                    journal: ids, stages, verdicts — never contents
│   └── windows_notify.py           toast backends (winsdk, then PowerShell), app-id registration
└── tests/                          125+ tests, all runnable without Windows
```
