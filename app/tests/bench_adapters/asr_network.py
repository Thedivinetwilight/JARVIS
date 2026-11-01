"""Test double: a speech-recognition adapter that tries to reach the network.

The connection is attempted from inside ``transcribe`` (not at import time) so the test covers the
per-request path as well as the import-time one. The sandbox must refuse it, count it, end the run
and mark the result ``violated`` with a ``rejected`` decision.
"""

import socket

ADAPTER = {
    "name": "test-double-network-asr",
    "version": "1.0.0",
    "kind": "asr",
    "languages": ["en"],
    "streaming": False,
    "notes": "harness test double; deliberately attempts a local connection",
}


def transcribe(request):
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=0.2).close()
    except OSError:
        pass  # swallowing the refusal must not hide it: the counter lives outside the adapter
    return {"text": "Lights on."}
