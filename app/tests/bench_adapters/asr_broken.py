"""Test double: a speech-recognition adapter that always fails.

Used to prove that an adapter failure is recorded as data — a failure entry and an ``incomplete``
or ``failed`` run — and never as a zero measurement or a silent success.
"""

ADAPTER = {
    "name": "test-double-failing-asr",
    "version": "1.0.0",
    "kind": "asr",
    "languages": ["en"],
    "streaming": False,
    "notes": "harness test double; raises on every request",
}


def transcribe(request):
    raise RuntimeError("simulated adapter failure: this adapter never recognises anything")
