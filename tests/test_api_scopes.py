"""API authorisation with paired clients: pairing, scope denial, revocation, audio, plans.

Real HTTP against a real loopback server and a real SQLite store. The legacy static
token is still exercised by tests/test_api.py; here every request uses a scoped token.
"""
from __future__ import annotations

import base64
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from jarvis import manifests
from jarvis.api import make_handler

TOKEN = "legacy-operator-token-" + "y" * 32


@pytest.fixture
def server(app_factory):
    app = app_factory()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, token=TOKEN))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield app, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=data, headers=headers)
    response = conn.getresponse()
    payload = json.loads(response.read() or b"null")
    conn.close()
    return response.status, payload


@pytest.fixture
def device_token(server):
    app, port = server
    issued = app.auth.create_pairing_code(label_hint="pixel", platform="android")
    return issued["code"]


def _redeem(port, code):
    status, payload = call(port, "POST", "/v1/pairing", {"code": code}, token=None)
    assert status == 200, payload
    return payload["token"], payload["client"]


def test_pairing_is_the_only_tokenless_route(server):
    _, port = server
    assert call(port, "GET", "/health", token=None)[0] == 200
    assert call(port, "GET", "/v1/status", token=None)[0] == 401
    assert call(port, "POST", "/v1/ask", {"message": "hi"}, token=None)[0] == 401
    assert call(port, "GET", "/v1/plans", token=None)[0] == 401


def test_pairing_code_yields_a_scoped_device_token(server, device_token):
    app, port = server
    token, client = _redeem(port, device_token)
    assert client["kind"] == "device" and client["platform"] == "android"
    assert set(client["scopes"]) == set(manifests.DEFAULT_DEVICE_SCOPES)
    assert call(port, "GET", "/v1/status", token=token)[0] == 200
    assert call(port, "GET", "/health", token=None)[0] == 200
    # single use
    assert call(port, "POST", "/v1/pairing", {"code": device_token}, token=None)[0] == 401
    # the raw token is never stored
    with app.store.read() as db:
        digests = [r["token_digest"] for r in db.execute("SELECT token_digest FROM clients")]
    assert token not in digests


def test_device_token_cannot_reach_admin_routes(server, device_token):
    _, port = server
    token, _ = _redeem(port, device_token)
    status, payload = call(port, "GET", "/v1/clients", token=token)
    assert status == 403 and payload["scope"] == manifests.SCOPE_CLIENTS_READ
    status, payload = call(port, "GET", "/v1/events", token=token)
    assert status == 403 and payload["scope"] == manifests.SCOPE_AUDIT_READ
    status, payload = call(port, "POST", "/v1/plans", {"title": "x", "steps": []}, token=token)
    assert status == 403 and payload["scope"] == manifests.SCOPE_PLANS_WRITE
    # the legacy operator token still has everything
    assert call(port, "GET", "/v1/clients")[0] == 200


def test_device_token_can_use_its_own_scopes(server, device_token):
    _, port = server
    token, client = _redeem(port, device_token)
    status, reminder = call(port, "POST", "/v1/reminders",
                            {"title": "drink water", "due_at": "2026-01-01T00:00:00+00:00",
                             "delivery_client_id": client["id"]}, token=token)
    assert status == 201 and reminder["title"] == "drink water"
    status, inbox = call(port, "GET", "/v1/reminders/inbox", token=token)
    assert status == 200 and len(inbox) == 1           # a device only sees its own queue
    delivery_id = inbox[0]["id"]
    assert call(port, "POST", f"/v1/reminders/inbox/{delivery_id}/ack", {}, token=token)[0] == 200
    assert call(port, "GET", "/v1/reminders/inbox", token=token)[1] == []
    assert call(port, "POST", "/v1/ask", {"message": "remember that the scope test ran"},
                token=token)[0] == 200
    assert call(port, "GET", "/v1/manifests", token=token)[1][0]["scopes"]


def test_audio_capture_bounds_surface_as_client_errors(server, device_token):
    _, port = server
    token, _ = _redeem(port, device_token)
    status, session = call(port, "POST", "/v1/audio/sessions", {}, token=token)
    assert status == 201 and session["status"] == "open"
    audio = base64.b64encode(b"\x00\x01" * 32).decode()
    status, chunk = call(port, "POST", f"/v1/audio/sessions/{session['id']}/audio",
                         {"data": audio, "mime_type": "audio/wav"}, token=token)
    assert status == 201 and chunk["size_bytes"] == 64
    assert call(port, "POST", f"/v1/audio/sessions/{session['id']}/audio",
                {"data": "not base64 %%%"}, token=token)[0] == 400
    status, closed = call(port, "POST", f"/v1/audio/sessions/{session['id']}/end", {}, token=token)
    assert status == 200 and closed["status"] == "closed"
    assert call(port, "GET", "/v1/audio/capabilities", token=token)[1]["asr"] is False


def test_revocation_invalidates_the_token_and_purges_its_audio(server, device_token):
    app, port = server
    token, client = _redeem(port, device_token)
    session = call(port, "POST", "/v1/audio/sessions", {}, token=token)[1]
    call(port, "POST", f"/v1/audio/sessions/{session['id']}/audio",
         {"data": base64.b64encode(b"\x00" * 8).decode()}, token=token)

    assert call(port, "POST", f"/v1/clients/{client['id']}/revoke", {})[0] == 200
    assert call(port, "GET", "/v1/status", token=token)[0] == 401
    with app.store.read() as db:
        assert db.execute("SELECT status FROM audio_sessions").fetchone()["status"] == "purged"
        assert db.execute("SELECT COUNT(*) FROM audio_chunks").fetchone()[0] == 0


def test_plan_execution_over_http_requires_the_write_scope(server, device_token):
    app, port = server
    plan = app.plans.create_plan(title="http plan", steps=[
        {"capability": "time.now", "arguments": {}, "purpose": "check the clock"}])
    token, _ = _redeem(port, device_token)
    assert call(port, "GET", f"/v1/plans/{plan.id}", token=token)[0] == 200     # plans.read
    status, payload = call(port, "POST", f"/v1/plans/{plan.id}/execute", {}, token=token)
    assert status == 403 and payload["scope"] == manifests.SCOPE_PLANS_WRITE
    status, executed = call(port, "POST", f"/v1/plans/{plan.id}/execute", {})
    assert status == 200 and executed["status"] == "completed"
    assert call(port, "GET", "/v1/plans")[1][0]["progress"]["succeeded"] == 1


def test_operator_can_mint_a_pairing_code_over_http(server):
    _, port = server
    status, issued = call(port, "POST", "/v1/clients/pairing",
                          {"label": "tablet", "platform": "ipados"})
    assert status == 201 and issued["code"].count("-") == 2
    assert set(issued["scopes"]) <= set(manifests.DEFAULT_DEVICE_SCOPES)
    status, payload = call(port, "POST", "/v1/clients/pairing", {"scopes": ["clients.revoke"]})
    assert status == 400 and payload["error"] == "invalid_scopes"
