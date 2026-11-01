"""Pairing, token authentication, scope enforcement and revocation.

Threat model in one paragraph: the owner runs the server; devices get their own token with
an explicit scope list and an expiry; tokens are shown once and stored as HMAC digests;
pairing needs a single-use code that only the operator can mint on the server itself; a
revoked or expired token is refused with a distinct error code and an audit record; and no
token grants a capability whose manifest is not available.
"""

from __future__ import annotations

import json
import secrets as _secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from . import manifests
from .audit import AuditLog
from .clock import Clock, parse_iso, to_iso
from .config import Config
from .db import Store
from .errors import (
    BadRequest,
    Conflict,
    NotFound,
    ScopeDenied,
    TokenExpired,
    TokenRevoked,
    Unauthenticated,
)
from .secrets import constant_time_equals, digest, mint_pairing_code, mint_token, normalise_pairing_code

PAIRING_THROTTLE_WINDOW = timedelta(minutes=10)


@dataclass(frozen=True)
class Client:
    id: str
    label: str
    platform: str
    kind: str
    scopes: tuple[str, ...]
    created_at: str
    expires_at: str
    revoked_at: str | None
    last_seen_at: str | None
    token_prefix: str

    @property
    def is_operator(self) -> bool:
        return self.kind == "operator"

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "platform": self.platform,
            "kind": self.kind,
            "scopes": list(self.scopes),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "last_seen_at": self.last_seen_at,
            "token_prefix": self.token_prefix,
        }


def _row_to_client(row) -> Client:  # noqa: ANN001 - sqlite3.Row
    return Client(
        id=row["id"],
        label=row["label"],
        platform=row["platform"],
        kind=row["kind"],
        scopes=tuple(json.loads(row["scopes"])),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        last_seen_at=row["last_seen_at"],
        token_prefix=row["token_prefix"],
    )


class AuthService:
    def __init__(self, store: Store, config: Config, clock: Clock, audit: AuditLog) -> None:
        self.store = store
        self.config = config
        self.clock = clock
        self.audit = audit
        self._pairing_failures: list[float] = []

    # ------------------------------------------------------------------ operators
    def create_operator(self, label: str = "operator") -> tuple[Client, str]:
        """Create the local operator token. CLI only: there is no HTTP route for this."""
        return self._create_client(
            label=label,
            platform="server-console",
            kind="operator",
            scopes=manifests.OPERATOR_SCOPES,
            ttl_days=self.config.auth.operator_token_ttl_days,
            paired_from=None,
        )

    # ------------------------------------------------------------------ pairing
    def create_pairing_code(
        self,
        *,
        label_hint: str,
        platform: str,
        scopes: Iterable[str] | None = None,
        ttl_minutes: int | None = None,
        created_by: str = "operator",
    ) -> dict:
        requested = tuple(sorted(set(manifests.DEFAULT_DEVICE_SCOPES if scopes is None else scopes)))
        try:
            scopes_tuple = manifests.validate_scopes(requested)
        except ValueError as exc:
            raise BadRequest(str(exc), code="unknown_scope") from exc
        admin = set(scopes_tuple) & set(manifests.ADMIN_SCOPES)
        if admin:
            raise BadRequest(
                f"a device pairing code may not carry administrative scopes: {sorted(admin)}",
                code="scope_not_grantable",
            )
        if not scopes_tuple:
            raise BadRequest("a pairing code needs at least one scope", code="scope_required")
        if not label_hint.strip():
            raise BadRequest("label_hint is required")
        if not platform.strip():
            raise BadRequest("platform is required")

        ttl = int(ttl_minutes or self.config.auth.pairing_code_ttl_minutes)
        if not 1 <= ttl <= 1440:
            raise BadRequest("ttl_minutes must be between 1 and 1440")
        code = mint_pairing_code()
        code_id = f"prc_{_secrets.token_hex(5)}"
        now = self.clock.now()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO pairing_codes (id, code_hash, label_hint, platform, scopes, "
                "created_at, expires_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    code_id,
                    digest(self.config.server_secret, normalise_pairing_code(code)),
                    label_hint.strip()[:120],
                    platform.strip()[:60],
                    json.dumps(list(scopes_tuple)),
                    to_iso(now),
                    to_iso(now + timedelta(minutes=ttl)),
                    created_by,
                ),
            )
        self.audit.record(
            actor_kind="operator",
            actor_id=created_by,
            action="pairing.code_created",
            target=code_id,
            outcome="allowed",
            detail={"platform": platform, "scopes": list(scopes_tuple), "ttl_minutes": ttl},
        )
        return {
            "pairing_code": code,
            "pairing_code_id": code_id,
            "expires_at": to_iso(now + timedelta(minutes=ttl)),
            "scopes": list(scopes_tuple),
            "note": "shown once; the code is stored only as a digest and can be used once",
        }

    def pair(
        self,
        *,
        code: str,
        label: str,
        platform: str,
        request_id: str | None = None,
    ) -> tuple[Client, str]:
        normalised = normalise_pairing_code(code or "")
        if len(normalised) < 8:
            self._deny_pairing("submitted code was too short to be a pairing code", request_id)
        self._check_pairing_throttle(request_id)

        now = self.clock.now()
        with self.store.connection() as connection:
            candidates = connection.execute(
                "SELECT * FROM pairing_codes WHERE used_at IS NULL AND expires_at > ?",
                (to_iso(now),),
            ).fetchall()

        submitted = digest(self.config.server_secret, normalised)
        match = None
        for row in candidates:
            if constant_time_equals(row["code_hash"], submitted):
                match = row
                break

        if match is None:
            self._note_pairing_failure()
            self._deny_pairing("code did not match an active pairing code", request_id)

        scopes = tuple(json.loads(match["scopes"]))
        if not label.strip():
            label = match["label_hint"]
        client, token = self._create_client(
            label=label.strip()[:120],
            platform=(platform or match["platform"]).strip()[:60],
            kind="device",
            scopes=scopes,
            ttl_days=self.config.auth.device_token_ttl_days,
            paired_from=match["id"],
        )
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE pairing_codes SET used_at = ? WHERE id = ? AND used_at IS NULL",
                (to_iso(now), match["id"]),
            )
        # Explicit per-manifest grants with their own expiry, so the scope grant is auditable
        # and revocable independently of the token row.
        self._write_grants(client_id=client.id, scopes=scopes, expires_at=client.expires_at)
        self.audit.record(
            actor_kind="client",
            actor_id=client.id,
            action="pairing.completed",
            target=match["id"],
            outcome="allowed",
            detail={"platform": client.platform, "scopes": list(client.scopes)},
            request_id=request_id,
        )
        self._pairing_failures.clear()
        return client, token

    def _check_pairing_throttle(self, request_id: str | None = None) -> None:
        window_start = self.clock.now().timestamp() - PAIRING_THROTTLE_WINDOW.total_seconds()
        self._pairing_failures = [stamp for stamp in self._pairing_failures if stamp > window_start]
        if len(self._pairing_failures) >= self.config.auth.pairing_max_failed_attempts:
            self.audit.record(
                actor_kind="anonymous",
                actor_id=None,
                action="pairing.throttled",
                target=None,
                outcome="denied",
                detail={"reason": "too many failed attempts in the throttle window",
                        "window_seconds": int(PAIRING_THROTTLE_WINDOW.total_seconds())},
                request_id=request_id,
            )
            raise Unauthenticated(
                "too many failed pairing attempts; wait before trying again",
                code="pairing_throttled",
            )

    def _deny_pairing(self, reason: str, request_id: str | None = None):
        """Audit one refused pairing attempt, then refuse it. Never returns."""
        self.audit.record(
            actor_kind="anonymous",
            actor_id=None,
            action="pairing.attempt",
            target=None,
            outcome="denied",
            detail={"reason": reason},
            request_id=request_id,
        )
        raise Unauthenticated("pairing code is invalid or expired", code="pairing_code_invalid")

    def _note_pairing_failure(self) -> None:
        self._pairing_failures.append(self.clock.now().timestamp())

    def _write_grants(self, *, client_id: str, scopes: tuple[str, ...], expires_at: str) -> None:
        with self.store.transaction() as connection:
            for manifest in manifests.MANIFESTS:
                shared = sorted(set(manifest.scopes) & set(scopes))
                if not shared:
                    continue
                connection.execute(
                    "INSERT INTO client_grants (client_id, manifest_id, scopes, granted_at, "
                    "expires_at) VALUES (?, ?, ?, ?, ?)",
                    (client_id, manifest.id, json.dumps(shared), to_iso(self.clock.now()), expires_at),
                )

    def _create_client(
        self,
        *,
        label: str,
        platform: str,
        kind: str,
        scopes: Iterable[str],
        ttl_days: int,
        paired_from: str | None,
    ) -> tuple[Client, str]:
        client_id = f"cli_{_secrets.token_hex(6)}"
        token, token_hash, token_prefix = mint_token(self.config.server_secret, client_id)
        now = self.clock.now()
        expires_at = to_iso(now + timedelta(days=ttl_days))
        scopes_tuple = manifests.validate_scopes(scopes)
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO clients (id, label, platform, kind, scopes, token_hash, "
                "token_prefix, created_at, expires_at, paired_from) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    client_id,
                    label,
                    platform,
                    kind,
                    json.dumps(list(scopes_tuple)),
                    token_hash,
                    token_prefix,
                    to_iso(now),
                    expires_at,
                    paired_from,
                ),
            )
        client = Client(
            id=client_id,
            label=label,
            platform=platform,
            kind=kind,
            scopes=scopes_tuple,
            created_at=to_iso(now),
            expires_at=expires_at,
            revoked_at=None,
            last_seen_at=None,
            token_prefix=token_prefix,
        )
        return client, token

    # ------------------------------------------------------------------ authentication
    def authenticate(self, token: str | None, *, request_id: str | None = None) -> Client:
        if not token:
            raise Unauthenticated("a bearer token is required", code="token_missing")
        token = token.strip()
        if not token.startswith("jvs_"):
            raise Unauthenticated("token is not recognised", code="token_invalid")
        hashed = digest(self.config.server_secret, token)
        with self.store.connection() as connection:
            row = connection.execute("SELECT * FROM clients WHERE token_hash = ?", (hashed,)).fetchone()
        if row is None:
            self.audit.record(
                actor_kind="anonymous", actor_id=None, action="auth.attempt", target=None,
                outcome="denied", detail={"reason": "unknown token"}, request_id=request_id,
            )
            raise Unauthenticated("token is not recognised", code="token_invalid")
        client = _row_to_client(row)
        if client.revoked_at:
            raise TokenRevoked("this client has been revoked", code="token_revoked")
        if parse_iso(client.expires_at) <= self.clock.now():
            raise TokenExpired("this token has expired; pair the device again", code="token_expired")
        self._touch(client)
        return client

    def _touch(self, client: Client) -> None:
        now = self.clock.now()
        if client.last_seen_at:
            try:
                last = parse_iso(client.last_seen_at)
            except ValueError:  # pragma: no cover - defensive
                last = None
            if last is not None and (now - last) < timedelta(seconds=60):
                return
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE clients SET last_seen_at = ? WHERE id = ?", (to_iso(now), client.id)
            )

    # ------------------------------------------------------------------ authorisation
    @staticmethod
    def require_scope(client: Client, scope: str) -> None:
        if not client.has_scope(scope):
            raise ScopeDenied(
                f"this token does not carry the {scope} scope", code="scope_denied",
                detail={"required_scope": scope},
            )

    # ------------------------------------------------------------------ revocation
    def revoke(
        self,
        client_id: str,
        *,
        reason: str,
        actor_kind: str,
        actor_id: str | None,
        request_id: str | None = None,
    ) -> Client:
        with self.store.connection() as connection:
            row = connection.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if row is None:
            raise NotFound("no such client", code="client_not_found")
        client = _row_to_client(row)
        if client.revoked_at:
            return client
        now = to_iso(self.clock.now())
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE clients SET revoked_at = ?, revoked_reason = ? WHERE id = ? AND revoked_at IS NULL",
                (now, reason[:200], client_id),
            )
            connection.execute(
                "UPDATE client_grants SET revoked_at = ? WHERE client_id = ? AND revoked_at IS NULL",
                (now, client_id),
            )
        self.audit.record(
            actor_kind=actor_kind,
            actor_id=actor_id,
            action="client.revoked",
            target=client_id,
            outcome="allowed",
            detail={"reason": reason},
            request_id=request_id,
        )
        with self.store.connection() as connection:
            row = connection.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        return _row_to_client(row)

    # ------------------------------------------------------------------ listing
    def list_clients(self) -> list[Client]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM clients ORDER BY created_at, id"
            ).fetchall()
        return [_row_to_client(row) for row in rows]

    def get_client(self, client_id: str) -> Client:
        with self.store.connection() as connection:
            row = connection.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if row is None:
            raise NotFound("no such client", code="client_not_found")
        return _row_to_client(row)

    def grants_for(self, client_id: str) -> list[dict]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM client_grants WHERE client_id = ? ORDER BY id", (client_id,)
            ).fetchall()
        return [
            {
                "manifest_id": row["manifest_id"],
                "scopes": json.loads(row["scopes"]),
                "granted_at": row["granted_at"],
                "expires_at": row["expires_at"],
                "revoked_at": row["revoked_at"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ helpers
    def client_count(self) -> int:
        with self.store.connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM clients").fetchone()
        return int(row["n"])

    def expired_or_revoked(self) -> list[str]:
        now = self.clock.now()
        stale: list[str] = []
        for client in self.list_clients():
            if client.revoked_at or parse_iso(client.expires_at) <= now:
                stale.append(client.id)
        return stale

    def pairing_code_count(self, *, unused_only: bool = False) -> int:
        query = "SELECT COUNT(*) AS n FROM pairing_codes"
        params: tuple = ()
        if unused_only:
            query += " WHERE used_at IS NULL"
        with self.store.connection() as connection:
            row = connection.execute(query, params).fetchone()
        return int(row["n"])


def ensure_unique_client_ids(clients: list[Client]) -> None:  # pragma: no cover - assertion helper
    ids = [client.id for client in clients]
    if len(set(ids)) != len(ids):
        raise Conflict("duplicate client identifiers")
