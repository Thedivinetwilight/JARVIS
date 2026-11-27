"""Capability manifests: least-privilege scope contracts.

Ported from the bbff branch (app/jarvis/manifests.py) and adapted to the
Genesis kernel. A manifest declares what JARVIS can do, which scope it needs,
and whether it is available. Routes and tools are authorised against these
manifests, so a scope cannot be used for a capability that does not exist.

Two rules:
- a manifest whose status is not ``available`` cannot be served (503);
- every external connector is ``disabled`` until explicitly enabled.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

# Scope ids
SCOPE_STATUS_READ = "status.read"
SCOPE_MANIFESTS_READ = "manifests.read"
SCOPE_AUDIT_READ = "audit.read"
SCOPE_CLIENTS_READ = "clients.read"
SCOPE_CLIENTS_REVOKE = "clients.revoke"
SCOPE_CLIENTS_PAIR = "clients.pair"
SCOPE_TASKS_READ = "tasks.read"
SCOPE_TASKS_WRITE = "tasks.write"
SCOPE_REMINDERS_READ = "reminders.read"
SCOPE_REMINDERS_WRITE = "reminders.write"
SCOPE_REMINDERS_RECEIVE = "reminders.receive"
SCOPE_AUDIO_CAPTURE = "audio.capture"
SCOPE_SPEECH_RECOGNIZE = "speech.recognize"
SCOPE_SPEECH_OUTPUT = "speech.output"
SCOPE_SURFACE_RENDER = "surface.render"
SCOPE_MEMORY_READ = "memory.read"
SCOPE_MEMORY_WRITE = "memory.write"
SCOPE_GOALS_READ = "goals.read"
SCOPE_GOALS_WRITE = "goals.write"
SCOPE_OPERATIONS_READ = "operations.read"
SCOPE_OPERATIONS_WRITE = "operations.write"
SCOPE_PLANS_READ = "plans.read"
SCOPE_PLANS_WRITE = "plans.write"
SCOPE_CONVERSE = "converse"

ADMIN_SCOPES = frozenset({SCOPE_CLIENTS_READ, SCOPE_CLIENTS_REVOKE, SCOPE_CLIENTS_PAIR,
                          SCOPE_AUDIT_READ})

DEFAULT_DEVICE_SCOPES = frozenset({
    SCOPE_STATUS_READ, SCOPE_MANIFESTS_READ,
    SCOPE_TASKS_READ, SCOPE_TASKS_WRITE,
    SCOPE_REMINDERS_READ, SCOPE_REMINDERS_WRITE, SCOPE_REMINDERS_RECEIVE,
    SCOPE_AUDIO_CAPTURE,
    SCOPE_MEMORY_READ, SCOPE_MEMORY_WRITE,
    SCOPE_GOALS_READ, SCOPE_GOALS_WRITE,
    SCOPE_OPERATIONS_READ,
    SCOPE_PLANS_READ,
    SCOPE_CONVERSE,
})

OPERATOR_SCOPES = frozenset(
    set(DEFAULT_DEVICE_SCOPES)
    | ADMIN_SCOPES
    | {SCOPE_SPEECH_RECOGNIZE, SCOPE_SPEECH_OUTPUT, SCOPE_SURFACE_RENDER,
       SCOPE_OPERATIONS_WRITE, SCOPE_PLANS_WRITE}
)

ALL_SCOPES = frozenset(
    set(DEFAULT_DEVICE_SCOPES) | ADMIN_SCOPES
    | {SCOPE_SPEECH_RECOGNIZE, SCOPE_SPEECH_OUTPUT, SCOPE_SURFACE_RENDER,
       SCOPE_OPERATIONS_WRITE, SCOPE_PLANS_WRITE}
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

    @property
    def implemented(self) -> bool:
        return self.status == "available"

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["servable"] = self.servable
        payload["implemented"] = self.implemented
        payload["scopes"] = list(self.scopes)
        return payload


MANIFESTS: tuple[Manifest, ...] = (
    Manifest(
        id="memory",
        kind="capability",
        title="Persistent memory (versioned, provenance-tracked)",
        status="available",
        scopes=(SCOPE_MEMORY_READ, SCOPE_MEMORY_WRITE),
        notes="Versioned memories with epistemic status, supersession chains, "
              "FTS5 retrieval, and erasure via forget.",
    ),
    Manifest(
        id="goals",
        kind="capability",
        title="Goals and tasks (evidence-based completion)",
        status="available",
        scopes=(SCOPE_GOALS_READ, SCOPE_GOALS_WRITE),
        notes="Goals with ordered steps, task dependencies, blockers, and "
              "evidence-gated completion.",
    ),
    Manifest(
        id="tasks",
        kind="capability",
        title="Tasks (structured records)",
        status="available",
        scopes=(SCOPE_TASKS_READ, SCOPE_TASKS_WRITE),
        notes="Structured task records with due times and delivery targets.",
    ),
    Manifest(
        id="reminders",
        kind="capability",
        title="Reminders (local scheduling, delivery queue)",
        status="available",
        scopes=(SCOPE_REMINDERS_READ, SCOPE_REMINDERS_WRITE, SCOPE_REMINDERS_RECEIVE),
        notes="A reminder is queued only for a client named explicitly at creation.",
    ),
    Manifest(
        id="operations",
        kind="capability",
        title="Operation journal (write-ahead, auditable)",
        status="available",
        scopes=(SCOPE_OPERATIONS_READ, SCOPE_OPERATIONS_WRITE),
        notes="Every capability invocation is journaled before execution.",
    ),
    Manifest(
        id="plans",
        kind="capability",
        title="Persistent plans (bounded multi-step execution)",
        status="available",
        scopes=(SCOPE_PLANS_READ, SCOPE_PLANS_WRITE),
        notes="Plans persist steps and bounded retries; execution goes through the "
              "permission-checked tool registry, never around it.",
    ),
    Manifest(
        id="audio",
        kind="capability",
        title="Audio sessions (local capture, bounded, expiring)",
        status="available",
        scopes=(SCOPE_AUDIO_CAPTURE,),
        notes="Audio chunks stored locally with expiry and purge on revocation.",
    ),
    Manifest(
        id="speech_recognition",
        kind="capability",
        title="Speech recognition (ASR)",
        status="unavailable",
        scopes=(SCOPE_SPEECH_RECOGNIZE,),
        reason="speech_asr_unavailable",
        notes="No ASR adapter exists in this build. Voice input via Termux:API "
              "is a separate interface (voice.py), not a server capability.",
    ),
    Manifest(
        id="speech_synthesis",
        kind="capability",
        title="Speech synthesis (TTS)",
        status="unavailable",
        scopes=(SCOPE_SPEECH_OUTPUT,),
        reason="speech_tts_unavailable",
        notes="No TTS adapter exists. Spoken output is refused, not substituted with text.",
    ),
    Manifest(
        id="text_conversation",
        kind="capability",
        title="Text conversation (natural-language chat)",
        status="available",
        scopes=(SCOPE_STATUS_READ,),
        notes="Free-form conversation via the cognitive kernel (CLI, REPL, API). "
              "Deterministic interpreter + optional model drafting.",
    ),
    Manifest(
        id="web_retrieval",
        kind="connector",
        title="Web retrieval",
        status="disabled",
        scopes=(),
        external_service="public_web",
        reason="Network access is disabled by default. Enable JARVIS_NETWORK_ENABLED "
               "and declare the service to use.",
        notes="No web retrieval adapter exists. Enabling requires explicit declaration.",
    ),
    Manifest(
        id="telephony",
        kind="connector",
        title="Telephony (calls)",
        status="disabled",
        scopes=(),
        external_service="telephone_network",
        reason="No telephony adapter exists.",
    ),
    Manifest(
        id="device_cloud",
        kind="connector",
        title="Device vendor cloud",
        status="disabled",
        scopes=(),
        external_service="device_vendor_cloud",
        reason="No device cloud adapter exists.",
    ),
    Manifest(
        id="update_downloads",
        kind="connector",
        title="Update downloads",
        status="disabled",
        scopes=(),
        external_service="update_downloads",
        reason="Software and model updates are not automated.",
    ),
    Manifest(
        id="holographic_display",
        kind="surface",
        title="Holographic display",
        status="boundary-only",
        scopes=(SCOPE_SURFACE_RENDER,),
        reason="Display technology is boundary-only; no rendering exists.",
    ),
)


def get_manifest(manifest_id: str) -> Manifest | None:
    for m in MANIFESTS:
        if m.id == manifest_id:
            return m
    return None


def validate_scopes(scopes) -> tuple[str, ...]:
    """Validate scope names; raise ValueError for unknown scopes."""
    result = tuple(sorted(set(scopes)))
    unknown = set(result) - ALL_SCOPES
    if unknown:
        raise ValueError(f"Unknown scopes: {sorted(unknown)}")
    return result


def manifests_dict() -> list[dict]:
    return [m.to_dict() for m in MANIFESTS]
