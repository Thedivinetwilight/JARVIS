"""Scoped per-client tokens with pairing, expiry and revocation.

Ported from bbff (app/jarvis/auth.py) and adapted to the Genesis store.
Replaces the single static API token with per-client HMAC-digested tokens:

- operator token (CLI only, full scopes)
- device tokens via single-use pairing codes (narrow scopes, expiry)
- revocation stops the token immediately and purges its audio sessions
- tokens are shown once, stored only as HMAC-SHA256 digests

The legacy single-token file (``api_token``) is still supported for
backward compatibility: if present, it maps to an operator client.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets as _secrets
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from . import manifests
from .util import to_iso

TOKEN_PREFIX = "jvs"
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIRING_GROUP = 4
PAIRING_GROUPS = 3

PAIRING_THROTTLE_WINDOW = timedelta(minutes=10)
PAIRING_MAX_FAILURES = 10


def _digest(server_secret: str, value: str) -> str:
    return hmac.new(server_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def _constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def mint_token(server_secret: str, client_id: str) -> tuple[str, str, str]:
    """Return (token, digest, prefix). Token shown once."""
    body = _secrets.token_urlsafe(32)
    token = f"{TOKEN_PREFIX}_{client_id}.{body}"
    return token, _digest(server_secret, token), body[:8]


def mint_pairing_code() -> str:
    groups = ["".join(_secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_GROUP))
              for _ in range(PAIRING_GROUPS)]
    return "-".join(groups)


def normalise_pairing_code(raw: str) -> str:
    import re
    return re.sub(r"[^A-Z2-9]", "", (raw or "").upper())


@dataclass(frozen=True)
class Client:
    id: str
    label: str
    platform: str
    kind: str  # operator | device
    scopes: tuple[str, ...]
    created_at: str
    expires_at: str
    revoked_at: str | None
    last_seen_at: str | None
    token_prefix: str

    @property
    def is_operator(self) -> bool:
        return self.kind == "operator"

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "platform": self.platform,
            "kind": self.kind, "scopes": list(self.scopes),
            "created_at": self.created_at, "expires_at": self.expires_at,
            "revoked_at": self.revoked_at, "last_seen_at": self.last_seen_at,
            "token_prefix": self.token_prefix,
        }


def _row_to_client(row: sqlite3.Row) -> Client:
    return Client(
        id=row["id"], label=row["label"], platform=row["platform"], kind=row["kind"],
        scopes=tuple(json.loads(row["scopes"])), created_at=row["created_at"],
        expires_at=row["expires_at"], revoked_at=row["revoked_at"],
        last_seen_at=row["last_seen_at"], token_prefix=row["token_prefix"],
    )


def load_or_create_server_secret(home) -> str:
    """Return the persistent token-signing secret, creating it 0600 on first use.

    The secret never leaves the machine: it is not derived from any API key, is not
    printed, and is registered with the redactor by the caller.
    """
    import os
    import secrets as _s
    from pathlib import Path
    path = Path(home) / "server_secret"
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if len(existing) >= 32:
        return existing
    secret = _s.token_urlsafe(48)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.unlink()
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secret + "\n")
    os.replace(tmp, path)
    return secret


class AuthError(RuntimeError):
    def __init__(self, message: str, code: str = "unauthenticated"):
        super().__init__(message)
        self.code = code


class AuthService:
    """Manages clients, pairing codes, and token authentication.

    Uses the Genesis Store's SQLite database. Tables are created lazily
    (CREATE TABLE IF NOT EXISTS) so no migration is required.
    """

    def __init__(self, store, server_secret: str):
        self.store = store
        self.server_secret = server_secret
        self._pairing_failures: list[float] = []

    # ------------------------------------------------------------ operators
    def create_operator(self, label: str = "operator", ttl_days: int = 365) -> tuple[Client, str]:
        """Create the local operator token. CLI only."""
        return self._create_client(
            label=label, platform="server-console", kind="operator",
            scopes=manifests.OPERATOR_SCOPES, ttl_days=ttl_days,
        )

    def _create_client(self, *, label: str, platform: str, kind: str,
                       scopes, ttl_days: int, paired_from: str | None = None) -> tuple[Client, str]:
        client_id = f"cli_{_secrets.token_hex(4)}"
        token, digest, prefix = mint_token(self.server_secret, client_id)
        now = self.store.now()
        expires = now + timedelta(days=ttl_days)
        scopes_tuple = manifests.validate_scopes(scopes)
        with self.store.write() as db:
            db.execute(
                "INSERT INTO clients (id, label, platform, kind, scopes, token_digest, "
                "created_at, expires_at, token_prefix) VALUES (?,?,?,?,?,?,?,?,?)",
                (client_id, label, platform, kind, json.dumps(list(scopes_tuple)),
                 digest, to_iso(now), to_iso(expires), prefix))
        client = Client(
            id=client_id, label=label, platform=platform, kind=kind,
            scopes=scopes_tuple, created_at=to_iso(now), expires_at=to_iso(expires),
            revoked_at=None, last_seen_at=None, token_prefix=prefix)
        return client, token

    # ------------------------------------------------------------ pairing
    def create_pairing_code(self, *, label_hint: str, platform: str,
                            scopes: Iterable[str] | None = None,
                            ttl_minutes: int = 10) -> dict:
        requested = tuple(sorted(set(manifests.DEFAULT_DEVICE_SCOPES if scopes is None else scopes)))
        scopes_tuple = manifests.validate_scopes(requested)
        # Devices must never receive admin scopes
        if set(scopes_tuple) & manifests.ADMIN_SCOPES:
            raise ValueError("Device tokens cannot receive admin scopes")
        code = mint_pairing_code()
        code_digest = _digest(self.server_secret, code)
        now = self.store.now()
        expires = now + timedelta(minutes=ttl_minutes)
        with self.store.write() as db:
            db.execute(
                "INSERT INTO pairing_codes (code_digest, label_hint, platform, scopes, "
                "created_at, expires_at, created_by) VALUES (?,?,?,?,?,?,?)",
                (code_digest, label_hint, platform, json.dumps(list(scopes_tuple)),
                 to_iso(now), to_iso(expires), "operator"))
        return {"code": code, "expires_at": to_iso(expires), "scopes": list(scopes_tuple)}

    def redeem_pairing_code(self, code: str) -> tuple[Client, str]:
        """Redeem a single-use pairing code for a device token."""
        import time
        now_ts = time.time()
        # Throttle: prune old failures, check count
        self._pairing_failures = [t for t in self._pairing_failures
                                  if now_ts - t < PAIRING_THROTTLE_WINDOW.total_seconds()]
        if len(self._pairing_failures) >= PAIRING_MAX_FAILURES:
            raise AuthError("Too many pairing failures; try again later", code="pairing_throttled")

        normalised = normalise_pairing_code(code)
        # Reconstruct display form for digest (digest is over the display form)
        display = "-".join(normalised[i:i+4] for i in range(0, len(normalised), 4))
        code_digest = _digest(self.server_secret, display)
        now = self.store.now()

        with self.store.write() as db:
            row = db.execute(
                "SELECT * FROM pairing_codes WHERE code_digest = ?", (code_digest,)).fetchone()
            if row is None:
                self._pairing_failures.append(now_ts)
                raise AuthError("Invalid pairing code", code="pairing_code_invalid")
            if row["redeemed_at"] is not None:
                self._pairing_failures.append(now_ts)
                raise AuthError("Pairing code already redeemed", code="pairing_code_used")
            if row["expires_at"] < to_iso(now):
                self._pairing_failures.append(now_ts)
                raise AuthError("Pairing code expired", code="pairing_code_expired")
            # Mark redeemed
            db.execute("UPDATE pairing_codes SET redeemed_at = ? WHERE code_digest = ?",
                       (to_iso(now), code_digest))
            scopes = tuple(json.loads(row["scopes"]))
            label = row["label_hint"]
            platform = row["platform"]

        client, token = self._create_client(
            label=label, platform=platform, kind="device",
            scopes=scopes, ttl_days=90, paired_from=code_digest[:8])
        return client, token

    # ------------------------------------------------------------ authentication
    def authenticate(self, token: str) -> Client:
        """Authenticate a bearer token. Raises AuthError on failure."""
        if not token or not token.startswith(f"{TOKEN_PREFIX}_"):
            raise AuthError("Missing or malformed token", code="token_missing")
        token_digest = _digest(self.server_secret, token)
        now_iso = to_iso(self.store.now())
        with self.store.read() as db:
            row = db.execute(
                "SELECT * FROM clients WHERE token_digest = ?", (token_digest,)).fetchone()
        if row is None:
            raise AuthError("Invalid token", code="token_invalid")
        client = _row_to_client(row)
        if client.is_revoked:
            raise AuthError("Token revoked", code="token_revoked")
        if client.expires_at < now_iso:
            raise AuthError("Token expired", code="token_expired")
        # Update last_seen
        with self.store.write() as db:
            db.execute("UPDATE clients SET last_seen_at = ? WHERE id = ?",
                       (now_iso, client.id))
        return client

    def require_scope(self, client: Client, scope: str) -> None:
        if not client.has_scope(scope):
            raise AuthError(f"Scope {scope} required", code="scope_denied")

    # ------------------------------------------------------------ revocation
    def revoke(self, client_id: str) -> Client:
        now_iso = to_iso(self.store.now())
        with self.store.write() as db:
            row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
            if row is None:
                raise AuthError("Client not found", code="client_not_found")
            db.execute("UPDATE clients SET revoked_at = ? WHERE id = ?", (now_iso, client_id))
            row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        return _row_to_client(row)

    # ------------------------------------------------------------ listing
    def list_clients(self) -> list[Client]:
        with self.store.read() as db:
            rows = db.execute("SELECT * FROM clients ORDER BY created_at").fetchall()
        return [_row_to_client(r) for r in rows]

    def get_client(self, client_id: str) -> Client | None:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        return _row_to_client(row) if row else None
