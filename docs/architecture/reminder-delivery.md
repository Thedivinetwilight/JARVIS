# Reminder delivery: a local queue, retrieved by an explicitly targeted client

Status: **implemented infrastructure**. It changes no product capability: every entry in
`docs/product/capabilities.json` (C-01…C-14) remains `not implemented`, gate **G-O1** remains
blocked, and Q19 (languages), Q20 (server) and A9 (approval) remain open. Nothing here selects a
model, a client platform, a device vendor or a delivery provider.

## 1. What "delivery" means, precisely

A reminder is **delivered** when it becomes retrievable by the one client it was queued for, and
**finished** when that client acknowledges it. The server does not push, call, speak, render, e-mail
or contact any device. There is no message broker, no vendor service, no callback and no socket in
the delivery path: the only transport is the authenticated local HTTP surface, and the client starts
every exchange.

That is why the limitation `reminder_delivery_unavailable` still exists and still says so. A queued
event is **not** voice delivery, **not** a notification on a device, and **not** evidence that
C-03 (voice-first interaction) or C-13 (holographic interface) works.

## 2. Decision: polling, not push

| Considered | Outcome |
| --- | --- |
| Local polling by a paired client over the existing authenticated API | **Adopted**, and **confirmed by the owner on 2026-11-01** ("polling, while the client is online; nothing pushed"). No new trust boundary, no outbound path, revocation is immediate, everything is testable in-process. |
| Cloud push provider (FCM/APNs-class) | **Rejected.** It is an outbound network service and a vendor account; both are out of scope until individually scoped and approved. |
| Local socket/notification daemon | **Not approved.** It would add a long-lived listener and a new privilege boundary, and the owner has not decided for it; the decision record keeps it open rather than rejected outright. |
| Spoken or on-screen reminder | **Unavailable.** Speech output and display surfaces are still unimplemented, so the server must not pretend a reminder was "announced". |

The consequence is honestly stated in every response: `delivery.pushed`, `delivery.spoken` and
`delivery.displayed` are `false`, and the model string is carried with the payload rather than only
in documentation.

## 3. Explicit target or nothing

`POST /v1/tasks` accepts `deliver_to: "<client_id>"`. The server validates it before writing
anything:

| Condition | Answer |
| --- | --- |
| unknown client id | `400 delivery_target_unknown` |
| revoked client | `400 delivery_target_revoked` |
| target lacks `reminders.receive` | `400 delivery_target_scope_missing` |
| `deliver_to` without `due_at` | `400 delivery_target_without_schedule` |
| no `deliver_to` at all | reminder is created, left **unrouted**, and reported as `pending_unrouted` / reason `no_delivery_target` |

The server never infers a recipient, a device preference or a recurring schedule — not even when
exactly one client is paired. An unrouted reminder stays visible in `GET /v1/reminders/due`, in the
inbox counts (`unrouted_reminders`) and in `GET /v1/status` (`storage.reminder_deliveries`).

`reminders.receive` is a distinct scope: a device may be allowed to read due reminders without ever
being allowed to receive a queue, and a client whose grant is narrowed stops being a valid target.

## 4. The queue and its states

`reminder_deliveries(reminder_id, client_id)` with `UNIQUE (reminder_id, client_id)`.

```
pending ──read──► delivered ──ack──► acknowledged
   │                 │
   │                 ├── window elapsed ──► expired
   │                 └── attempts exhausted ──► failed
   └── target revoked / acknowledged elsewhere ──► cancelled
```

* **Idempotent by construction.** The unique constraint means enqueueing twice is one row; every
  transition is `UPDATE … WHERE state = ?` inside `BEGIN IMMEDIATE`, so a retry cannot double-count
  an attempt or move an acknowledgement stamp.
* **Attempts** count *hand-overs*, not requests: a repeat read inside `retry_interval_minutes` is the
  same attempt, so a client polling quickly cannot burn its budget. Exceeding
  `max_delivery_attempts` closes the row as `failed` / `attempts_exhausted`.
* **Expiry** is `fire_at + delivery_window_minutes`. A reminder that was never acknowledged inside
  the window is closed as `expired` / `delivery_window_elapsed` and reported in `held_back` with a
  reason instead of being silently dropped.
* **Repair after a crash.** Queueing is committed after the reminder. If the process dies between the
  two, the next inbox read reconciles the gap (`reminder_delivery.reconciled`), which is safe to run
  any number of times.
* **Deletion** of a task removes its reminder and, by foreign-key cascade, its queued events; the
  audit record states how many were removed.
* **Revocation** cancels a client's open events immediately (`client_revoked`) and keeps the rows, so
  the attempt record survives.

## 5. Time: UTC storage, one configured display zone, no local-to-instant conversion

All timestamps are stored in UTC. `reminders.display_timezone` (an IANA name resolved locally, never
fetched) is used **only** to render a wall clock for a human, together with the offset actually in
force at that instant.

**The display zone has no default.** It must be configured; an absent, empty or unknown name refuses
startup with one stable sentence (`reminders.display_timezone is required: …`), `/readyz` reports
`checks.reminders.ok: false` with the same text if a running host can no longer resolve the zone, and
the inbox answers `503 display_timezone_unresolved` rather than rendering a reminder in a zone nobody
chose. `UTC` is accepted when it is chosen, never as a fallback — a reminder shown at the wrong hour is
a wrong reminder, and the failure mode of a silent default is exactly the daylight-saving boundary
this design is careful about everywhere else.

**`late_after_seconds` is a server-side label, not a client measurement.** It is configuration
(default 10, range 0–3600), and lateness is decided on the exact interval from the scheduled time to
the first hand-over: at exactly the threshold the event is within target, at 10.5 seconds it is late,
so a floored integer in the payload cannot mask the boundary. It says nothing about how quickly a
client shows the reminder — that is a polling cadence the server neither controls nor observes, the
owner's 10-second figure is a target for it, and no client exists to demonstrate it. Because the
comparison is exact, the payload publishes the exact value it used next to the floored one:
`late_by_seconds` (whole seconds, for display) and `late_by_seconds_exact` (fractional, up to the
microsecond), with the same pair inside the `delay` block. A late event whose floored delay equals the
threshold is therefore always explainable from the payload alone — empirically, at 10.5 s late it
reports `late: true`, `late_by_seconds: 10`, `late_by_seconds_exact: 10.5` and a threshold of 10.

* A naive `due_at` is accepted for compatibility but labelled `due_time_basis: "assumed_utc"`; the
  basis is recorded when the task is written, not re-derived from the normalised value.
* **Fall-back (duplicate-delivery trap).** On 2026-11-01 in `America/New_York`, `05:30Z` and
  `06:30Z` are both 01:30 local. They are two instants, so they are two reminders, each delivered
  once, rendered `01:30-04:00` and `01:30-05:00`.
* **Spring-forward (nonexistent-time trap).** On 2027-03-14 `02:30` local does not exist. The server
  never converts a wall clock into an instant, so it cannot invent that time: `06:30Z` renders
  `01:30-05:00` and `07:30Z` renders `03:30-04:00`.
* **Downtime.** A reminder whose due time passed while the server was down is served on the next
  read, flagged `late` with `late_by_seconds` (late once the hand-over is past `late_after_seconds`);
  if it is older than the window it is reported as expired instead.
* **A display zone that becomes unresolvable at runtime.** `zoneinfo` caches parsed zones for the
  life of a process, so a cached object would keep rendering after the host's tz database lost the
  zone and readiness would keep claiming the zone was fine. Readiness and the hand-over guard
  therefore resolve with a cache bypass (`display_zone(..., fresh=True)`): they describe the host
  *now*. A live process in that state answers `/readyz` with `503` and the inbox with
  `503 display_timezone_unresolved`, both carrying the same sentence, and both return to `200` when
  the zone data comes back. Rendering itself still uses the cached object, which stays correct.

## 5b. The first client (Windows PC companion)

The client half is in `client/` and described in [windows-companion.md](windows-companion.md). Three
of its properties belong to this document because they are properties of the queue:

- it polls this client's inbox and nothing else — the server routes each reminder to one explicitly
  selected target, and the client never chooses, filters or invents one;
- **reading is not a new attempt** while inside `retry_interval_minutes`, so a 10-second cadence does
  not consume the attempt budget (asserted against this server in
  `client/tests/test_integration_loopback.py`);
- it **does not acknowledge on display**. An acknowledgement follows the owner's own action, because
  it closes the event for good and cancels the same reminder on any other target.

It has never been executed on Windows, so no toast, no client-side surfacing time and no sleep/resume
has been observed; the queue's own behaviour is unchanged by its existence.

## 6. Audit: the lifecycle, never the contents

`reminder_delivery.enqueued`, `.attempt`, `.acknowledged`, `.expired`, `.failed`, `.cancelled`,
`.reconciled` are recorded with ids, counts, attempts, seconds and reasons — never a reminder title,
note or body. The generic request record already logs `POST reminders.delivery.ack` against the
delivery id, and the detail object is passed through the same secret scrubber as every other record.
A test asserts the title and note never appear in the trail.

## 7. What would have to change before this is a notification channel

1. An owner decision on a delivery transport that is not the local API (A9 is open).
2. A selected, measured speech adapter before anything may be called spoken delivery (G-O1).
3. A client application, which does not exist for any device target.

Until then the honest sentence is: *the server queues a reminder for one named client and that
client collects it; the server has not spoken, displayed or pushed anything.*

### Which parts of this are decided, and which are still defaults

Every number and rule above was chosen to make the behaviour testable. On **2026-11-01** the owner
decided part of the contract and left the rest open, and the record is
[`../product/reminder-delivery-decision.md`](../product/reminder-delivery-decision.md):

| Decided | Still an unapproved default |
|---|---|
| **One** explicitly selected target device per reminder, "for now" | the number of delivery attempts (3) |
| **UTC storage** with a **required** local display zone — no default exists | the retry interval (5 minutes) |
| **Polling** while the client is online; nothing pushed | the delivery window (24 hours) |
| **Windows PC companion first** | what an attempt counts (hand-offs rather than polls) |
| **Local OS notifications first**; speech only after local speech is available and measured | any transport other than polling |
| a **10-second** surface target while the client is online — a target, not a measurement | |
| `late_after_seconds` as an explicit setting (default 10) | |

Two consequences bind this build. First, the client rows describe work that has not started: **no
Windows companion exists**, so nothing about the 10-second surfacing target or the notification has
been demonstrated, and the configuration work below does not change that. Second, the undecided
numbers stay **configurable and unapproved** — they are placeholders, not product requirements, and
the owner's explicit direction is that they must not be treated as approved defaults. A 10-second poll
cadence and the hand-off attempt rule must remain compatible when those numbers are finally chosen, or
a working client would be failed by its own polling.

Following those decisions, two configuration behaviours changed on this branch and nothing else:
`reminders.display_timezone` lost its implicit UTC default and is now required (with a stable refusal
sentence at startup, at `/readyz` and at the inbox), and the former hard-coded 60-second lateness
constant became `reminders.late_after_seconds` (default 10). Attempt counting, the retry interval, the
delivery window, expiry, acknowledgement, revocation and the polling-only transport are untouched.
