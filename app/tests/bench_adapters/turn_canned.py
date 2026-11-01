"""Test double: a turn component that answers with a fixed sentence.

The optional turn phase exists so a run can time an assistant step *if the operator supplies one*.
This double proves the wiring; it does no reasoning and its text is constant.
"""

ADAPTER = {
    "name": "test-double-canned-turn",
    "version": "1.0.0",
    "kind": "turn",
    "languages": ["en"],
    "streaming": False,
    "notes": "harness test double; constant reply, no reasoning",
}

REPLY = "Acknowledged."


def respond(request):
    if not isinstance(request.get("text"), str):
        raise ValueError("the turn phase needs the recognised text")
    return {"text": REPLY}
