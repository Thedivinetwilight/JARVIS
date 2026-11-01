# Device support matrix — J.A.R.V.I.S. (v1.0.0)

- **Status:** specification of intent, **not** a support claim. **No client application exists for any target.**
- **Published:** 2026-11-01 · **Document version:** 1.0.0 · **Companion to spec 1.2.0** ([capability-specification.md](capability-specification.md))
- **Normative capability:** [C-14 Device surfaces and platform limits](capability-specification.md#c-14--device-surfaces-and-platform-limits)
- **Machine-readable copy:** [`capabilities.json`](capabilities.json) → `device_matrix`
- **Owner decisions behind it:** O-1 (dedicated server), O-2 (local-only processing), O-9 (voice-first, no chat boxes), O-11 (declared targets, EDITH Glass separate), O-12 (AETHERION out of scope)

> **Nothing here is implemented.** No device has been paired, no app has been built, no vendor API has been
> called and no latency has been measured. Every "unverified" cell is genuinely unverified: it is a list of
> things nobody has tested, not a euphemism for "works". The test suite checks that **0 of the 7 targets is
> marked verified or supported**.

## 1. How to read this matrix

For each target the matrix answers four questions, and nothing else:

1. **Intended interaction modes** — how the owner would actually use it (voice first, always).
2. **Integration boundary** — where the client ends and the server begins, and which component is external.
3. **Platform dependency and permissions** — the API, entitlement or user-granted role the target depends on,
   and what the owner has to grant.
4. **Unverified** — what has not been tested, including anything that may turn out to be **impossible**.

Two rules hold on every row: inference, memory, voice processing and call summaries stay on the dedicated
server (§0.2 of the specification), and **no surface offers a conversational text input** (O-9, G-UX). Touch
screens exist; text conversations do not.

| # | Target | Class | Status | Verified |
|---|---|---|---|---|
| 1 | PC (desktop or laptop) | declared target | specified, not built | no |
| 2 | Tablet (iPadOS or Android) | declared target | specified, not built | no |
| 3 | iPhone | declared target | specified, not built | no |
| 4 | Android phone | declared target | specified, not built | no |
| 5 | VR headset | declared target | specified, not built | no |
| 6 | Ray-Ban glasses | declared target | specified, not built | no |
| 7 | EDITH Glass | **separate future project** | not specified — requirements undefined | no |

**Out of scope:** AETHERION — a separate, postponed holographic-hardware research effort. It is not a surface
here, nothing in this specification depends on it, and it is not waiting behind any milestone (O-12).

**Also not a device target:** the [home holographic interface](capability-specification.md#c-13--home-holographic-interface-voice-first-spatial-display)
(C-13). It is a room-scale display in the home, specified with **no display technology chosen**. It is
neither VR nor
either glasses target, and it is listed in the specification rather than here because it is a display surface,
not a client device.

## 2. The targets in detail

### 2.1 PC (desktop or laptop)

- **Interaction modes:** voice command and response; visual output on the PC screen or an adjacent display;
  no text conversation.
- **Integration boundary:** a local-network client that talks only to the dedicated server. The PC renders and
  captures audio; it does not run inference.
- **Platform dependency and permissions:** the operating system's audio capture and playback and a local
  network transport; microphone, notifications, and screen capture only if explicitly granted.
- **Unverified:** no client application exists; local-network round-trip latency to the server is unmeasured;
  microphone and output-device selection is untested on each OS; behaviour with the machine asleep or on a
  metered link is unknown.

### 2.2 Tablet (iPadOS or Android)

- **Interaction modes:** voice; **touch to confirm or dismiss** — never to type a conversation; a mirror of
  what the home surface shows.
- **Integration boundary:** local-network client of the same server; no cloud AI in the path.
- **Platform dependency and permissions:** tablet OS background-audio and notification APIs; microphone,
  local network access, notifications.
- **Unverified:** whether audio keeps working while the app is suspended; long-lived audio limits imposed by
  the OS; no client application exists; whether a tablet can hold an always-available wake path without
  draining the battery.

### 2.3 iPhone

- **Interaction modes:** voice; **VoIP calls** the assistant originates or answers, presented in the system
  call interface; notifications and brief spoken summaries.
- **Integration boundary:** a companion app connected to the server over the local network or a tunnel the
  owner controls. The app carries its own VoIP media. **It is not a carrier call and does not become one.**
- **Platform dependency and permissions:** the platform's VoIP call framework (CallKit) with VoIP push
  (PushKit), and a Call Directory extension used **only** for caller identification and blocking. Microphone,
  notification permission, the VoIP push entitlement, contacts only if granted.
- **Unverified:** no application exists and entitlement approval is unknown; low-power and background
  behaviour is untested; whether any supported path avoids a third-party push service; whether store policy
  permits the intended use.
- **Forbidden by the platform, and therefore never promised:** the audio of an ordinary **cellular** call. A
  third-party app cannot join, record, transcribe into or speak inside a carrier call. This does not become
  possible later, and no milestone claims it.

### 2.4 Android phone

- **Interaction modes:** voice; **incoming-call screening** when the owner grants the call-screening role;
  placing and managing calls **only** with the default-dialer role; VoIP calls.
- **Integration boundary:** a companion app on the device. The roles are user-granted, revocable and never
  assumed. No cloud AI in the path.
- **Platform dependency and permissions:** the platform's call-screening service and the call-screening role,
  the default-dialer role (optional), the Telecom framework; phone state, microphone, contacts only if
  granted.
- **Unverified:** role availability and behaviour vary by device maker and OS version; the platform's
  screening deadline under a loaded server; no application exists; store policy for the intended use.

### 2.5 VR headset

- **Interaction modes:** voice; spatial visual output inside the headset; hand or gaze selection to confirm.
- **Integration boundary:** a headset client streaming from the server over the local network. The headset
  vendor's runtime stays a **vendor component**; if it processes personal data it has to be declared as an
  external service under §0.2 or the target is ruled out.
- **Platform dependency and permissions:** the headset runtime and its local-network streaming capability;
  microphone; sensor data only if granted.
- **Unverified:** no headset build exists; runtime and streaming performance is unmeasured; whether the
  runtime requires a vendor account or a cloud relay (which would collide with G-LOCAL). **This is not the home
  holographic interface**: VR needs hardware on the head, C-13 does not.

### 2.6 Ray-Ban glasses

- **Interaction modes:** voice with **audio-only** responses; photo capture only if granted; **no display** of
  the assistant's output on the glasses.
- **Integration boundary:** the glasses pair to the owner's phone, and the companion app is the bridge to the
  server. Any vendor component in that path is declared and logged.
- **Platform dependency and permissions:** whatever companion-app or device integration the vendor exposes.
  **No open, always-on audio path is assumed.** Microphone, Bluetooth pairing, camera only if granted.
- **Unverified:** whether a supported integration path exists **at all**; whether the vendor processes audio
  in its own cloud — if it does, it must be declared under the processing boundary or the target is ruled out;
  wake-word behaviour on the device; latency through the phone bridge; battery and comfort for long use.
- **Naming rule:** these are retail glasses. They are **never** described as EDITH (O-11).

### 2.7 EDITH Glass (separate future project)

- **Status:** **not specified.** The owner has confirmed it is a separate future custom hardware and software
  project; requirements are undefined (**Q25**). Nothing is assumed: not a display, not a camera, not
  always-on audio, not a form factor, not a price.
- **Interaction modes:** to be defined by the owner.
- **Integration boundary:** it would be its own project. This specification does not depend on it and does not
  describe it as available or planned for a date.
- **Platform dependency and permissions:** none established; nothing designed; nothing granted.
- **Unverified:** everything — hardware, optics, firmware, software, cost, timeline, and whether the owner
  pursues it at all.
- **Naming rule:** EDITH Glass and retail Ray-Ban glasses are different things and are never used
  interchangeably.

## 3. Telephony limits (the part most likely to be overpromised)

The platform decides, not the roadmap. Sources checked 2026-11-01: the Android platform's
[call-screening service and role](https://developer.android.com/reference/android/telecom/CallScreeningService)
and the iPhone's [VoIP call framework and Call Directory extension](https://developer.apple.com/documentation/callkit).

| Channel | Reachable? | Mechanism | Limit that must be stated |
|---|---|---|---|
| Android incoming calls | **partly** | call-screening role: allow, block or identify **before the phone rings**, inside a platform deadline of about 5 s | the role must be granted; no role means no function; behaviour varies by device maker |
| Android outgoing calls | **partly** | default-dialer role, if granted | a role the owner can revoke at any time; not needed for VoIP |
| iPhone VoIP calls the assistant carries | **yes, in principle** | CallKit with PushKit; Call Directory extension for identification and blocking | only the app's own VoIP calls; a push service may be involved |
| iPhone ordinary cellular calls | **no** | none exists | no third-party access to carrier call audio; **never promised**, at any milestone |
| Owner-controlled line (SIP trunk or landline gateway) | **yes** | the owner's own line attached to the server | full control, but recording and disclosure rules still apply (Q17) |
| Speakerphone with the owner present | **assist only** | the room microphone hears the owner's side | this is not call capture and is never described as joining the call |

## 4. Scoped, revocable, auditable access on every surface

- One **per-device** grant with a permission manifest; there is no "access to all my things" and no shared
  master token (O-7, R-10, G-SCOPE).
- Pairing is tier 1; camera, screen or call-role access is tier 2; anything that reaches another person's data
  is tier 3.
- Unpairing revokes the device's tokens and deletes its cached data **within 1 s of revocation**
  (C-12-AC3; C-14 permission rules).
- Every device action is logged with purpose, scope and outcome; revocation drills are part of the evidence
  for C-14 (B22).
- A device never receives a credential that gives it more than its own surface needs.

## 5. What would change this matrix

- **Q21** — the owner's actual accounts and devices. Coverage can only be measured against real inventory;
  until then B22 audits the declared classes and reports on the fixture, not on the owner's home.
- **Q25** — EDITH Glass requirements. Until the owner defines them, the row stays empty and honest.
- **Q26** — rooms and optical constraints for the home surface (C-13).
- **Q17** and **Q22** — jurisdiction, disclosure and consent for telephony, and the default call modes.
- A platform withdrawing a role, an entitlement or a device integration. The matrix is expected to change;
  it must change **downward** honestly when a platform closes a door.

## 6. What was learned

The device list is not a support list. Three of the seven targets depend on something the owner must grant
(Android roles, iPhone entitlements, glasses vendor integration), one is a research question rather than an
engineering one (VR runtime and cloud relay), one is plausible but unverified end to end (Ray-Ban glasses),
and one has no requirements at all yet (EDITH Glass). Only the PC and tablet rows are ordinary client work.

## 7. What remains uncertain

Whether **any** supported path exists that connects retail glasses to a local server without the vendor
processing audio; whether iPhone entitlements are granted; how much the Android screening deadline costs on a
loaded server; and whether the owner wants a dedicated telephone line. None of these is settled by this
document, and none is a promise.

## 8. Next step

Answer **Q21** (device and account inventory) and **Q25** (EDITH Glass) so the matrix can be measured against
reality rather than classes; run the M16 survey of what each declared platform actually permits; and keep the
"no carrier-audio promise" line auditable in every release note (G-CLAIM).
