"""HTTP API against a live loopback server: endpoints, request-origin defences and
client authorisation (real HTTP requests; the real `jarvis serve` process in the last test)."""
from __future__ import annotations

import http.client
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from conftest import ROOT
from jarvis.api import load_or_create_token, make_handler

TOKEN = "test-token-" + "x" * 32


@pytest.fixture
def server(app_factory):
    app = app_factory()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, token=TOKEN))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield app, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, headers=None, token=TOKEN, with_headers=False):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    hdrs = {"Content-Type": "application/json"} if data is not None else {}
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    hdrs.update(headers or {})
    conn.request(method, path, body=data, headers=hdrs)
    response = conn.getresponse()
    payload = json.loads(response.read() or b"null")
    response_headers = dict(response.getheaders())
    conn.close()
    return (response.status, payload, response_headers) if with_headers else (response.status, payload)


def test_health_status_and_ask(server):
    app, port = server
    assert call(port, "GET", "/health")[1]["ok"] is True
    status, result = call(port, "POST", "/v1/ask", {"message": "remember that the API works"})
    assert status == 200 and result["status"] == "completed" and result["actions"][0]["verified"] is True
    assert call(port, "GET", "/v1/status")[1]["memories"] == 1
    assert call(port, "GET", f"/v1/cycles/{result['cycle_id']}")[1]["cycle"]["status"] == "completed"
    hits = call(port, "GET", "/v1/memory/search?q=api+works")[1]
    assert hits and hits[0]["value"] == "the API works"
    assert call(port, "GET", "/v1/state")[1]["identity"]["system_id"] == app.identity["system_id"]


def test_cross_site_and_rebinding_requests_are_rejected(server):
    _, port = server
    assert call(port, "GET", "/v1/status", headers={"Host": "evil.example:8765"})[0] == 403
    assert call(port, "POST", "/v1/ask", {"message": "hi"}, headers={"Origin": "https://evil.example"})[0] == 403
    status, _ = call(port, "POST", "/v1/ask", b'{"message": "hi"}', headers={"Content-Type": "text/plain"})
    assert status == 415
    assert call(port, "OPTIONS", "/v1/ask")[0] == 405
    assert call(port, "POST", "/v1/ask", {"message": "hi"}, headers={"Origin": "http://localhost:3000"})[0] == 200
    # Sandboxed iframes, file:// pages and some redirects send "Origin: null".
    assert call(port, "POST", "/v1/ask", {"message": "hi"}, headers={"Origin": "null"})[0] == 403


def test_approval_endpoint_and_input_validation(server):
    app, port = server
    app.ask("remember that the shed code hint is the year we moved")
    proposal = app.ask("forget about the shed code hint")
    op_id = proposal["actions"][0]["operation_id"]
    status, outcome = call(port, "POST", f"/v1/operations/{op_id}/approve", {})
    assert status == 200 and outcome["status"] == "succeeded" and outcome["verified"] is True
    assert app.store.operations.get(op_id)["approved_by"] == "user:api"
    assert call(port, "POST", "/v1/ask", b"{bad")[0] == 400
    assert call(port, "POST", "/v1/ask", {"message": "x" * 8001})[0] == 413
    assert call(port, "POST", "/v1/ask", {"nope": 1})[0] == 400
    assert call(port, "DELETE", "/v1/ask")[0] == 405


@pytest.mark.parametrize("auth", [None, "Bearer wrong-token", "Basic dXNlcjpwYXNz", f"Bearer {TOKEN[:-1]}y",
                                  f"bearer{TOKEN}", TOKEN])
def test_requests_without_the_token_cannot_read_or_approve(server, auth):
    """Regression (pre-release audit A1): any local process could read every memory and approve
    irreversible operations, attributed to the user. Loopback is not an access control."""
    app, port = server
    app.ask("remember that the vault hint is the red gate")
    op_id = app.ask("forget #1")["actions"][0]["operation_id"]
    events_before = len(app.store.events.recent(500))
    headers = {"Authorization": auth} if auth else {}
    status, payload, response_headers = call(port, "POST", f"/v1/operations/{op_id}/approve", {}, headers=headers,
                                             token=None, with_headers=True)
    assert status == 401 and payload == {"error": "unauthorized"}
    assert response_headers.get("WWW-Authenticate", "").startswith("Bearer")
    for method, path, body in (("GET", "/v1/memory/search?q=vault", None), ("GET", "/v1/events", None),
                               ("GET", "/v1/operations", None), ("GET", "/v1/nonexistent", None),
                               ("POST", "/v1/ask", {"message": "forget #1"}),
                               ("POST", f"/v1/operations/{op_id}/deny", {})):
        assert call(port, method, path, body, headers=headers, token=None)[0] == 401, (method, path)
    # The permission layer was never reached: nothing executed, nothing recorded, nothing leaked.
    assert app.store.operations.get(op_id)["status"] == "proposed"
    assert app.store.memory.get(1)["status"] == "active"
    assert len(app.store.events.recent(500)) == events_before
    assert call(port, "GET", "/health", token=None)[0] == 200          # liveness stays public


def test_unknown_operation_is_404_not_500(server):
    _, port = server
    assert call(port, "POST", "/v1/operations/999999/approve", {}) == (404, {"error": "operation_not_found"})
    assert call(port, "POST", "/v1/operations/999999/deny", {})[0] == 404


def test_token_file_is_private_stable_and_regenerated_when_invalid(tmp_path):
    token, path = load_or_create_token(tmp_path)
    assert len(token) >= 32 and load_or_create_token(tmp_path) == (token, path)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        path.chmod(0o644)
        load_or_create_token(tmp_path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text("short\n", encoding="utf-8")
    regenerated, _ = load_or_create_token(tmp_path)
    assert regenerated != token and len(regenerated) >= 32
    with pytest.raises(ValueError):
        make_handler(object(), token="")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_jarvis_serve_process_requires_the_token(tmp_path):
    """End to end against the real CLI server process, as an unrelated local client would see it."""
    home, port = tmp_path / "home", _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_")}
    env.update({"JARVIS_HOME": str(home), "JARVIS_WORKSPACE": str(tmp_path), "PYTHONPATH": str(ROOT / "src")})
    proc = subprocess.Popen([sys.executable, "-m", "jarvis", "serve", "--port", str(port)], env=env, cwd=tmp_path,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if call(port, "GET", "/health", token=None)[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        token = (home / "api_token").read_text(encoding="utf-8").strip()
        assert call(port, "GET", "/v1/status", token=None)[0] == 401
        assert call(port, "GET", "/v1/status", token="not-the-token")[0] == 401
        assert call(port, "GET", "/v1/status", token=token)[0] == 200
        assert call(port, "POST", "/v1/ask", {"message": "remember that x is y"}, token=None)[0] == 401
        assert call(port, "POST", "/v1/ask", {"message": "remember that x is y"}, token=token)[0] == 200
    finally:
        proc.terminate()
        out, err = proc.communicate(timeout=10)
    assert token not in out and token not in err           # the token itself is never printed
    assert str(home / "api_token") in out


def test_an_existing_api_token_is_redacted_from_conversation(app_factory, tmp_path):
    """Review: the API token pasted on its own into a conversation was stored verbatim, although
    configured secrets are documented as redacted before anything is stored."""
    from conftest import db_bytes

    token, _ = load_or_create_token(tmp_path / "home")
    app = app_factory()                                  # reads the token file at start-up
    app.ask(token)
    app.ask(f"my jarvis api token is {token} please keep it")
    assert token.encode() not in db_bytes(tmp_path / "home")


def test_jarvis_serve_binds_the_ipv6_loopback(tmp_path):
    """Review: `serve --host ::1` passed the loopback check and then crashed (IPv4-only server)."""
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::1", 0))
            port = probe.getsockname()[1]
    except OSError:
        pytest.skip("IPv6 loopback is not available here")
    home = tmp_path / "home"
    env = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_")}
    env.update({"JARVIS_HOME": str(home), "JARVIS_WORKSPACE": str(tmp_path), "PYTHONPATH": str(ROOT / "src")})
    proc = subprocess.Popen([sys.executable, "-m", "jarvis", "serve", "--host", "::1", "--port", str(port)], env=env,
                            cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        status = None
        for _ in range(100):
            try:
                conn = http.client.HTTPConnection("::1", port, timeout=5)
                conn.request("GET", "/health")
                status = conn.getresponse().status
                conn.close()
                break
            except OSError:
                time.sleep(0.1)
        assert status == 200
        token = (home / "api_token").read_text(encoding="utf-8").strip()
        conn = http.client.HTTPConnection("::1", port, timeout=5)        # sends Host: [::1]:port
        conn.request("GET", "/v1/status", headers={"Authorization": f"Bearer {token}"})
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        proc.terminate()
        proc.communicate(timeout=10)


def test_forget_approved_over_http_leaves_no_copy(server, tmp_path):
    """The HTTP approval path of `forget` is held to the same erasure guarantee as chat and CLI."""
    from conftest import db_bytes

    app, port = server
    app.ask("remember that the yorbleshed hint is kwazzlefrump")
    op = app.ask("forget #1")["actions"][0]["operation_id"]
    status, outcome = call(port, "POST", f"/v1/operations/{op}/approve", {})
    assert status == 200 and outcome["status"] == "succeeded" and outcome["verified"] is True
    assert outcome["result"]["residue"]["derived"] == 0
    assert b"kwazzlefrump" not in db_bytes(tmp_path / "home") and b"yorbleshed" not in db_bytes(tmp_path / "home")
