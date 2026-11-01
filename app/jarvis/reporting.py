"""Health, readiness and limitation reporting.

Three different questions, kept apart:

* ``/healthz``  — is the process alive? (never fails on capability grounds)
* ``/readyz``   — can it serve? (database migrated, data directory writable, egress guard on)
* ``/v1/limitations``, ``/v1/status`` — what can it *not* do, and why (authenticated)

A limitation is a first-class object with a stable code, so a client can show it instead of
inventing behaviour. No response in this module claims a product capability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .capabilities import SPEC_VERSION, capability_report, infrastructure_report

if TYPE_CHECKING:  # pragma: no cover
    from .app import App


def limitation_catalogue() -> list[dict]:
    """Everything the server currently cannot do, with a code and a reason."""
    return [
        {
            "code": "speech_asr_unavailable",
            "title": "Speech recognition is unavailable",
            "detail": "no local speech-to-text adapter is configured; audio is stored and not "
                      "understood. Selecting an adapter is part of gate G-O1, which is blocked.",
            "affects": ["C-03", "C-04"],
            "spec_reference": "docs/product/capability-specification.md#c-04--speech-recognition-and-authorized-speaker-verification",
        },
        {
            "code": "speech_tts_unavailable",
            "title": "Spoken output is unavailable",
            "detail": "no local speech-synthesis adapter is configured, so the server cannot read "
                      "an answer aloud and does not substitute text for audio.",
            "affects": ["C-03"],
            "spec_reference": "docs/product/capability-specification.md#c-03--voice-first-interaction-multilingual-no-chat-boxes",
        },
        {
            "code": "text_conversation_not_supported",
            "title": "There is no text conversation, by design",
            "detail": "the product has no conversational text interface (R-6, gate G-UX). No "
                      "text-chat endpoint exists; do not add one.",
            "affects": ["C-03"],
            "spec_reference": "docs/product/capability-specification.md#2-cross-cutting-rules",
        },
        {
            "code": "holographic_surface_no_display",
            "title": "The home holographic interface is a boundary only",
            "detail": "no display technology is selected and no optics exist; the server exposes "
                      "the protocol boundary and reports the surface as boundary-only.",
            "affects": ["C-13"],
            "spec_reference": "docs/product/capability-specification.md#c-13--home-holographic-interface-voice-first-spatial-display",
        },
        {
            "code": "no_model_selected",
            "title": "No model is selected",
            "detail": "gate G-O1 is blocked: the workload mapping, the measured server baseline "
                      "and the owner's inputs are outstanding. Nothing here reasons about language.",
            "affects": ["C-01", "C-02", "C-08", "C-09", "C-13"],
            "spec_reference": "docs/product/capability-roadmap.md#4-gates-and-why-model-selection-is-blocked",
        },
        {
            "code": "language_support_unmeasured",
            "title": "No language is supported yet",
            "detail": "the fifteen-language list is proposal PR-1 (Q19 open); no language has a "
                      "measured result, so none may be described as working.",
            "affects": ["C-03"],
            "spec_reference": "docs/product/capability-specification.md#5-open-items-blocking-review",
        },
        {
            "code": "external_services_disabled",
            "title": "Every external service is off",
            "detail": "web retrieval, telephony, device vendor clouds and update downloads are "
                      "declared but disabled, with no adapter and no approved scope.",
            "affects": ["C-05", "C-08", "C-11", "C-12"],
            "spec_reference": "docs/product/capability-specification.md#02-deployment-and-processing-boundary-normative",
        },
        {
            "code": "no_client_applications",
            "title": "No device client exists",
            "detail": "there is no application for PC, tablet, phone, VR or glasses; the device "
                      "matrix lists intent and limits, not support.",
            "affects": ["C-14"],
            "spec_reference": "docs/product/device-support-matrix.md",
        },
        {
            "code": "reminder_delivery_unavailable",
            "title": "Reminders are queued for a paired client, never pushed, spoken or shown",
            "detail": "the server can list due reminders and queue one for a client that was "
                      "named explicitly, which retrieves it by polling and acknowledges it. It "
                      "has no push channel, no speech and no display, so it never claims to "
                      "have spoken, shown or otherwise reminded anyone.",
            "affects": ["C-03", "C-13"],
            "spec_reference": "docs/product/capability-specification.md#1-requirement-summary",
        },
        {
            "code": "natural_language_entry_unavailable",
            "title": "Tasks are structured input only",
            "detail": "tasks accept an explicit title, note and due time. Parsing a spoken "
                      "request into a task needs the speech adapters and a model; both are absent.",
            "affects": ["C-01", "C-03"],
            "spec_reference": "docs/product/capability-specification.md#c-03--voice-first-interaction-multilingual-no-chat-boxes",
        },
        {
            "code": "device_platform_roles_not_implemented",
            "title": "No platform call roles are implemented",
            "detail": "Android screening/dialer roles and iPhone VoIP are platform mechanisms the "
                      "owner must grant; none is used by this server, and cellular call audio is "
                      "not reachable at all.",
            "affects": ["C-05", "C-14"],
            "spec_reference": "docs/product/device-support-matrix.md#3-telephony-limits-the-part-most-likely-to-be-overpromised",
        },
    ]


def health_payload(app: "App") -> dict:
    return {
        "status": "ok",
        "version": app.version,
        "uptime_s": app.uptime_s(),
        "started_at": app.started_at_iso,
    }


def readiness_payload(app: "App") -> tuple[bool, dict]:
    storage = app.store.status()
    writable = app.data_dir_writable()
    guard = app.guard.installed
    checks = {
        "database": {
            "ok": storage["up_to_date"],
            "detail": "migrations up to date" if storage["up_to_date"]
            else f"pending migrations: {', '.join(storage['pending'])}",
        },
        "storage": {"ok": writable, "detail": "data directory writable" if writable else "not writable"},
        "egress_guard": {
            "ok": guard,
            "detail": "default-deny guard installed" if guard else "guard not installed",
        },
        "adapters": {
            "ok": True,
            "detail": "speech adapters unavailable by design; readiness is not a capability claim",
            "speech": dict(app.config.adapters),
        },
    }
    ready = all(check["ok"] for check in checks.values())
    return ready, {"ready": ready, "checks": checks}


def status_payload(app: "App") -> dict:
    ready, readiness = readiness_payload(app)
    storage = app.store.status()
    return {
        "server": {
            "version": app.version,
            "uptime_s": app.uptime_s(),
            "started_at": app.started_at_iso,
            "bind": app.config.server.bind,
            "port": app.config.server.port,
            "tls": app.config.server.tls_enabled,
            "processing": "local-only",
        },
        "ready": ready,
        "readiness": readiness,
        "storage": {
            "database": storage["path"],
            "migrations_applied": storage["applied"],
            "migrations_pending": storage["pending"],
            "audio_bytes": app.audio.storage_bytes(),
            "tasks": app.tasks.counts(),
            "reminder_deliveries": app.deliveries.counts(),
            "clients": len(app.auth.list_clients()),
        },
        "egress": app.guard.policy(),
        "product_spec": {
            "spec_version": SPEC_VERSION,
            "source": "docs/product/capabilities.json",
            "capabilities": capability_report(),
        },
        "infrastructure": infrastructure_report(),
        "config": app.config.public_dict(),
        "limitations": limitation_catalogue(),
    }


def limitations_payload(app: "App") -> dict:
    return {
        "count": len(limitation_catalogue()),
        "limitations": limitation_catalogue(),
        "note": "each limitation has a stable code; clients should show it rather than "
                "improvise behaviour",
    }
