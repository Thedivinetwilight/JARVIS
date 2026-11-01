"""Append-only audit records with redaction at the boundary.

Every authenticated request, every denied request, every pairing attempt and every
revocation lands here. Detail payloads are scrubbed before they are serialised, so a
token, a bearer header or a pairing code cannot reach the audit table by accident.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from .clock import Clock, to_iso
from .db import Store
from .secrets import scrub

LOGGER = logging.getLogger("jarvis.audit")

OUTCOMES = ("allowed", "denied", "error", "info")


@dataclass(frozen=True)
class AuditEntry:
    id: int
    at: str
    request_id: str | None
    actor_kind: str
    actor_id: str | None
    action: str
    target: str | None
    outcome: str
    detail: dict | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "at": self.at,
            "request_id": self.request_id,
            "actor_kind": self.actor_kind,
            "actor_id": self.actor_id,
            "action": self.action,
            "target": self.target,
            "outcome": self.outcome,
            "detail": self.detail,
        }


class AuditLog:
    def __init__(self, store: Store, clock: Clock) -> None:
        self.store = store
        self.clock = clock

    def record(
        self,
        *,
        actor_kind: str,
        actor_id: str | None,
        action: str,
        outcome: str,
        target: str | None = None,
        detail: Any = None,
        request_id: str | None = None,
    ) -> int:
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown audit outcome: {outcome}")
        safe_detail = scrub(detail) if detail is not None else None
        payload = json.dumps(safe_detail, sort_keys=True) if safe_detail is not None else None
        try:
            with self.store.transaction() as connection:
                cursor = connection.execute(
                    "INSERT INTO audit (at, request_id, actor_kind, actor_id, action, target, "
                    "outcome, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        to_iso(self.clock.now()),
                        request_id,
                        actor_kind,
                        actor_id,
                        action[:200],
                        (target or "")[:200] or None,
                        outcome,
                        payload,
                    ),
                )
                return int(cursor.lastrowid or 0)
        except sqlite3.Error:
            # The audit table does not exist yet (readiness probe before migration) or the
            # disk refused the write. Dropping the record silently would be wrong, so the
            # scrubbed record goes to the process log and the caller is told 0 = not stored.
            LOGGER.error("audit record could not be stored: %s %s %s", action, outcome,
                         json.dumps(safe_detail, sort_keys=True) if safe_detail else "{}")
            return 0

    def tail(self, limit: int = 50, *, after_id: int | None = None) -> list[AuditEntry]:
        limit = max(1, min(int(limit), 500))
        query = "SELECT * FROM audit"
        params: list[Any] = []
        if after_id is not None:
            query += " WHERE id > ?"
            params.append(int(after_id))
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.store.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        entries = []
        for row in rows:
            detail = json.loads(row["detail"]) if row["detail"] else None
            entries.append(
                AuditEntry(
                    id=row["id"],
                    at=row["at"],
                    request_id=row["request_id"],
                    actor_kind=row["actor_kind"],
                    actor_id=row["actor_id"],
                    action=row["action"],
                    target=row["target"],
                    outcome=row["outcome"],
                    detail=detail,
                )
            )
        return entries

    def count(self) -> int:
        with self.store.connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM audit").fetchone()
        return int(row["n"])
