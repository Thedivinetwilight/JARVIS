# Reminder delivery — owner decision record

- **Status: partly decided (2026-11-01).** Six items carry an owner decision — DR-1, DR-5, DR-7,
  DR-8, DR-9 and DR-10. **Four remain pending owner decision**: DR-2, DR-3, DR-4 and DR-6. Any
  transport other than polling is also still open.
- **What this is:** the record of what the owner has decided about the reminder-delivery contract,
  together with what is still open, the alternatives, and the consequence of changing course. A
  recorded decision is an owner instruction about *intended behaviour*; it is not a capability claim.
- **What this is not:** not part of the published specification (`capability-specification.md`
  v1.2.0) and not part of its open-item register. The identifiers `DR-1…DR-10` are local to this
  document. Adding them to the owner register (18 entries: Q13–Q26, A7–A9 and the family note, of
  which Q16 and B4–B7 are already answered) would itself be an owner decision, so the register is
  untouched.
- **Nothing here changes product capability status.** Every capability C-01…C-14 remains
  `not implemented`, gate **G-O1** remains **blocked**, and Q19 (languages), Q20 (server), Q25
  (EDITH Glass), Q26 (holographic rooms/surfaces) and A9 (approval) remain **open**.
- **Nothing here is implemented as a change.** Recording a decision did not alter the server: the
  delivery code is exactly as it was. The first-client decisions describe work that has **not been
  started** — no client application exists for any target, and the device matrix still records
  **0 of 7 targets verified**.
- **Implementation state behind this sheet:** 49 deterministic tests (`app/tests/test_reminder_delivery.py`)
  and the architecture record [`../architecture/reminder-delivery.md`](../architecture/reminder-delivery.md).
  The tests prove the behaviour described below on fixed clocks and temporary databases; they
  establish no rate, latency or reliability figure.

## Owner decisions recorded on 2026-11-01

| # | Decision | What it settles | What it does not settle |
|---|---|---|---|
| DR-1 | **One explicitly selected target device per reminder, for now** | the recipient model stays single-target | whether a second target is ever supported; the words "for now" keep it reopenable |
| DR-5 | **UTC for storage; a configurable local display time zone** | storage format and that display is a setting — since this branch, a **required** one with no default | nothing; the former "which default zone?" question is void because there is no default |
| DR-7 | **Polling, while the client is online; nothing pushed** | the transport for the first client | server-initiated local delivery; any vendor push (out of scope) |
| DR-8 | **A Windows PC companion first** | which client comes first | every other target's order, and macOS/Linux PCs |
| DR-9 | **Local OS notifications first; spoken only after local speech is available and measured** | the modality sequence | whether spoken delivery is ever enabled — that stays behind G-O1 and measurement |
| DR-10 | **Target: surface a due reminder within 10 seconds while the client is online** | the delay the first client aims at, and the server's `late_after_seconds` label (default 10) | whether it is achieved — it is a target, not a measurement, and the label is not evidence of it |

**Still pending owner decision:** DR-2 (attempts), DR-3 (retry interval), DR-4 (delivery window),
DR-6 (what an attempt counts), and any transport other than polling. For DR-2, DR-3 and DR-4 the
owner's direction is explicit: **the behaviour stays configurable and the current numbers are not
approved defaults** — they remain the implementation's placeholders until a value is chosen.

## How to answer the open items

Each item gives the current behaviour, the practical alternatives, and what changes if the answer
differs from today. One line per item is enough. **If an item is left unanswered, the current
behaviour continues and stays labelled unapproved** — it does not become a requirement by default.

Standing constraints that hold **regardless of every answer below**:

- **Local only** (O-1, O-2): no cloud service, no vendor account, no third-party push provider.
- **No text-conversation interface or chat box on any surface** (O-9, G-UX). A reminder is not a
  message and never becomes one.
- **No device inventory is assumed.** No client application exists for any target; the device matrix
  records **0 of 7 targets verified**.
- **No speech is claimed.** Speech recognition and spoken output stay unavailable until a local
  adapter is selected and measured; gate G-O1 is blocked.
- A queued event is **never** described as pushed, spoken, displayed or notified by the server.

---

## Part A — the delivery contract as implemented

Summary of what the code does today, and which parts the owner has since decided:

| # | Choice | Current value | Status | Where it lives |
|---|---|---|---|---|
| DR-1 | Recipients per reminder | exactly **one** client, named explicitly | **decided, "for now"** | `deliver_to` on `POST /v1/tasks`; `reminders.delivery_client_id` |
| DR-2 | Delivery attempts | **3** | **pending** — value not approved | `reminders.max_delivery_attempts` |
| DR-3 | Retry interval | **5 minutes** | **pending** — value not approved | `reminders.retry_interval_minutes` |
| DR-4 | Delivery window | **24 hours** after the scheduled time | **pending** — value not approved | `reminders.delivery_window_minutes` |
| DR-5 | Storage and display zone | UTC storage; `display_timezone` **required, no default** | **decided** | `reminders.display_timezone` |
| DR-6 | What an attempt counts | hand-offs, not every poll | **pending** | `app/jarvis/reminders.py` |
| DR-7 | Transport | polling only; nothing pushed, spoken or displayed | **decided, "for now"** | the two inbox routes; no push route exists |

### DR-1 · One target client per reminder — **owner decision recorded (2026-11-01): one, for now**

**Owner decision.** One explicitly selected target device per reminder, *for now*. Nothing in the
build changes: the single-target rule, the explicit `deliver_to`, and the refusal to pick a default
recipient all stay. "For now" keeps the question reopenable; it does not schedule multi-target work.
Multi-client and owner-defined groups (options 2 and 3) are **not approved** and are not planned.

**Current behaviour.** `POST /v1/tasks` takes `deliver_to: "<client_id>"`, validated to exist, be
unrevoked and hold the `reminders.receive` scope. Exactly one client can hold a reminder, one queue
row is created (a database constraint forbids a second row for the same reminder and client), and
every other client sees nothing. A reminder created without `deliver_to` is **not sent anywhere**: it
stays `pending_unrouted` with reason `no_delivery_target` and is reported that way in the task, the
due list and `/v1/status`. No default recipient is ever inferred, even when exactly one device is
paired. If a second client is ever queued for the same reminder, acknowledging it in one place
closes the others as `acknowledged_elsewhere` — that path is currently unreachable.

**Practical alternatives**

1. One explicit client (today).
2. Several explicitly named clients, each with its own state and its own acknowledgement.
3. One explicitly named **group** (e.g. "all of the owner's devices"), membership defined by the owner.
4. No target at all: reminders exist only as a due list, never queued.

**If this decision is revisited.** Option 2 needs a schema change (a targets table replacing the single column),
per-client acknowledgement semantics, and a "who has seen it" answer in the API — the current
"acknowledged" means *this reminder is done for everyone*. Option 3 additionally needs a group
register with owner-defined membership; the server must never guess membership from pairing, and a
group is another thing that can be revoked. Option 4 removes the queue entirely and returns the
build to "due reminders are reported only". None of these touches the local-only boundary.

### DR-2 · Three delivery attempts — **pending owner decision**

> **Owner direction (2026-11-01):** the number of attempts must stay **configurable**, and the
> current value of 3 is **not** an approved default. The number itself is still to be chosen; the
> entry below is unchanged in substance and remains open.

**Current behaviour.** After the 3rd hand-off without acknowledgement, the next attempt closes the
event as `failed` / `attempts_exhausted`. It is then reported in `held_back` with that reason and is
no longer served. The number is configuration (`max_delivery_attempts`, accepted range 1–10), not a
constant, and it can never hide a reminder: the closed event stays visible to the client as
"not delivered, because …".

**Practical alternatives:** 1 (one chance) · 3 (today) · 5–10 (persistent) · unlimited until the
window elapses.

**If you change it.** A value in the current 1–10 range is a configuration change with no migration;
"unlimited" would also need the accepted range widened (a small code change). Lower values end
delivery sooner: with 1, a client receives exactly one hand-off and the event is closed at the
following retry if it was not acknowledged. Higher values mostly *delay* the failure state, because
the delivery window (DR-4) is the real upper bound; with unlimited attempts the window is the only
stop. This value is only meaningful together with DR-3 and DR-6: it bounds *attempts*, not requests.

### DR-3 · Five-minute retry interval — **pending owner decision**

> **Owner direction (2026-11-01):** the interval must stay **configurable**, and 5 minutes is **not**
> an approved default. One constraint now applies from DR-10: the first client aims to poll often
> enough to surface a reminder within 10 seconds, so an interval **shorter than the poll cadence**
> would turn every poll into a new attempt and could close a reminder while it is being used.

**Current behaviour.** A repeat hand-off inside 5 minutes is the *same* attempt: counters and
timestamps advance only when at least `retry_interval_minutes` have passed since the last hand-off.
This is what stops a client that polls every few seconds from burning its attempt budget while it is
actively in use.

**Practical alternatives:** 30 seconds · 5 minutes (today) · 30–60 minutes · no interval at all
(every successful hand-off counts).

**If you change it.** A value in the current 1–1440-minute range is a configuration change with no
migration; an interval of *zero* would need the accepted range widened (a small code change). Shorter
intervals make attempts accrue faster, so a client that reconnects repeatedly can reach the DR-2 limit
while the owner is still looking at the device. Longer intervals make a genuine retry (client crashed
and came back) take longer to register, but nothing inside the window is lost. With no interval at
all, DR-2 becomes a *poll* budget: a client polling every 10 seconds would exhaust 3 attempts in half
a minute. **DR-2 and DR-3 should therefore be answered together.**

### DR-4 · Twenty-four-hour delivery window — **pending owner decision**

> **Owner direction (2026-11-01):** the window must stay **configurable**, and 24 hours is **not** an
> approved default. The value is open.

**Current behaviour.** Each queued event expires 24 hours after its scheduled time
(`delivery_window_minutes`, accepted range 1–43 200). An unacknowledged event past that point is
closed as `expired` / `delivery_window_elapsed` and listed in `held_back` with that reason; it is
never delivered afterwards, and it never silently disappears.

**Practical alternatives:** 1 hour (time-critical only) · 24 hours (today) · 7 days (tolerant of
travel) · no expiry (kept until acknowledged).

**If you change it.** A value in the current 1–43 200-minute range (up to 30 days) is a configuration
change with no migration; "no expiry" would be a small code change. A short window means a reminder
that fires overnight with the server or client down can be *lost as stale* — correctly reported, but
not delivered. A long window means a three-day-old reminder can surface ("take the medication" from
last week), which is a safety and trust question rather than a technical one. No expiry means the
queue keeps personal titles indefinitely, which interacts with the deletion and retention posture.

### DR-5 · Storage and display time zones — **decided (2026-11-01): UTC storage, required local zone, no default**

**Owner decision.** Timestamps are **stored in UTC**, and the display time zone is a **configurable
local** setting.

**Follow-up applied.** The implicit UTC default is gone: `reminders.display_timezone` is now
**required**, and an absent, empty or unknown name refuses startup with one stable, actionable
sentence (the same text appears in `/readyz` and, as a `503`, if a running host can no longer resolve
the zone). No zone is assumed on the owner's behalf and none is filled in for them — the example
configuration ships the key empty with an instruction to take the name from the host itself. The
earlier open question "which zone is the default?" is therefore **closed by having no default**, not
by choosing one.

**Current behaviour.** Every timestamp is stored in UTC. `reminders.display_timezone` (any IANA zone,
resolved locally, never fetched from a network source) is used **only** to render a wall clock for a
human, together with the offset in force at that instant. There is no default value: the key is
required, and until it is set the server refuses to start rather than rendering in a zone nobody
chose. Daylight saving is handled by rendering the instant, never by parsing a wall clock back: the
two 01:30s of a fall-back day are two distinct reminders, each delivered once, and a time that does
not exist on a spring-forward day is never invented. A timestamp written without an offset is
accepted but recorded as `due_time_basis: "assumed_utc"` — the assumption is visible rather than
presented as local time.

**Options that were considered for the default zone (now moot, recorded for completeness)**

1. UTC by default — **removed** by the owner's follow-up; a silent UTC is the failure mode this
   decision exists to prevent.
2. The owner's primary residence zone by default — never adopted; a zone the server guessed is a zone
   the owner did not choose.
3. Each client supplies its own zone; the server renders per client — not adopted; it needs a client
   preference field, and the first client does not exist.
4. No rendering at all, UTC everywhere — not adopted; it contradicts "configurable local display".

**If this is revisited.** Re-introducing any default would have to explain how a wrong default is
detected. A separate question, still not answered and not assumed here: whether a timestamp written
*without* an offset should be interpreted as the owner's local zone. That would change the stored
instant of existing reminders (a behaviour change and a daylight-saving hazard), so it stays on its
own, outside this record.

### DR-6 · An attempt counts hand-offs, not polls — **pending owner decision**

> **Owner direction (2026-11-01):** no answer yet. It now matters more, because the first client aims
> at a 10-second surface target: one poll every 10 seconds must not consume the attempt budget, which
> is the behaviour today (hand-offs are separated by the retry interval). Whatever DR-2 and DR-3
> become, that property has to hold or a working client will be failed by its own polling.

**Current behaviour.** "Attempt" means *a distinct delivery episode*: the first hand-off after the
due time, plus each later hand-off separated by at least the retry interval. A client polling every
second for ten minutes records exactly one attempt. The attempt number, the maximum and the lateness
are shown to the client.

**Practical alternatives**

1. Hand-offs only (today): attempts measure delivery episodes.
2. Every successful read counts: attempts measure requests.
3. Only a read from a "new session" counts (client reconnect).
4. Attempts are not counted; only the window bounds delivery.

**If you change it.** Option 2 changes the meaning of every number already reported, and it makes a
malfunctioning client that polls in a loop close reminders as "failed" while the owner never saw
them; it also makes DR-2 and DR-3 into poll-rate limits. Option 3 needs a session concept the server
does not have (the API is stateless per request). Option 4 removes the failure state entirely, so
`held_back` would only ever show expiry and cancellation, and the audit trail would lose the
"attempts exhausted" record.

### DR-7 · Polling is the only transport — **owner decision recorded (2026-11-01): polling, for now**

**Owner decision.** Delivery stays **client-initiated polling**, and the delivery target is stated as
"**while the client is online**": nothing is pushed from the server, and there is no expectation that
a reminder reaches a device that is off, asleep or not running the client. Options 3
(server-initiated local-network delivery) and 4 (a vendor push service) are **not approved**; option 4
stays out of scope under the standing constraints, and option 3 would need its own decision and threat
review before any code. Option 2 — the client scheduling a **local** OS notification from what it
fetched — is what DR-9 selects, and it is still the client acting on polled data, not the server
pushing.

**Current behaviour.** A paired client calls `GET /v1/reminders/inbox` and
`POST /v1/reminders/inbox/{id}/ack`. The server never initiates contact: there is no push route, no
subscription, no callback and no broker in the codebase, and the egress guard denies outbound
connections by default. Every response states `pushed: false`, `spoken: false`, `displayed: false`,
and the limitation `reminder_delivery_unavailable` says the same in the catalogue. Nothing is spoken
(speech output adapter unavailable) and nothing is drawn (no display surface exists).

**Practical alternatives**

1. Client-initiated polling (today) — **decided, for now**.
2. Client fetches, then schedules a **local** operating-system notification itself (see DR-9). Still
   no server push, no external service; the server cannot know whether it fired. **This is what DR-9
   selects**: the client acts on polled data, the server still pushes nothing.
3. Server-initiated delivery over the owner's local network (a listener on the device or a socket
   the server opens to it). This is a new inbound surface and a new trust boundary, and it would need
   its own decision record and threat review before any code.
4. Any vendor push service. **Out of scope by the standing constraints** (O-2, no external services);
   the device matrix already flags push entitlements as unverified per platform for the phone target.
5. Server speaks the reminder aloud. **Unavailable** until a local speech adapter exists and has been
   measured (G-O1 blocked); a queued event must not be called spoken delivery.

**If this decision is revisited.** Option 2 changes only the client, and the server's honest phrasing stays
"queued and retrieved". Option 3 is the only option that would reduce delay below the client's poll
interval, and it is the most consequential: a listening service on a device, pairing and authorisation
for that channel, platform background limits (which differ per target and are untested), and a new
answer for what happens when the device is unreachable. Options 4 and 5 cannot be enabled by this
work at all.

---

## Part B — the first client, and how it surfaces reminders

### DR-8 · Which client surfaces reminders first — **owner decision recorded (2026-11-01): a Windows PC companion**

**Owner decision.** The first client is a **Windows PC companion**. This narrows the declared PC
target (O-11) to one operating system: macOS and Linux PCs are **not** in the first client, and their
order is still open, as is every other target's. The decision says which client comes first; it does
**not** claim that a PC client exists, that Windows notification delivery works, or that C-14 is
verified — the device matrix still records 0 of 7 targets verified and no client application exists.
The companion's minimum contract, and what is deliberately undecided about it, are in
[Part D](#part-d--the-first-client-as-decided-windows-pc-companion).

The owner has named PCs, tablets, phones, VR and Ray-Ban glasses as declared targets (O-11), a home
holographic interface as the primary spatial display (O-10, C-13), and EDITH Glass as a separate
future hardware/software project with undefined requirements (O-11, Q25). No order has been given for
the remaining targets, and **no inventory is assumed**: the table lists the targets that exist in the
specification, not a claim that the owner owns any particular one.

**What the choice costs, per target** (all currently `specified, not built`; 0 of 7 verified):

| Candidate | What makes it suitable | What stays unverified | Notes |
|---|---|---|---|
| Phone (iPhone or Android) | always with the owner; most natural place for a reminder | background execution, notification permission, battery policy | two separate platforms mean two clients, not one |
| PC / laptop | easiest to build and test; no battery constraint | only useful when the owner is at the desk | the notification path is the simplest OS integration |
| Tablet | a home surface that is on and docked | background timers, long-lived notification behaviour | closest to the intended "home" usage |
| VR headset | head-worn surface | platform path, session model, comfort limits | no display surface work exists in this build |
| Ray-Ban glasses | near-eye, but audio-first | platform path, whether any notification abstraction exists | not EDITH (O-11) |
| Home holographic interface | the owner's primary spatial display (O-10) | everything: no display technology, optics or prototype is chosen; rooms/surfaces are Q26 (open) | cannot be first without a display decision |
| EDITH Glass | separate project | requirements undefined (Q25 open) | cannot be chosen as "a client" until it is defined |

**If this decision is revisited.** Choosing a first client starts platform-specific work
(permissions, background behaviour, packaging) and creates the first real evidence for one row of C-14
— while the other targets stay unverified. Choosing one target does **not** imply support for the
others. If a later decision moves the first client to the holographic interface, the prerequisite is a
display decision (C-13), which is not part of this record.

### DR-9 · Operating-system notification, spoken output, or both — **owner decision recorded (2026-11-01): notifications first, speech only after measurement**

**Owner decision.** The first client uses **local operating-system notifications first**. Spoken
delivery is added **only after local speech is available and has been measured** — which keeps it
behind gate G-O1 and behind the voice workload measurement that has not been run. Neither is
implemented today: the server still queues an event and says nothing was pushed, spoken or displayed.

**Current behaviour:** neither. The server queues an event; the client retrieves it. There is no
notification and no speech, and the response says so.

**Options**

1. **OS notification only** (client-side, from data the client just fetched). No model, no speech, no
   network egress; the client schedules the banner/sound itself. Honest limit: the server cannot
   confirm that the notification appeared, and a client that is not running schedules nothing.
   **This is the first step, as decided.**
2. **Spoken output only.** Blocked today: the speech output adapter is `unavailable`, no model is
   selected (G-O1), and no speech measurement exists. Speech output is synthesis, not comprehension:
   a spoken reminder must never imply that anything was understood. **Not approved.**
3. **Both, sequenced:** notification first, speech later, and only after a local adapter has been
   selected and measured on the owner's server. **This is the decided sequence; the second step is a
   condition, not an authorisation.**
4. **Neither yet** — keep the queue and let the first client surface reminders inside its own view.

**If this decision is revisited.** Option 1 is client work only and can be built without touching
the server or any gate. Option 2 cannot be built now and must not be described as available; even after
a speech adapter exists, spoken reminders would be the first user of speech synthesis, so the
measurement has to come first. Option 3 is the decided sequence, and its second step stays blocked on
G-O1. Whichever way it goes, an OS notification is **not** voice capability, and no option makes C-03
or C-13 true.

### DR-10 · Acceptable delivery delay — **owner decision recorded (2026-11-01): within 10 seconds while online**

**Owner decision.** A due reminder should surface **within 10 seconds while the client is online**.
This is a **target for the first client's poll cadence**, not a measurement: nothing has been built,
so the 10 seconds are unverified, and a client that is offline has no bound at all. The server needs no
timer to meet it — it hands over whatever is due on each request — but the interaction with DR-3 and
DR-6 has to hold (a 10-second poll must not count as a new attempt every time).

**Follow-up applied.** The former hard-coded 60-second "late" constant is now the explicit setting
`reminders.late_after_seconds`, default **10** — the owner's figure — and lateness is decided on the
exact interval: a hand-over at exactly 10 seconds is within target, anything past it (10.5 seconds
included) is labelled late. The two numbers stay distinct: the threshold is what the *server* labels
on its own hand-over delay, while the 10-second target is what a *client* should achieve once online.
A payload field never presents the first as evidence of the second.

**Current behaviour.** Delay is whatever the client's poll interval is: nothing schedules work on
the server — expiry and lateness are computed when a client asks — there is no push, and therefore no
guaranteed bound. A hand-over past `reminders.late_after_seconds` after the scheduled time is labelled
**late**; the setting is configuration (default 10, range 0–3600) and is reported in every event as
`late_after_seconds`, alongside `late_by_seconds` and a `delay` block that states what was and was not
measured.

**Options and their practical meaning**

| Tolerance | What it requires |
|---|---|
| a few seconds | server-initiated delivery (DR-7 option 3) or constant polling; constant polling is hostile to battery and to the device platforms' background rules |
| about a minute | a client polling roughly every 30–60 s while foregrounded or permitted in the background |
| about fifteen minutes | a client polling every few minutes; plausibly compatible with mobile background budgets, which remain unmeasured on every target |
| "next time the client is open" | no background work at all; a reminder may surface hours later, and the window (DR-4) decides whether it still exists |

The decided target — **within 10 seconds while the client is online** — sits at the demanding end of
this table: it needs a poll interval of about 10 seconds or less, which is achievable without push but
costs battery and depends on Windows scheduling behaviour that nobody has measured here.

**If this decision is revisited.** The tolerance decides whether DR-7 can stay "polling only" and
how aggressive polling must be, and `late_after_seconds` should move with it so the label keeps
matching the target. Nothing here can be verified without a real client on a real device, and none of
those measurements exist.

---

## Part C — what does not change under any answer

- Storage stays local SQLite in **UTC**; delivery stays inside the authenticated local API; the
  egress guard stays default-deny.
- No cloud push provider, no vendor cloud, no web retrieval, no telephony, no update downloads — and
  a Windows companion does not change that: it talks to the local server, not to a service.
- No text-conversation interface, no chat box, no message thread on any surface, including the
  companion: a reminder stays a structured record with a schedule and a target, and an OS
  notification is not a message.
- The server never claims to have spoken, displayed, notified or pushed anything it did not; the
  limitation `reminder_delivery_unavailable` keeps saying so.
- Speech recognition and spoken output remain unavailable until selected and measured; **G-O1 stays
  blocked**, and the "spoken later" sequence in DR-9 is a condition, not an approval.
- Capability status is untouched: C-01…C-14 remain `not implemented`, and the device matrix stays at
  0 of 7 verified — a decided client does not verify a row.
- No device inventory, ownership or preference is assumed: choosing Windows PC first says which
  client to build first, not what the owner currently owns.

## Part D — the first client, as decided (Windows PC companion)

This is the **minimum contract** that follows from DR-1, DR-7, DR-8, DR-9 and DR-10. It is a design
target for work that has not started, not a description of anything that exists.

**What the companion must do**

1. Talk **only** to the local server, with a paired device token holding `reminders.receive`, over
   the authenticated API — `GET /v1/reminders/inbox` while online, `POST /v1/reminders/inbox/{id}/ack`
   when the owner dismisses or completes the reminder.
2. Poll often enough to surface a due reminder **within 10 seconds while online** (DR-10) — in
   practice a poll interval of 10 seconds or less, with backoff when the server is unreachable.
3. Show a **local operating-system notification** built from the fetched event (DR-9). Showing it is
   not acknowledging it: acknowledgement follows the owner's own action, because an acknowledgement
   closes the event for good. The server cannot verify that a notification appeared, and the
   companion must not imply that it did.
4. Show the reminder's scheduled time in a **local display time zone** that the owner configures,
   while the server stores UTC (DR-5).
5. Present **no text-chat box, no conversation view and no message thread** (O-9, G-UX). A
   notification with a title and a scheduled time is the whole surface.

**What the companion must not do**

- No speculative network activity: no telemetry, no update checks, no cloud account, no vendor
  service, no third-party push registration.
- No speech output, and no claim of speech, until a local adapter is selected and measured (G-O1).
- No second recipient: it acknowledges only its own deliveries (DR-1).
- No inferred preference: it does not choose its own time zone silently, and it does not decide
  which reminders to hide.

**Deliberately undecided about the companion** — do not treat these as implied by "Windows PC first":

| Question | State |
|---|---|
| Whether it runs at login, in the tray, or only when launched | not decided |
| Minimum Windows version, installer, code signing, upgrade path | not decided |
| Whether it later handles tasks, audio capture or voice | not decided; each is separate work |
| Whether it schedules notifications itself or only shows them while running | not decided; both are client-side |
| macOS and Linux PCs | not decided, not started, not implied |
| Every other target (tablet, phone, VR, glasses, holographic, EDITH Glass) | order still open (Q25, Q26 open) |

## Part E — registers that stay open

| Register | State after these decisions |
|---|---|
| Open items Q13–Q26, A7–A9 | unchanged; Q16 and B4–B7 answered as published, every other item still open |
| G-O1 model selection | **blocked** |
| G-UX (no chat box), G-LOCAL, G-SCOPE, G-CLAIM | open |
| Published specification | unchanged at v1.2.0; this record is not part of it |
| `capabilities.json` open-item count | unchanged (18); adding `DR-1…DR-10` to it would be a separate owner decision |
| Device matrix | unchanged: PC (desktop or laptop) stays `specified, not built`; **0 of 7 verified** |
| Reminder delivery contract | DR-2, DR-3, DR-4 and DR-6 still **pending owner decision**; DR-5 and DR-10 are decided and implemented as configuration |

## Part F — answer sheet

Recorded answers are filled in; anything blank is still the owner's to set, and while it is blank the
current behaviour continues, labelled unapproved.

```
DR-1  recipients per reminder ......  [x] one client, for now          [ ] several  [ ] group  [ ] none
DR-2  delivery attempts ............  [ ] 1   [ ] 3   [ ] 5-10   [ ] unlimited until window   (configurable; 3 not approved)
DR-3  retry interval ...............  [ ] 30 s  [ ] 5 min  [ ] 30-60 min  [ ] none            (configurable; 5 min not approved)
DR-4  delivery window ..............  [ ] 1 h  [ ] 24 h  [ ] 7 days  [ ] no expiry            (configurable; 24 h not approved)
DR-5  storage ......................  [x] UTC
      display zone .................  [x] required, explicitly configured — no default (example ships empty)
                                        [ ] alternative: default: ______   [ ] per-client   [ ] no rendering
DR-6  attempt semantics ............  [ ] hand-offs  [ ] every read  [ ] new session  [ ] not counted
DR-7  transport ....................  [x] polling while online      [ ] local-network push      (vendor push out of scope)
DR-8  first client .................  [x] Windows PC companion      [ ] other: ______
DR-9  modality .....................  [x] local OS notification first; spoken only after local speech is available and measured
DR-10 delivery delay ...............  [x] within 10 seconds while the client is online (target, unverified)
```
