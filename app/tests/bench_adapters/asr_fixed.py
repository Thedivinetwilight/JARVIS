"""Test double: a speech-recognition adapter that always returns the same sentence.

It exists so the harness can be exercised end to end without a real speech model. **Its numbers
are checks on the harness, never evidence about recognition**: it returns the string in
``BENCH_TEST_TEXT`` (or ``BENCH_TEST_TEXT_<language>``), so word error rate is whatever the test
chose it to be.

It does open the audio file, which is how the tests prove the adapter received real audio rather
than a placeholder.
"""

import os
import wave

ADAPTER = {
    "name": "test-double-fixed-asr",
    "version": "1.0.0",
    "kind": "asr",
    "languages": ["en"],
    "streaming": False,
    "notes": "harness test double; output is fixed by an environment variable",
}

DEFAULT_TEXT = "Lights on."


def transcribe(request):
    path = request.get("audio_path")
    if not path:
        raise ValueError("the harness did not supply an audio path")
    with wave.open(path, "rb") as handle:
        if handle.getnframes() <= 0:
            raise ValueError("the fixture has no audio frames")
        if handle.getsampwidth() != 2:
            raise ValueError("the fixture is not 16-bit PCM")
    language = request.get("language") or ""
    text = (os.environ.get(f"BENCH_TEST_TEXT_{language}")
            or os.environ.get("BENCH_TEST_TEXT")
            or DEFAULT_TEXT)
    return {"text": text, "language": language}
