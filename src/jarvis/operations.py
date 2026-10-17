"""Operation journal: write-ahead records for every capability invocation.

Lifecycle::

    proposed --approve--> started --> succeeded | failed | timed_out
        \\--deny/cancel--> denied | cancelled
    started --(process died)--> interrupted       (set by recovery, never by guesswork)

Guarantees
- The ``started`` row is committed *before* the handler runs, so a crash leaves
  evidence that execution may have begun. Recovery marks such rows
  ``interrupted`` (outcome unknown); it never assumes success and never re-runs them.
- Idempotency keys (adapted from Stripe's semantics): reusing a key with the same
  arguments returns the recorded outcome instead of executing again; reusing it with
  different arguments is rejected.
- Arguments needed to execute an approved proposal are stored until the operation
  finishes; afterwards only a redacted copy (sensitive fields replaced, long text
  truncated) is kept, so the journal does not become a second store of private content.
"""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .util import bounded, dumps, loads, parse_iso, to_iso

if TYPE_CHECKING:  # pragma: no cover
    from .db import Store

TERMINAL = {"denied", "succeeded", "failed", "timed_out", "interrupted", "cancelled"}
RECOVERY_GRACE_S = 30.0
_CONTENT_KEYS = {"value", "message", "text", "reason", "note", "content", "body"}


def _generic_redaction(arguments: dict[str, Any]) -> dict[str, Any]:
    """Redaction without registry knowledge (used for rows orphaned by a crash)."""
    from .redact import redact_text
    return {k: "[REDACTED]" if k.lower() in _CONTENT_KEYS else (redact_text(v)[:200] if isinstance(v, str) else v)
            for k, v in arguments.items()}


class OperationJournal:
    def __init__(self, store: "Store"):
        self.store = store

    def create(self, *, capability: str, arguments: dict[str, Any], redacted_arguments: dict[str, Any],
               args_digest: str, origin: str, risk: str, status: str, reason: str, timeout_s: float,
               correlation_id: str | None, session_id: str | None, idempotency_key: str | None,
               runtime_instance: str | None) -> int:
        stored_args = arguments if status in {"proposed", "started"} else redacted_arguments
        with self.store.write() as db:
            op_id = int(db.execute(
                "INSERT INTO operations(correlation_id, session_id, capability, arguments_json, args_digest, "
                "idempotency_key, origin, risk, status, decision_reason, requested_at, started_at, timeout_s, "
                "runtime_instance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (correlation_id, session_id, capability, dumps(stored_args), args_digest, idempotency_key, origin,
                 risk, status, bounded(reason, 300), to_iso(self.store.now()),
                 to_iso(self.store.now()) if status == "started" else None, timeout_s,
                 runtime_instance)).lastrowid)
            self.store.events.append(f"operation.{status}", actor=origin, target=capability, operation_id=op_id,
                                     session_id=session_id, cycle_id=correlation_id, decision=status,
                                     payload={"risk": risk, "reason": bounded(reason, 300),
                                              "arguments": redacted_arguments}, conn=db)
        return op_id

    def start(self, op_id: int, *, approved_by: str | None = None, runtime_instance: str | None = None) -> None:
        """Move a proposed operation to started (write-ahead, committed before execution)."""
        with self.store.write() as db:
            now = to_iso(self.store.now())
            cur = db.execute(
                "UPDATE operations SET status='started', started_at=?, approved_at=coalesce(?, approved_at), "
                "approved_by=coalesce(?, approved_by), runtime_instance=coalesce(?, runtime_instance) "
                "WHERE id=? AND status='proposed'",
                (now, now if approved_by else None, approved_by, runtime_instance, op_id))
            if cur.rowcount != 1:
                raise ValueError(f"Operation {op_id} is not awaiting approval")
            row = db.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
            self.store.events.append("operation.started", actor=approved_by or row["origin"], target=row["capability"],
                                     operation_id=op_id, session_id=row["session_id"], cycle_id=row["correlation_id"],
                                     decision="started", payload={"approved_by": approved_by}, conn=db)

    def finish(self, op_id: int, *, status: str, result: Any = None, error_type: str | None = None,
               error_message: str | None = None, verified: bool | None = None, verification_note: str = "",
               redacted_arguments: dict[str, Any] | None = None, expect_status: str | None = None) -> bool:
        """Record a terminal status. With ``expect_status`` the transition happens only if the
        row is still in that status, checked inside the same write transaction; otherwise
        nothing is written and ``False`` is returned. Callers that decided on a transition
        from an earlier read (deny, approval pre-checks) must pass it: without it a stale
        caller could overwrite the outcome of a concurrent approval (e.g. mark an operation
        that has already executed as ``cancelled``)."""
        if status not in TERMINAL:
            raise ValueError(f"Not a terminal status: {status}")
        with self.store.write() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown operation {op_id}")
            if expect_status is not None and row["status"] != expect_status:
                return False
            db.execute(
                "UPDATE operations SET status=?, finished_at=?, result_json=?, error_type=?, error_message=?, "
                "verified=?, verification_note=?, arguments_json=coalesce(?, arguments_json) WHERE id=?",
                (status, to_iso(self.store.now()), bounded(dumps(result), 20000) if result is not None else None,
                 error_type, bounded(error_message, 500) if error_message else None,
                 None if verified is None else int(verified), bounded(verification_note, 300),
                 dumps(redacted_arguments) if redacted_arguments is not None else None, op_id))
            self.store.events.append(f"operation.{status}", actor="tool", target=row["capability"], operation_id=op_id,
                                     session_id=row["session_id"], cycle_id=row["correlation_id"], decision=status,
                                     payload={"error_type": error_type, "verified": verified,
                                              "verification_note": bounded(verification_note, 300)}, conn=db)
            if verified:
                self.store.events.append("operation.verified", actor="jarvis", target=row["capability"],
                                         operation_id=op_id, session_id=row["session_id"],
                                         cycle_id=row["correlation_id"], decision="verified",
                                         payload={"note": bounded(verification_note, 300)}, conn=db)
        return True

    def get(self, op_id: int) -> dict[str, Any] | None:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
        return self._decode(row) if row else None

    @staticmethod
    def _decode(row) -> dict[str, Any]:
        item = dict(row)
        item["arguments"] = loads(item.pop("arguments_json", "{}"), {})
        item["result"] = loads(item.pop("result_json", None))
        return item

    def by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM operations WHERE idempotency_key=?", (key,)).fetchone()
        return self._decode(row) if row else None

    def list(self, *, status: str | None = None, limit: int = 50, correlation_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM operations", []
        clauses = []
        if status:
            clauses.append("status=?")
            args.append(status)
        if correlation_id:
            clauses.append("correlation_id=?")
            args.append(correlation_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        with self.store.read() as db:
            return [self._decode(r) for r in db.execute(sql, (*args, max(1, min(limit, 500))))]

    def recover_interrupted(self, *, current_runtime: str | None) -> list[dict[str, Any]]:
        """Mark operations that were left ``started`` beyond their deadline as ``interrupted``.

        An operation is considered abandoned when it has been ``started`` for longer
        than its own timeout plus a grace period and it does not belong to the current
        runtime. Platform-independent: no process probing is needed because every
        operation has a hard deadline enforced by the registry.
        """
        now = self.store.now()
        recovered: list[dict[str, Any]] = []
        with self.store.write() as db:
            rows = db.execute("SELECT * FROM operations WHERE status='started'").fetchall()
            for row in rows:
                if current_runtime and row["runtime_instance"] == current_runtime:
                    continue
                started = parse_iso(row["started_at"]) or parse_iso(row["requested_at"])
                deadline = started + timedelta(seconds=float(row["timeout_s"]) + RECOVERY_GRACE_S) if started else now
                if now < deadline:
                    continue
                db.execute("UPDATE operations SET status='interrupted', finished_at=?, error_type='Interrupted', "
                           "error_message=?, verified=NULL, arguments_json=? WHERE id=? AND status='started'",
                           (to_iso(now), "Process stopped while the operation was running; outcome unknown",
                            dumps(_generic_redaction(loads(row["arguments_json"], {}))), row["id"]))
                self.store.events.append("operation.interrupted", actor="system", target=row["capability"],
                                         operation_id=row["id"], session_id=row["session_id"],
                                         cycle_id=row["correlation_id"], decision="interrupted",
                                         payload={"started_at": row["started_at"], "outcome": "unknown"}, conn=db)
                recovered.append(self._decode(db.execute("SELECT * FROM operations WHERE id=?",
                                                         (row["id"],)).fetchone()))
        return recovered

    def stats(self) -> dict[str, dict[str, int]]:
        """Observed outcome counts per capability (grounds the self-model in real history)."""
        with self.store.read() as db:
            rows = db.execute("SELECT capability, status, COUNT(*) n FROM operations GROUP BY capability, status")
            out: dict[str, dict[str, int]] = {}
            for r in rows:
                out.setdefault(r["capability"], {})[r["status"]] = r["n"]
            verified = db.execute("SELECT capability, SUM(CASE WHEN verified=1 THEN 1 ELSE 0 END) v, "
                                  "SUM(CASE WHEN verified=0 THEN 1 ELSE 0 END) f FROM operations GROUP BY capability")
            for r in verified:
                out.setdefault(r["capability"], {})["verified"] = int(r["v"] or 0)
                out[r["capability"]]["verification_failed"] = int(r["f"] or 0)
        return out
