"""The product capability register, mirrored for reporting only.

The server must never tell a client that a product capability works. This module holds the
capability ids and their **specification** status, copied from
``docs/product/capabilities.json``; ``app/tests/test_capabilities.py`` fails if the two
drift apart. Infrastructure that exists (this server) is reported separately, so "the
server is running" can never be read as "C-03 voice interaction is implemented".
"""

from __future__ import annotations

#: Product specification version this register mirrors.
SPEC_VERSION = "1.2.0"

#: id -> specification status, exactly as published. No capability is implemented.
PRODUCT_CAPABILITIES: tuple[tuple[str, str, str], ...] = (
    ("C-01", "Personal context and preferences", "specified \u00b7 harness-only"),
    ("C-02", "Modular knowledge stores and people database", "specified"),
    ("C-03", "Voice-first interaction, multilingual, no chat boxes", "specified"),
    ("C-04", "Speech recognition and authorized-speaker verification", "specified"),
    ("C-05", "Personal-assistant call handling (attend, handle, escalate, summarise)", "specified"),
    ("C-06", "Brain-inspired architecture without consciousness as a goal", "specified \u00b7 harness-only"),
    ("C-07", "Self-evolving capabilities with bounded autonomy", "specified \u00b7 harness-only"),
    ("C-08", "Invention support", "specified"),
    ("C-09", "Limitation explanation and paths forward", "specified \u00b7 harness-only"),
    ("C-10", "MCU J.A.R.V.I.S. practical equivalents and Rescue support", "specified"),
    ("C-11", "Smart-home control and monitoring", "specified"),
    ("C-12", "Connectors: scoped account and device access", "specified"),
    ("C-13", "Home holographic interface (voice-first spatial display)", "specified"),
    ("C-14", "Device surfaces and platform limits", "specified"),
)

#: What this server actually implements. Infrastructure only, plus one model-free capability.
INFRASTRUCTURE: tuple[tuple[str, str, str], ...] = (
    ("config", "implemented", "explicit configuration with startup validation"),
    ("auth.pairing", "implemented", "single-use pairing codes, hashed tokens, revocation"),
    ("auth.scopes", "implemented", "per-manifest least-privilege scopes with expiry"),
    ("audit", "implemented", "append-only audit of requests, pairings and refusals"),
    ("storage", "implemented", "SQLite with checksum-verified migrations"),
    ("egress", "implemented", "default-deny outbound network policy"),
    ("health", "implemented", "health, readiness and limitation reporting"),
    ("audio.input", "implemented", "authenticated audio session boundary; no transcription"),
    ("tasks", "implemented", "model-free task records"),
    ("reminders", "implemented", "local due-reminder reporting; no delivery channel"),
    ("surface.holographic", "boundary-only", "client/API boundary; no optics, no display"),
    ("adapters.speech_asr", "unavailable", "no local speech adapter; G-O1 blocked"),
    ("adapters.speech_tts", "unavailable", "no local speech adapter; G-O1 blocked"),
    ("model", "not-selected", "gate G-O1 is blocked; no model, provider or hardware chosen"),
    ("clients", "not-built", "no client application exists for any device target"),
    ("connectors", "disabled", "web, telephony, vendor clouds and updates all switched off"),
)


def capability_report() -> list[dict]:
    return [
        {
            "id": capability_id,
            "title": title,
            "spec_status": spec_status,
            "implementation": "not implemented",
            "note": "specified only; this server is infrastructure, not the capability",
        }
        for capability_id, title, spec_status in PRODUCT_CAPABILITIES
    ]


def infrastructure_report() -> list[dict]:
    return [
        {"component": component, "status": status, "detail": detail}
        for component, status, detail in INFRASTRUCTURE
    ]
