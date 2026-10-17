"""Optional voice adapter for Android/Termux (Termux:API), roadmap item R2.

Voice is an *interface*, not a separate mind: each utterance goes through the same
cognitive cycle (``Jarvis.ask`` with channel ``voice``), so memory, goals, permissions,
redaction and retention apply unchanged.

Commands used (provided by the Termux:API add-on; see docs/RESEARCH.md):
- ``termux-speech-to-text`` — prints partial recognitions to stdout; the last non-empty
  line is taken as the utterance;
- ``termux-tts-speak`` — speaks text read from stdin.

Availability is probed, never assumed: if either command is missing the adapter reports
itself unavailable and the CLI explains how to enable it. Verified only with simulated
commands in tests — not yet on a real Android device.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

STT_COMMAND = "termux-speech-to-text"
TTS_COMMAND = "termux-tts-speak"
STOP_PHRASES = ("stop listening", "goodbye jarvis", "exit voice", "stop voice")
MAX_SPOKEN_CHARS = 600


@dataclass(frozen=True)
class VoiceProbe:
    available: bool
    reason: str
    stt: str | None
    tts: str | None


def probe() -> VoiceProbe:
    stt, tts = shutil.which(STT_COMMAND), shutil.which(TTS_COMMAND)
    if stt and tts:
        return VoiceProbe(True, "Termux:API speech commands found", stt, tts)
    missing = [name for name, path in ((STT_COMMAND, stt), (TTS_COMMAND, tts)) if not path]
    return VoiceProbe(False, f"not found on PATH: {', '.join(missing)} (install the Termux:API app and "
                             "`pkg install termux-api`)", stt, tts)


def speakable(text: str) -> str:
    """Make a response suitable for speech: drop symbols and markup, bound the length."""
    text = re.sub(r"[✓✗→·•⚠–]", " ", text)
    text = re.sub(r"[#*_`>]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS].rsplit(". ", 1)[0]
        text = cut.rstrip(".") + ". The rest is on screen."
    return text


class TermuxVoice:
    name = "termux"

    def __init__(self, probe_result: VoiceProbe, *, listen_timeout: float = 60, speak_timeout: float = 120):
        if not probe_result.available:
            raise RuntimeError(f"Voice unavailable: {probe_result.reason}")
        self.stt, self.tts = probe_result.stt, probe_result.tts
        self.listen_timeout, self.speak_timeout = listen_timeout, speak_timeout

    def listen(self) -> str | None:
        """Return the recognised utterance, or None if nothing was recognised."""
        try:
            proc = subprocess.run([self.stt], capture_output=True, text=True, timeout=self.listen_timeout)
        except subprocess.TimeoutExpired:
            return None
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return lines[-1] if proc.returncode == 0 and lines else None

    def speak(self, text: str) -> bool:
        spoken = speakable(text)
        if not spoken:
            return True
        try:
            proc = subprocess.run([self.tts], input=spoken, text=True, capture_output=True, timeout=self.speak_timeout)
        except subprocess.TimeoutExpired:
            return False
        return proc.returncode == 0


def voice_loop(ask: Callable[..., dict[str, Any]], io: Any, *, max_turns: int | None = None,
               max_silences: int = 3, echo: Callable[[str], None] = print) -> dict[str, Any]:
    """Listen → cognitive cycle → speak, until a stop phrase, ``max_turns`` or repeated silence."""
    turns = silences = 0
    session_id = None
    while max_turns is None or turns < max_turns:
        heard = io.listen()
        if not heard:
            silences += 1
            if silences >= max_silences:
                echo("(no speech recognised; leaving voice mode)")
                break
            continue
        silences = 0
        echo(f"you (voice) › {heard}")
        if heard.lower().strip(" .!") in STOP_PHRASES:
            io.speak("Okay. Your session is saved.")
            break
        result = ask(heard, channel="voice", session_id=session_id)
        session_id = result["session_id"]
        echo(f"jarvis › {result['answer']}")
        if not io.speak(result["answer"]):
            echo("(speech output failed; the answer is shown above)")
        turns += 1
    return {"turns": turns, "session_id": session_id}
