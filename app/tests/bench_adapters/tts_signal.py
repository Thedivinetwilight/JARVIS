"""Test double: a speech-generation adapter that reports a fixed amount of audio.

No audio is actually produced; the harness only needs the size and rate to compute a real-time
factor and to time first output. Nothing here is a speech synthesiser.
"""

ADAPTER = {
    "name": "test-double-fixed-tts",
    "version": "1.0.0",
    "kind": "tts",
    "languages": [],
    "streaming": False,
    "notes": "harness test double; reports a fixed audio length",
}

SECONDS = 0.5
SAMPLE_RATE = 16000


def synthesise(request):
    text = request.get("text") or ""
    if not isinstance(text, str):
        raise ValueError("the harness did not supply text")
    return {
        "audio_bytes": int(SECONDS * SAMPLE_RATE * 2),
        "sample_rate": SAMPLE_RATE,
        "duration_s": SECONDS,
    }
