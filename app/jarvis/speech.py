"""Speech adapter contracts — declared, not implemented.

The product is voice-first. The server therefore exposes the *shapes* that a local speech
recogniser and a local speech synthesiser must satisfy, and reports both as unavailable
until an adapter is chosen. Choosing one is part of the model decision that gate **G-O1**
keeps blocked, so this build refuses to configure anything other than ``unavailable``
(``config.load_config`` enforces that).

Nothing here produces text or audio. A "transcript" is never fabricated, and no route
returns an answer as text where audio was expected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .errors import Unavailable


class Recogniser(Protocol):
    """Local speech-to-text contract. Implementations must run on this server."""

    name: str

    def recognise(self, audio_path: str, *, language_hint: str | None) -> dict:
        """Return ``{"text": str, "confidence": float, "language": str}`` or raise."""


class Synthesiser(Protocol):
    """Local text-to-speech contract. Implementations must run on this server."""

    name: str

    def synthesise(self, utterance: dict, *, language: str | None) -> bytes:
        """Return encoded audio bytes or raise."""


@dataclass(frozen=True)
class UnavailableRecogniser:
    name: str = "unavailable"
    reason: str = (
        "no local speech-recognition adapter is configured; recognising speech requires a "
        "selected, measured model (gate G-O1 is blocked), so audio is stored and not understood"
    )

    def recognise(self, audio_path: str, *, language_hint: str | None = None) -> dict:  # pragma: no cover
        raise Unavailable(self.reason, code="speech_asr_unavailable")


@dataclass(frozen=True)
class UnavailableSynthesiser:
    name: str = "unavailable"
    reason: str = (
        "no local speech-synthesis adapter is configured (gate G-O1 is blocked); the server "
        "never speaks an answer and never substitutes a text response for audio"
    )

    def synthesise(self, utterance: dict, *, language: str | None = None) -> bytes:  # pragma: no cover
        raise Unavailable(self.reason, code="speech_tts_unavailable")


def build_registry(adapters: dict[str, str]) -> dict:
    """Return the configured adapters. Only ``unavailable`` is accepted in this build."""
    registry: dict = {"asr": UnavailableRecogniser(), "tts": UnavailableSynthesiser()}
    for key, value in adapters.items():
        if value != "unavailable":  # pragma: no cover - config refuses this earlier
            raise Unavailable(
                f"adapter {key}={value!r} cannot be used: no model is selected",
                code="adapter_not_selectable",
            )
    return registry


def status(adapters: dict[str, str]) -> dict:
    """Capability report for the audio path; used by ``/v1/audio/capabilities``."""
    return {
        "input": {
            "supported": True,
            "kind": "audio",
            "note": "audio is accepted, stored locally and never sent anywhere",
        },
        "asr": {
            # Never true in this build, whatever the configuration says: the adapter that
            # exists is the one that refuses.
            "available": False,
            "adapter": adapters.get("speech_asr", "unavailable"),
            "reason": UnavailableRecogniser.reason,
        },
        "tts": {
            "available": adapters.get("speech_tts") != "unavailable" and False,
            "adapter": adapters.get("speech_tts", "unavailable"),
            "reason": UnavailableSynthesiser.reason,
        },
        "text_conversation": {
            "available": False,
            "reason": "the product has no conversational text interface (R-6, gate G-UX); no "
                      "text-chat endpoint exists on this server",
        },
    }
