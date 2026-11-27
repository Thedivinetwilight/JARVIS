"""Persistent leases: at most one runner drives an objective at a time.

Mission 003. A lease is a row in ``objective_leases`` keyed by the objective. Acquisition is a
single atomic upsert that only succeeds when the existing lease has expired, so two processes
racing for the same objective cannot both win. Every win increments a **fencing token**; a
holder must present both its owner string and that token to renew or release, and a token is
never valid again after expiry (renew requires ``expires_at > now``, so an expired holder cannot
resurrect its claim).

Semantics, stated plainly:

* **Objective-level state is single-writer.** The service re-checks the lease before writing the
  objective's status, a replan, or a terminal outcome; a stale holder stops instead of
  overwriting the new holder's work.
* **Step execution is at-least-once.** A step that was already inside a tool when the lease
  expired may finish and be journaled. The operation journal's idempotency keys are what make a
  repeated non-idempotent call safe; the lease narrows the window, it does not replace them.
* **Time is stored as ISO-8601 UTC text**, so expiry comparisons are string comparisons. All
  timestamps in this codebase are written through ``util.to_iso``.

Failure assumptions: the database is the only coordination point (no shared memory), clocks are
roughly synchronised (a badly skewed clock only shortens or lengthens a lease, it cannot make two
holders write objective state at once, because every write is preceded by a conditional read).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .util import to_iso

DEFAULT_TTL_SECONDS = 60.0
MIN_TTL_SECONDS = 5.0
MAX_TTL_SECONDS = 900.0


class LeaseError(RuntimeError):
    """Raised when a lease operation is asked for something invalid."""


class LeaseLost(LeaseError):
    """Raised when a holder discovers it no longer owns the lease."""


@dataclass(frozen=True)
class Lease:
    resource: str
    owner: str
    token: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"resource": self.resource, "owner": self.owner, "token": self.token,
                "acquired_at": self.acquired_at, "heartbeat_at": self.heartbeat_at,
                "expires_at": self.expires_at}


def _bounded_ttl(ttl: float) -> float:
    value = float(ttl)
    if not MIN_TTL_SECONDS <= value <= MAX_TTL_SECONDS:
        raise LeaseError(f"Lease ttl must be within {MIN_TTL_SECONDS:g}-{MAX_TTL_SECONDS:g} seconds")
    return value


class LeaseStore:
    """Atomic acquire / renew / release over ``objective_leases``."""

    def __init__(self, store) -> None:
        self.store = store

    # ------------------------------------------------------------------ writing
    def acquire(self, resource: str, owner: str | None = None, *,
                ttl: float = DEFAULT_TTL_SECONDS) -> Lease | None:
        """Claim the objective, or return the existing lease if this owner already holds it.

        Returns ``None`` when another live owner holds it.
        """
        if not resource or not isinstance(resource, str):
            raise LeaseError("resource is required")
        owner = owner or f"worker-{secrets.token_hex(6)}"
        ttl = _bounded_ttl(ttl)
        now = self.store.now()
        now_iso = to_iso(now)
        expires = to_iso(now + timedelta(seconds=ttl))
        with self.store.write() as db:
            db.execute(
                "INSERT INTO objective_leases(resource, owner, token, acquired_at, "
                "heartbeat_at, expires_at) VALUES (?,?,1,?,?,?) "
                "ON CONFLICT(resource) DO UPDATE SET owner = excluded.owner, "
                "token = objective_leases.token + 1, acquired_at = excluded.acquired_at, "
                "heartbeat_at = excluded.heartbeat_at, expires_at = excluded.expires_at "
                "WHERE objective_leases.expires_at <= excluded.acquired_at",
                (resource, owner, now_iso, now_iso, expires))
            row = db.execute("SELECT * FROM objective_leases WHERE resource = ?",
                             (resource,)).fetchone()
        if row is None or row["owner"] != owner:
            return None
        return self._lease(row)

    def renew(self, lease: Lease, *, ttl: float = DEFAULT_TTL_SECONDS) -> bool:
        """Extend the lease. False means it was lost (expired or taken over) — stop working."""
        ttl = _bounded_ttl(ttl)
        now = self.store.now()
        now_iso = to_iso(now)
        expires = to_iso(now + timedelta(seconds=ttl))
        with self.store.write() as db:
            updated = db.execute(
                "UPDATE objective_leases SET heartbeat_at = ?, expires_at = ? "
                "WHERE resource = ? AND owner = ? AND token = ? AND expires_at > ?",
                (now_iso, expires, lease.resource, lease.owner, lease.token, now_iso)).rowcount
        return updated == 1

    def release(self, lease: Lease) -> bool:
        with self.store.write() as db:
            deleted = db.execute(
                "DELETE FROM objective_leases WHERE resource = ? AND owner = ? AND token = ?",
                (lease.resource, lease.owner, lease.token)).rowcount
        return deleted == 1

    # ------------------------------------------------------------------ reading
    def held_by(self, resource: str) -> Lease | None:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM objective_leases WHERE resource = ?",
                             (resource,)).fetchone()
        return None if row is None else self._lease(row)

    def is_held_by(self, lease: Lease) -> bool:
        """Read-only check that this exact fencing token is still live."""
        now_iso = to_iso(self.store.now())
        with self.store.read() as db:
            row = db.execute(
                "SELECT 1 FROM objective_leases WHERE resource = ? AND owner = ? AND token = ? "
                "AND expires_at > ?", (lease.resource, lease.owner, lease.token, now_iso)
            ).fetchone()
        return row is not None

    def purge_expired(self) -> int:
        now_iso = to_iso(self.store.now())
        with self.store.write() as db:
            removed = db.execute("DELETE FROM objective_leases WHERE expires_at <= ?",
                                 (now_iso,)).rowcount
        return int(removed)

    @staticmethod
    def _lease(row) -> Lease:
        return Lease(resource=row["resource"], owner=row["owner"], token=row["token"],
                     acquired_at=row["acquired_at"], heartbeat_at=row["heartbeat_at"],
                     expires_at=row["expires_at"])
