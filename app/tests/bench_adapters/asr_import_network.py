"""Test double: an adapter that reaches for the network while it is being imported.

Import happens *after* the sandbox is installed, so this must be caught too. An adapter cannot
escape the boundary by doing its outbound work at import time.
"""

import socket

socket.getaddrinfo("example.invalid", 443)

ADAPTER = {
    "name": "test-double-import-network-asr",
    "version": "1.0.0",
    "kind": "asr",
    "languages": ["en"],
    "streaming": False,
    "notes": "harness test double; attempts a name lookup at import time",
}


def transcribe(request):
    return {"text": "Lights on."}
