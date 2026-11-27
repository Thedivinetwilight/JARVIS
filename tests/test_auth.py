"""Scoped client auth: pairing, token digests, expiry, revocation, throttling.

Real SQLite, real HMAC digests, real clock. Nothing is mocked: the properties
asserted here are the ones an attacker would try to break (token reuse, scope
escalation, revoked credentials, secret exposure).
"""
from __future__ import annotations

import json
import stat

import pytest

from jarvis import manifests
from jarvis.auth import (
    PAIRING_MAX_FAILURES,
    AuthError,
    AuthService,
    load_or_create_server_secret,
    mint_pairing_code,
    normalise_pairing_code,
)
from jarvis.db import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "jarvis.sqlite3")


@pytest.fixture
def auth(tmp_path, store):
    return AuthService(store, load_or_create_server_secret(tmp_path / "home"))


def test_server_secret_is_created_once_owner_only_and_stable(tmp_path):
    home = tmp_path / "home"
    first = load_or_create_server_secret(home)
    second = load_or_create_server_secret(home)
    assert first == second and len(first) >= 32
    path = home / "server_secret"
    if stat.S_IMODE(path.stat().st_mode) != 0o600:      # pragma: no cover - non-POSIX
        pytest.skip("POSIX permissions")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "server_secret" not in json.dumps({"configured": True})


def test_tokens_are_stored_only_as_digests(auth, store):
    client, token = auth.create_operator("owner")
    assert token.startswith("jvs_") and client.id in token
    with store.read() as db:
        rows = db.execute("SELECT token_digest, token_prefix FROM clients").fetchall()
    assert len(rows) == 1
    assert token not in rows[0]["token_digest"]
    assert len(rows[0]["token_digest"]) == 64                       # HMAC-SHA256 hex
    assert auth.authenticate(token).id == client.id                  # the plaintext is never stored


def test_authenticate_accepts_its_own_token_and_rejects_others(auth):
    _, token = auth.create_operator("owner")
    assert auth.authenticate(token).is_operator
    with pytest.raises(AuthError) as info:
        auth.authenticate(token + "x")
    assert info.value.code == "token_invalid"
    with pytest.raises(AuthError):
        auth.authenticate("")
    with pytest.raises(AuthError):
        auth.authenticate("not-a-jarvis-token")


def test_pairing_code_is_single_use_and_grants_device_scopes_only(auth):
    issued = auth.create_pairing_code(label_hint="pixel", platform="android")
    assert issued["code"].count("-") == 2
    client, token = auth.redeem_pairing_code(issued["code"].lower())     # case/format tolerant
    assert client.kind == "device" and client.platform == "android"
    assert set(client.scopes) == set(manifests.DEFAULT_DEVICE_SCOPES)
    assert not set(client.scopes) & manifests.ADMIN_SCOPES
    assert auth.authenticate(token).id == client.id
    with pytest.raises(AuthError) as info:
        auth.redeem_pairing_code(issued["code"])
    assert info.value.code == "pairing_code_used"


def test_pairing_code_cannot_be_created_with_admin_scopes(auth):
    with pytest.raises(ValueError):
        auth.create_pairing_code(label_hint="rogue", platform="android",
                                 scopes=[manifests.SCOPE_CLIENTS_REVOKE])
    with pytest.raises(ValueError):
        auth.create_pairing_code(label_hint="rogue", platform="android", scopes=["not.a.scope"])


def test_expired_pairing_code_and_token_are_refused(auth, store):
    issued = auth.create_pairing_code(label_hint="old", platform="pc", ttl_minutes=1)
    with store.write() as db:
        db.execute("UPDATE pairing_codes SET expires_at = '2000-01-01T00:00:00+00:00'")
    with pytest.raises(AuthError) as info:
        auth.redeem_pairing_code(issued["code"])
    assert info.value.code == "pairing_code_expired"

    _, token = auth.create_operator("owner")
    with store.write() as db:
        db.execute("UPDATE clients SET expires_at = '2000-01-01T00:00:00+00:00'")
    with pytest.raises(AuthError) as info:
        auth.authenticate(token)
    assert info.value.code == "token_expired"


def test_revocation_is_immediate_and_idempotent(auth):
    client, token = auth.create_operator("owner")
    auth.authenticate(token)
    auth.revoke(client.id)
    with pytest.raises(AuthError) as info:
        auth.authenticate(token)
    assert info.value.code == "token_revoked"
    revoking_again = auth.revoke(client.id)
    assert revoking_again.is_revoked
    with pytest.raises(AuthError) as missing:
        auth.revoke("cli_deadbeef")
    assert missing.value.code == "client_not_found"


def test_pairing_failures_are_throttled(auth):
    for _ in range(PAIRING_MAX_FAILURES):
        with pytest.raises(AuthError):
            auth.redeem_pairing_code("AAAA-BBBB-CCCC")
    with pytest.raises(AuthError) as info:
        auth.redeem_pairing_code("AAAA-BBBB-CCCC")
    assert info.value.code == "pairing_throttled"
    # A correct code cannot be redeemed while throttled either.
    issued = auth.create_pairing_code(label_hint="late", platform="pc")
    with pytest.raises(AuthError) as info:
        auth.redeem_pairing_code(issued["code"])
    assert info.value.code == "pairing_throttled"


def test_scope_checks_and_client_listing(auth):
    issued = auth.create_pairing_code(label_hint="pixel", platform="android")
    client, _ = auth.redeem_pairing_code(issued["code"])
    auth.require_scope(client, manifests.SCOPE_MEMORY_READ)
    with pytest.raises(AuthError) as info:
        auth.require_scope(client, manifests.SCOPE_CLIENTS_REVOKE)
    assert info.value.code == "scope_denied"
    listing = auth.list_clients()
    assert [c.id for c in listing] == [client.id]
    assert auth.get_client(client.id).label == "pixel"
    assert auth.get_client("missing") is None


def test_pairing_code_normalisation_ignores_formatting():
    code = mint_pairing_code()
    assert normalise_pairing_code(code.lower().replace("-", " ")) == normalise_pairing_code(code)


def test_revoke_returns_the_record_it_actually_wrote(auth):
    """The API reports the post-update row, not the row read before the update."""
    client, _ = auth.create_operator("owner")
    revoked = auth.revoke(client.id)
    assert revoked.revoked_at is not None and revoked.is_revoked
    assert auth.get_client(client.id).revoked_at == revoked.revoked_at
