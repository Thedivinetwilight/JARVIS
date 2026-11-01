"""Capability and connector manifests: the least-privilege contract.

A manifest is a static declaration of what the server can do, which scope it needs, and
whether it is actually available. Routes are authorised against these manifests, so a
scope cannot be used for a capability that does not exist, and a capability cannot appear
"working" merely because a route exists.

Two rules are encoded here and tested:

* a manifest whose ``status`` is not ``available`` cannot be served — the request fails with
  ``503`` and a machine-readable reason, rather than a fake success;
* every external connector is ``disabled`` and points at one of the four declared services
  from :mod:`app.jarvis.egress`, so enabling one is a visible, reviewed change.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .errors import Unavailable

#: Scope ids. Read/write pairs per capability, plus administrative scopes.
SCOPE_STATUS_READ = "status.read"
SCOPE_MANIFESTS_READ = "manifests.read"
SCOPE_AUDIT_READ = "audit.read"
SCOPE_CLIENTS_READ = "clients.read"
SCOPE_CLIENTS_REVOKE = "clients.revoke"
SCOPE_TASKS_READ = "tasks.read"
SCOPE_TASKS_WRITE = "tasks.write"
SCOPE_REMINDERS_READ = "reminders.read"
SCOPE_REMINDERS_WRITE = "reminders.write"
SCOPE_AUDIO_CAPTURE = "audio.capture"
SCOPE_SPEECH_RECOGNIZE = "speech.recognize"
SCOPE_SPEECH_OUTPUT = "speech.output"
SCOPE_SURFACE_RENDER = "surface.render"

#: Scopes a paired device must never receive: they expose other clients or the audit trail.
ADMIN_SCOPES = frozenset({SCOPE_CLIENTS_READ, SCOPE_CLIENTS_REVOKE, SCOPE_AUDIT_READ})

#: What a freshly paired device receives unless the operator narrows it further.
DEFAULT_DEVICE_SCOPES = frozenset(
    {
        SCOPE_STATUS_READ,
        SCOPE_MANIFESTS_READ,
        SCOPE_TASKS_READ,
        SCOPE_TASKS_WRITE,
        SCOPE_REMINDERS_READ,
        SCOPE_REMINDERS_WRITE,
        SCOPE_AUDIO_CAPTURE,
    }
)

OPERATOR_SCOPES = frozenset(
    set(DEFAULT_DEVICE_SCOPES)
    | ADMIN_SCOPES
    | {SCOPE_STATUS_READ, SCOPE_MANIFESTS_READ}
    | {SCOPE_SPEECH_RECOGNIZE, SCOPE_SPEECH_OUTPUT, SCOPE_SURFACE_RENDER}
)


@dataclass(frozen=True)
class Manifest:
    id: str
    kind: str            # capability | connector | surface
    title: str
    status: str          # available | unavailable | boundary-only | disabled | not-a-target
    scopes: tuple[str, ...]
    reason: str = ""
    external_service: str | None = None
    spec_reference: str = ""
    notes: str = ""

    @property
    def servable(self) -> bool:
        return self.status in {"available", "boundary-only"}

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["servable"] = self.servable
        # "servable" means a handler may run (the boundary exists); "implemented" is the
        # claim that the thing itself works, and only a real capability may make it.
        payload["implemented"] = self.status == "available"
        payload["scopes"] = list(self.scopes)
        return payload


_SPEC = "docs/product/capability-specification.md"
_MATRIX = "docs/product/device-support-matrix.md"

MANIFESTS: tuple[Manifest, ...] = (
    # ---------------------------------------------------------------- capabilities
    Manifest(
        id="tasks",
        kind="capability",
        title="Tasks (structured records, no model)",
        status="available",
        scopes=(SCOPE_TASKS_READ, SCOPE_TASKS_WRITE),
        reason="",
        spec_reference=f"{_SPEC}#1-requirement-summary",
        notes="Everyday assistance without a model: explicit title, optional note and due "
              "time, local storage. Natural-language voice entry needs the speech adapters, "
              "which are unavailable.",
    ),
    Manifest(
        id="reminders",
        kind="capability",
        title="Reminders (local scheduling, no delivery channel yet)",
        status="available",
        scopes=(SCOPE_REMINDERS_READ, SCOPE_REMINDERS_WRITE),
        reason="",
        spec_reference=f"{_SPEC}#1-requirement-summary",
        notes="Due reminders are computed locally and read by a client. There is no speech "
              "or display delivery channel yet, so nothing is spoken or shown by the server.",
    ),
    Manifest(
        id="audio.session",
        kind="capability",
        title="Voice-first audio session (input boundary)",
        status="available",
        scopes=(SCOPE_AUDIO_CAPTURE,),
        reason="",
        spec_reference=f"{_SPEC}#c-03--voice-first-interaction-multilingual-no-chat-boxes",
        notes="Accepts audio and stores it locally. No text-conversation endpoint exists. The "
              "response states that transcription is unavailable.",
    ),
    Manifest(
        id="speech.asr",
        kind="capability",
        title="Speech recognition (local)",
        status="unavailable",
        scopes=(SCOPE_SPEECH_RECOGNIZE,),
        reason="no local speech adapter is configured, and selecting one is part of the "
               "blocked model decision (G-O1); audio is stored, not understood",
        spec_reference=f"{_SPEC}#c-04--speech-recognition-and-authorized-speaker-verification",
    ),
    Manifest(
        id="speech.tts",
        kind="capability",
        title="Spoken audio output (local)",
        status="unavailable",
        scopes=(SCOPE_SPEECH_OUTPUT,),
        reason="no local speech synthesis adapter is configured (G-O1 blocked); the server "
               "never reads an answer aloud and never returns text as a substitute",
        spec_reference=f"{_SPEC}#c-03--voice-first-interaction-multilingual-no-chat-boxes",
    ),
    # ---------------------------------------------------------------- surfaces
    Manifest(
        id="surface.holographic",
        kind="surface",
        title="Home holographic interface (client/API boundary only)",
        status="boundary-only",
        scopes=(SCOPE_SURFACE_RENDER,),
        reason="no display technology is selected and no optics are implemented; only the "
               "protocol boundary and its registry entry exist (C-13)",
        spec_reference=f"{_SPEC}#c-13--home-holographic-interface-voice-first-spatial-display",
        notes="Room-scale display in the home. Distinct from VR and from both glasses targets.",
    ),
    Manifest(
        id="surface.vr",
        kind="surface",
        title="VR headset (client boundary only)",
        status="boundary-only",
        scopes=(SCOPE_SURFACE_RENDER,),
        reason="no headset client exists; the registry entry exists so the boundary is "
               "explicit and distinct from the home surface",
        spec_reference=f"{_MATRIX}#25-vr-headset",
    ),
    Manifest(
        id="surface.pc",
        kind="surface",
        title="PC (client boundary only)",
        status="boundary-only",
        scopes=(SCOPE_SURFACE_RENDER,),
        reason="no client application exists",
        spec_reference=f"{_MATRIX}#21-pc-desktop-or-laptop",
    ),
    Manifest(
        id="surface.tablet",
        kind="surface",
        title="Tablet (client boundary only)",
        status="boundary-only",
        scopes=(SCOPE_SURFACE_RENDER,),
        reason="no client application exists",
        spec_reference=f"{_MATRIX}#22-tablet-ipados-or-android",
    ),
    Manifest(
        id="surface.iphone",
        kind="surface",
        title="iPhone (client boundary only; VoIP only, never cellular audio)",
        status="boundary-only",
        scopes=(SCOPE_SURFACE_RENDER,),
        reason="no client application exists; carrier call audio is unavailable to third-party "
               "apps and is not planned",
        spec_reference=f"{_MATRIX}#23-iphone",
    ),
    Manifest(
        id="surface.android_phone",
        kind="surface",
        title="Android phone (client boundary only)",
        status="boundary-only",
        scopes=(SCOPE_SURFACE_RENDER,),
        reason="no client application exists; screening and dialer roles require the owner's "
               "explicit grant and are not implemented",
        spec_reference=f"{_MATRIX}#24-android-phone",
    ),
    Manifest(
        id="surface.rayban_glasses",
        kind="surface",
        title="Ray-Ban glasses (retail device; audio only, no display)",
        status="boundary-only",
        scopes=(SCOPE_SURFACE_RENDER,),
        reason="no integration path is assumed to exist; audio-only responses, no display of "
               "assistant output on the glasses",
        spec_reference=f"{_MATRIX}#26-ray-ban-glasses",
    ),
    Manifest(
        id="surface.edith_glass",
        kind="surface",
        title="EDITH Glass (separate future project, not a target)",
        status="not-a-target",
        scopes=(),
        reason="the owner has not defined its requirements (Q25); nothing is designed, "
               "assumed or promised",
        spec_reference=f"{_MATRIX}#27-edith-glass-separate-future-project",
    ),
    # ---------------------------------------------------------------- connectors
    Manifest(
        id="connector.public_web",
        kind="connector",
        title="Public web and prior-art retrieval",
        status="disabled",
        scopes=(),
        reason="external service switched off; no adapter exists and no scope is approved",
        external_service="public_web",
        spec_reference=f"{_SPEC}#02-deployment-and-processing-boundary-normative",
    ),
    Manifest(
        id="connector.telephony",
        kind="connector",
        title="Telephony",
        status="disabled",
        scopes=(),
        reason="external service switched off; jurisdiction and consent rules are open (Q17), "
               "call modes are open (Q22)",
        external_service="telephone_network",
        spec_reference=f"{_SPEC}#c-05--personal-assistant-call-handling",
    ),
    Manifest(
        id="connector.device_vendor_cloud",
        kind="connector",
        title="Device vendor clouds",
        status="disabled",
        scopes=(),
        reason="external service switched off; there is no device inventory (Q21, Q24)",
        external_service="device_vendor_cloud",
        spec_reference=f"{_SPEC}#02-deployment-and-processing-boundary-normative",
    ),
    Manifest(
        id="connector.update_downloads",
        kind="connector",
        title="Software and model update downloads",
        status="disabled",
        scopes=(),
        reason="external service switched off; no update pipeline exists",
        external_service="update_downloads",
        spec_reference=f"{_SPEC}#c-07--self-evolving-capabilities-with-bounded-autonomy",
    ),
)

MANIFESTS_BY_ID = {manifest.id: manifest for manifest in MANIFESTS}

#: Scopes that guard informational routes rather than a manifest.
ROUTE_SCOPES = frozenset({SCOPE_STATUS_READ, SCOPE_MANIFESTS_READ})

#: Every scope the server knows about: manifest scopes, route scopes and admin scopes.
ALL_SCOPES: frozenset[str] = frozenset(
    {scope for manifest in MANIFESTS for scope in manifest.scopes}
    | set(ROUTE_SCOPES)
    | set(ADMIN_SCOPES)
)


def describe() -> list[dict]:
    """The manifest set as JSON, for the client protocol and for tests."""
    return [manifest.to_dict() for manifest in MANIFESTS]


def get(manifest_id: str) -> Manifest:
    try:
        return MANIFESTS_BY_ID[manifest_id]
    except KeyError as exc:  # pragma: no cover - internal misuse
        raise KeyError(f"unknown manifest: {manifest_id}") from exc


def require_servable(manifest_id: str) -> Manifest:
    """Raise ``Unavailable`` unless the manifest may be served right now."""
    manifest = get(manifest_id)
    if not manifest.servable:
        raise Unavailable(
            f"{manifest.title} is {manifest.status}: {manifest.reason}",
            code="manifest_unavailable",
            detail={
                "manifest": manifest.id,
                "status": manifest.status,
                "reason": manifest.reason,
            },
        )
    return manifest


def validate_scopes(scope_ids) -> tuple[str, ...]:
    """Refuse unknown scopes outright; return a sorted, de-duplicated tuple."""
    unknown = sorted(set(scope_ids) - ALL_SCOPES)
    if unknown:
        raise ValueError(f"unknown scopes: {', '.join(unknown)}")
    return tuple(sorted(set(scope_ids)))
