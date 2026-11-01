# Windows PC companion — the first client

- **Status:** source complete; **never executed on Windows**. No toast has been raised, no Credential
  Manager entry written, no sleep/resume observed. Every Windows-specific claim below is design intent,
  checked against the API surface, not against a running PC.
- **What it is:** the client half of the reminder queue described in
  [reminder-delivery.md](reminder-delivery.md). It pairs with the local server, polls its own inbox,
  shows the events the server routed to it as **Windows notifications**, and acknowledges one only when
  the owner acts.
- **What it is not:** a chat client, a voice client, a cloud client, or a second recipient. It contains
  no speech recognition, no speech output, no text input and no conversation view.
- **Governed by:** [the decision record](../product/reminder-delivery-decision.md) Part D, which is the
  minimum contract DR-1, DR-7, DR-8, DR-9 and DR-10 imply. Where that record says a question is open
  (autostart, installer, code signing, other platforms), the client does not decide it.
- **Code:** `client/` — package `jarvis_companion`, installer `client/install.ps1`, tests `client/tests/`.

## 1. The four stages, and why they are separate

The owner asked for one thing above all: never say a reminder was shown when it was not. The client
therefore reports four stages that are never collapsed into one word.

| Stage | Produced by | What it means | What it does not mean |
| --- | --- | --- | --- |
| `handed_over` | the server | the event is in **this client's** queue and was returned in an inbox response | that any device received it |
| `received` | the client | the response was parsed and journalled | that anything was displayed |
| `os_accepted` | the OS notification API | the display request was accepted (**or** `denied` / `failed` / `unsupported`) | that a human saw a toast; the OS decides that |
| `acknowledged` | the owner, then the server | the owner acted and the server confirmed it | that the reminder was shown before the owner acted |

A toast is raised **after** `received`, and the acknowledgement is sent **only** from an explicit owner
action (`jarvis-companion ack`). Showing is not acknowledging: the server closes the delivery on
acknowledgement and cancels the same reminder on any other target, which is not a decision a
notification may make on the owner's behalf.

## 2. Polling, cadence and what a poll is not

- One `GET /v1/reminders/inbox?limit=50` per tick, then one flush of queued acknowledgements.
- Cadence is configuration, capped at **10 seconds** — `poll_seconds` accepts 1–10 and refuses more.
  A slower value would miss the owner's surfacing target by construction, so it is a configuration
  error rather than a slow client.
- **A poll is not an attempt.** The server counts an attempt per `retry_interval_minutes`, not per
  request: repeated reads inside the interval return the same attempt number, so a 10-second client
  does not consume its own delivery budget. `client/tests/test_integration_loopback.py` asserts this
  against the real server over six consecutive polls.
- **Sleep and resume.** The loop compares the monotonic clock against the previous tick. A gap longer
  than 2.5 × the cadence is reported as `resumed_after_gap`, the next tick runs immediately, and no
  backoff is invented for a machine that was asleep. A sleeping client is not "online", and the events
  that came due meanwhile are still due.
- **Offline.** A transport failure is transient: exponential backoff from 2 × interval, capped at 60
  seconds, reset on the first success. The loop never exits because the server was busy, and never
  spins when it is down.
- **Fatal conditions stop the loop with a reason**: `token_expired`, `token_revoked`, `scope_denied`,
  `display_zone_missing`, `protocol_error`. Retrying cannot fix any of them, so pretending to poll
  would only produce a client that looks alive and shows nothing.

## 3. Time: the server's zone, never this PC's

Reminder times arrive already rendered (`fire_at_local.iso`, `fire_at_local.timezone`) alongside the
queue's `display_timezone`. The client shows **that** rendering, and it refuses three situations:

- the inbox published no `display_timezone` → `DisplayZoneMissing`, no notification, run stops with the
  reason (the server requires the setting, so this is a configuration fault worth surfacing);
- an event's `fire_at_local.timezone` differs from the published zone → `ProtocolError`, no
  notification: a time that disagrees with the server is worse than no time;
- an event missing a field the client would have to guess → `ProtocolError`, no notification.

The PC's own time zone is never consulted. If the owner's PC is set to a different zone from the
server's display zone, the notification carries the zone name, so the two cannot be silently confused.

## 4. Deduplication and the journal

`journal.sqlite3` holds one row per delivery: ids, timestamps, the OS verdict, the show-attempt counter,
and the acknowledgement state. It has **no column for a title or a note**, so no future code path can
quietly start storing what the owner was reminded about.

Decisions the journal makes:

- a delivery whose verdict is `accepted` is never offered to the OS again — a second toast for one
  reminder is a duplicate, not a retry;
- a `failed` or `denied` delivery is retried up to **three** times across ticks, then left alone and
  reported as such; permission denied is not a reason to hammer the OS;
- the same rule survives a restart, because the journal is on disk: a client restarted five seconds
  later does not re-notify what it already showed;
- acknowledgements made while the server was unreachable are queued and flushed on the next tick, and
  dropped with a reason if the server has since closed the delivery (`delivery_closed`).

## 5. Credentials and what touches the disk

| Item | Where | Protection |
| --- | --- | --- |
| device token | Windows Credential Manager, one entry per server (`JARVIS/companion/<host>:<port>`) | generic credential, `CRED_PERSIST_LOCAL_MACHINE`, UTF-16LE, protected by the owner's logon |
| server, cadence, label, client id | `%LOCALAPPDATA%\JARVIS\Companion\companion.json` | no secret; unknown keys refused so a stray `token` cannot be added |
| journal | `%LOCALAPPDATA%\JARVIS\Companion\journal.sqlite3` | ids, stages, verdicts; no contents |
| toast text | memory only, then the OS | never logged, never written to the journal, never put on a command line |

On a platform without that store the client **refuses to hold a token** rather than writing one to a
file; there is no plaintext fallback, because the owner was told the credential is OS-protected. The
tests use an in-memory store for that reason and say so.

Removal has two shapes, deliberately: `revoke` tells the server first (closing this client's queued
events) and then deletes the credential; `forget` deletes only the local credential and says plainly
that the server still has the client until it is revoked there.

## 6. Local-only, and how it is enforced rather than promised

- One origin: every request is `{server_url}{path}`; the configuration refuses a URL with a path, a
  query or a fragment, so an API path cannot be smuggled into the host.
- **Redirects are refused**, not followed: a 302 cannot move the bearer token to another host. The test
  proves it by pointing a client at a redirector and asserting the target server received nothing.
- **Proxies are ignored**: the opener installs no `ProxyHandler`, so `HTTP_PROXY` in the environment
  cannot route the token through another machine. The test sets both proxy variables and asserts the
  proxy saw zero requests.
- No cookies, no referer, no language headers, `Connection: close`; the user agent says what it is.
- No telemetry, no update check, no vendor service, no push registration. The notification is raised
  **on this PC** by the OS's own facility; nothing is sent anywhere to make it appear.
- Static enforcement lives in `client/tests/test_boundaries.py`: standard library only, exactly one
  module may import the network stack, no vendor or model names anywhere, no UI assets, no speech
  code, and an installer that fetches nothing.

## 7. Windows notification backends

| Order | Backend | When it is used | What "accepted" means |
| --- | --- | --- | --- |
| 1 | `winsdk` WinRT bindings (`Windows.UI.Notifications`) | when the optional `winsdk` package is installed | `ToastNotificationManager.create_toast_notifier(...).show(...)` returned without raising |
| 2 | PowerShell WinRT projection | otherwise, on a stock Windows installation | the PowerShell host exited 0 after calling the same API |
| — | none | any non-Windows host, or notifications switched off | `unsupported`/`denied`; nothing is shown and nothing is claimed |

Both backends first ask the registry whether notifications are enabled (`ToastEnabled`, and the
per-app `Enabled` value under `HKCU`); a switched-off app id is reported as **denied** *before* the OS
is asked, which is why a denied permission never produces a phantom "shown". The app id
`JARVIS.Companion` is registered under `HKCU\Software\Classes\AppUserModelId` so the toast is
attributable and can be found in Settings.

A Start-menu shortcut carrying the same app id is **not** created: it needs COM interop that cannot be
exercised here, and inventing it would be a claim about Windows behaviour that nothing in this
repository has checked. It is listed in the unverified checklist, not silently assumed.

## 8. What is unverified, and what that costs

| Unverified | Consequence |
| --- | --- |
| any toast being displayed on Windows | the client's honest maximum claim is "the OS accepted the request" |
| Credential Manager round-trip on a real profile | the code path compiles and its constants are from `wincred.h`, but it has never run |
| sleep/resume on real hardware | resume detection is tested against a fake monotonic clock only |
| a Start-menu shortcut with the app id | not created; some Windows builds may show the notification without a name or icon |
| the install/uninstall scripts | written for Windows PowerShell 5.1+, never executed |
| Windows version floor | Windows 10 or 11 is the design target; **no version has been tested** |

The device matrix therefore keeps the PC row out of any "verified" or "supported" state, and the
server's capability statuses are unchanged: this client shows notifications, which is not voice, not a
capability claim, and not the 10-second surfacing target (which remains a target — measured server-side
hand-over delays are published per event, and client-side surfacing has still never been timed).

## 9. Tests, and what they actually cover

`client/tests/` (run with `python3 -m unittest discover -s client/tests -t client`) covers, without
Windows:

- the real HTTP contract against the genuine server over a loopback socket: pairing, narrow scopes,
  inbox, acknowledgement, idempotence, another client's delivery, closed deliveries, self-revocation;
- cadence: the configured interval is used exactly, a slower one is refused, and a gap becomes a resume;
- deduplication: within one payload, across ticks, and across a restart;
- acknowledgement policy: polling never acknowledges; an explicit acknowledgement does; offline
  acknowledgements queue and flush; closed deliveries are dropped with a reason;
- notification verdicts: `failed` retried three times, `denied` never counted as shown, permission
  returning later letting the reminder through, `unsupported` reported as such;
- time zones: the server's zone is used, a missing zone stops the client, a mismatched event is refused;
- boundaries: standard library only, one network module, no vendor names, no speech code, no UI assets,
  an installer that fetches nothing, and a README that does not claim Windows support.
