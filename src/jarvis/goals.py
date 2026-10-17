"""Goals and tasks with explicit state, dependencies, and evidence-based completion.

Task state machine (invalid transitions raise :class:`InvalidTransition`)::

    todo        -> in_progress | blocked | done | cancelled
    in_progress -> todo | blocked | done | cancelled
    blocked     -> todo | in_progress | done | cancelled
    done        -> todo | in_progress            (reopen; reason required)
    cancelled   -> todo                          (reinstate)

A same-state request is an idempotent no-op.

Completion requires evidence (Acceptance tests 5, 7, 11):
- ``user_confirmation``: the user said it is done (actor must be the user);
- ``operation_verified``: an operation in the journal with status ``succeeded``
  *and* a passed postcondition check (``verified = 1``) — checked against the
  database, not taken from the caller's word.
A language model's statement that something is done is never evidence.

Starting or completing a task requires all of its dependencies to be done or
cancelled. Goal status is ``proposed`` (inferred, not yet authorised), ``active``,
``completed`` (automatically, when every non-cancelled task is done — the task
completion events are recorded as evidence) or ``abandoned``. Blockedness is
derived from tasks rather than stored, so it cannot drift out of date.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from .util import bounded, dumps, loads, to_iso

if TYPE_CHECKING:  # pragma: no cover
    from .db import Store

TASK_STATUSES = ("todo", "in_progress", "blocked", "done", "cancelled")
TRANSITIONS: dict[str, set[str]] = {
    "todo": {"in_progress", "blocked", "done", "cancelled"},
    "in_progress": {"todo", "blocked", "done", "cancelled"},
    "blocked": {"todo", "in_progress", "done", "cancelled"},
    "done": {"todo", "in_progress"},
    "cancelled": {"todo"},
}
GOAL_STATUSES = ("proposed", "active", "completed", "abandoned")
EVIDENCE_TYPES = ("user_confirmation", "operation_verified")


class GoalError(ValueError):
    pass


class InvalidTransition(GoalError):
    pass


class DependencyBlocked(GoalError):
    pass


class EvidenceRejected(GoalError):
    pass


class GoalStore:
    def __init__(self, store: "Store"):
        self.store = store

    # ------------------------------------------------------------------ goals
    def create_goal(self, title: str, *, description: str = "", steps: list[str] | None = None,
                    origin: str = "user", priority: int = 3, project: str | None = None,
                    success_criteria: str = "", due_at: str | None = None, source_event_id: int | None = None,
                    session_id: str | None = None, cycle_id: str | None = None) -> dict[str, Any]:
        title = (title or "").strip()
        if not title or len(title) > 240:
            raise GoalError("Goal title must contain 1-240 characters")
        if origin not in {"user", "inferred"}:
            raise GoalError("Goal origin must be 'user' or 'inferred'")
        if not 1 <= int(priority) <= 5:
            raise GoalError("Priority must be between 1 (highest) and 5 (lowest)")
        steps = [s.strip() for s in (steps or []) if s and s.strip()]
        if len(steps) > 25:
            raise GoalError("A goal can be created with at most 25 initial steps")
        # Inferred goals are suggestions: they start unauthorised and inactive.
        authorised, status = (1, "active") if origin == "user" else (0, "proposed")
        with self.store.write() as db:
            now = to_iso(self.store.now())
            project_id = self._project_id(db, project)
            goal_id = int(db.execute(
                "INSERT INTO goals(title, description, origin, authorised, status, priority, project_id, "
                "success_criteria, due_at, created_at, updated_at, source_event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (title, description.strip(), origin, authorised, status, int(priority), project_id,
                 success_criteria.strip(), due_at, now, now, source_event_id)).lastrowid)
            self.store.events.append("goal.created", actor="user" if origin == "user" else "jarvis",
                                     target=f"goal:{goal_id}", goal_id=goal_id, session_id=session_id,
                                     cycle_id=cycle_id, caused_by=source_event_id,
                                     payload={"title": title, "origin": origin, "steps": len(steps)}, conn=db)
            for position, step in enumerate(steps, 1):
                self._insert_task(db, step, goal_id=goal_id, project_id=project_id, position=position,
                                  origin=origin, session_id=session_id, cycle_id=cycle_id,
                                  source_event_id=source_event_id)
            return self._goal(db, goal_id)

    def authorise_goal(self, goal_id: int, *, session_id: str | None = None) -> dict[str, Any]:
        with self.store.write() as db:
            goal = self._goal(db, goal_id)
            if goal["status"] != "proposed":
                raise GoalError(f"Goal {goal_id} is {goal['status']}, not proposed")
            db.execute("UPDATE goals SET authorised=1, status='active', updated_at=? WHERE id=?",
                       (to_iso(self.store.now()), goal_id))
            self.store.events.append("goal.authorised", actor="user", target=f"goal:{goal_id}", goal_id=goal_id,
                                     session_id=session_id, conn=db)
            return self._goal(db, goal_id)

    def abandon_goal(self, goal_id: int, *, reason: str = "", session_id: str | None = None) -> dict[str, Any]:
        with self.store.write() as db:
            goal = self._goal(db, goal_id)
            if goal["status"] in {"completed", "abandoned"}:
                raise GoalError(f"Goal {goal_id} is already {goal['status']}")
            db.execute("UPDATE goals SET status='abandoned', updated_at=? WHERE id=?",
                       (to_iso(self.store.now()), goal_id))
            self.store.events.append("goal.abandoned", actor="user", target=f"goal:{goal_id}", goal_id=goal_id,
                                     session_id=session_id, payload={"reason": bounded(reason, 300)}, conn=db)
            return self._goal(db, goal_id)

    @staticmethod
    def _goal(db: sqlite3.Connection, goal_id: int) -> dict[str, Any]:
        row = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        if not row:
            raise GoalError(f"Unknown goal id: {goal_id}")
        return dict(row)

    def get_goal(self, goal_id: int) -> dict[str, Any]:
        with self.store.read() as db:
            return self._goal(db, goal_id)

    def list_goals(self, statuses: tuple[str, ...] = ("active", "proposed")) -> list[dict[str, Any]]:
        with self.store.read() as db:
            rows = db.execute(f"SELECT * FROM goals WHERE status IN ({','.join('?' * len(statuses))}) "
                              "ORDER BY priority, created_at", statuses).fetchall()
        return [dict(r) for r in rows]

    def progress(self, goal_id: int) -> dict[str, Any]:
        """Derived progress view: counts, next actionable tasks, blockers, failed attempts."""
        with self.store.read() as db:
            goal = self._goal(db, goal_id)
            tasks = [dict(r) for r in db.execute("SELECT * FROM tasks WHERE goal_id=? ORDER BY position, id",
                                                 (goal_id,))]
            deps = {}
            for r in db.execute("SELECT d.task_id, d.depends_on, t.status FROM task_dependencies d "
                                "JOIN tasks t ON t.id=d.depends_on WHERE d.task_id IN "
                                "(SELECT id FROM tasks WHERE goal_id=?)", (goal_id,)):
                deps.setdefault(r["task_id"], []).append((r["depends_on"], r["status"]))
        counts = {s: 0 for s in TASK_STATUSES}
        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        countable = [t for t in tasks if t["status"] != "cancelled"]
        actionable = [t for t in tasks if t["status"] in {"todo", "in_progress"}
                      and all(s in {"done", "cancelled"} for _, s in deps.get(t["id"], []))]
        waiting = [t for t in tasks if t["status"] in {"todo", "in_progress"} and t not in actionable]
        return {
            "goal": goal, "tasks": tasks, "counts": counts,
            "done": counts["done"], "total": len(countable),
            "percent": round(100.0 * counts["done"] / len(countable), 1) if countable else 0.0,
            "next": actionable[:3],
            "blocked": [t for t in tasks if t["status"] == "blocked"],
            "waiting_on_dependencies": [{"task": t["id"], "depends_on": [d for d, s in deps.get(t["id"], [])
                                                                          if s not in {"done", "cancelled"}]}
                                        for t in waiting],
            "failed_attempts": [t for t in tasks if t["last_error"]],
        }

    # ------------------------------------------------------------------ tasks
    @staticmethod
    def _project_id(db: sqlite3.Connection, project: str | None) -> int | None:
        if not project:
            return None
        row = db.execute("SELECT id FROM projects WHERE name=? COLLATE NOCASE", (project,)).fetchone()
        if not row:
            raise GoalError(f"Unknown project: {project}")
        return int(row["id"])

    def _insert_task(self, db: sqlite3.Connection, title: str, *, goal_id: int | None, project_id: int | None,
                     position: int = 0, description: str = "", priority: int = 3, due_at: str | None = None,
                     origin: str = "user", session_id: str | None = None, cycle_id: str | None = None,
                     source_event_id: int | None = None) -> int:
        title = (title or "").strip()
        if not title or len(title) > 240:
            raise GoalError("Task title must contain 1-240 characters")
        if not 1 <= int(priority) <= 5:
            raise GoalError("Priority must be between 1 (highest) and 5 (lowest)")
        now = to_iso(self.store.now())
        task_id = int(db.execute(
            "INSERT INTO tasks(project_id, goal_id, title, description, priority, due_at, position, origin, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (project_id, goal_id, title, description.strip(), int(priority), due_at, position, origin,
             now, now)).lastrowid)
        self.store.events.append("task.created", actor="user" if origin == "user" else "jarvis",
                                 target=f"task:{task_id}", task_id=task_id, goal_id=goal_id, session_id=session_id,
                                 cycle_id=cycle_id, caused_by=source_event_id, payload={"title": title}, conn=db)
        return task_id

    def add_task(self, title: str, *, project: str | None = None, goal_id: int | None = None,
                 description: str = "", priority: int = 3, due_at: str | None = None,
                 session_id: str | None = None, cycle_id: str | None = None,
                 source_event_id: int | None = None) -> dict[str, Any]:
        with self.store.write() as db:
            project_id = self._project_id(db, project)
            position = 0
            if goal_id is not None:
                goal = self._goal(db, goal_id)
                if goal["status"] in {"completed", "abandoned"}:
                    raise GoalError(f"Goal {goal_id} is {goal['status']}; reopen it before adding tasks")
                project_id = project_id or goal["project_id"]
                position = int(db.execute("SELECT coalesce(max(position), 0) + 1 FROM tasks WHERE goal_id=?",
                                          (goal_id,)).fetchone()[0])
            task_id = self._insert_task(db, title, goal_id=goal_id, project_id=project_id, position=position,
                                        description=description, priority=priority, due_at=due_at,
                                        session_id=session_id, cycle_id=cycle_id, source_event_id=source_event_id)
            return self._task(db, task_id)

    @staticmethod
    def _task(db: sqlite3.Connection, task_id: int) -> dict[str, Any]:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise GoalError(f"Unknown task id: {task_id}")
        return dict(row)

    def get_task(self, task_id: int) -> dict[str, Any]:
        with self.store.read() as db:
            return self._task(db, task_id)

    def add_dependency(self, task_id: int, depends_on: int) -> None:
        if task_id == depends_on:
            raise GoalError("A task cannot depend on itself")
        with self.store.write() as db:
            self._task(db, task_id)
            self._task(db, depends_on)
            # Reject cycles: depends_on must not (transitively) depend on task_id.
            frontier, seen = [depends_on], set()
            while frontier:
                current = frontier.pop()
                if current == task_id:
                    raise GoalError("Dependency would create a cycle")
                if current in seen:
                    continue
                seen.add(current)
                frontier.extend(r[0] for r in db.execute(
                    "SELECT depends_on FROM task_dependencies WHERE task_id=?", (current,)))
            db.execute("INSERT OR IGNORE INTO task_dependencies(task_id, depends_on) VALUES (?,?)", (task_id, depends_on))
            self.store.events.append("task.dependency_added", actor="user", target=f"task:{task_id}", task_id=task_id,
                                     payload={"depends_on": depends_on}, conn=db)

    def _check_evidence(self, db: sqlite3.Connection, evidence: dict[str, Any] | None, actor: str) -> dict[str, Any]:
        if not isinstance(evidence, dict) or evidence.get("type") not in EVIDENCE_TYPES:
            raise EvidenceRejected("Completing a task requires evidence: user_confirmation or operation_verified")
        if evidence["type"] == "user_confirmation":
            if actor != "user":
                raise EvidenceRejected("Only the user can provide user_confirmation evidence")
            return {"type": "user_confirmation", "event_id": evidence.get("event_id")}
        op_id = evidence.get("operation_id")
        row = db.execute("SELECT id, status, verified, capability FROM operations WHERE id=?", (op_id,)).fetchone()
        if not row:
            raise EvidenceRejected(f"Operation {op_id} does not exist")
        if row["status"] != "succeeded" or row["verified"] != 1:
            raise EvidenceRejected(
                f"Operation {op_id} is {row['status']} (verified={row['verified']}); only succeeded and verified "
                "operations are evidence of completion")
        return {"type": "operation_verified", "operation_id": row["id"], "capability": row["capability"]}

    def set_task_status(self, task_id: int, status: str, *, actor: str = "user",
                        evidence: dict[str, Any] | None = None, reason: str = "", blocker: str | None = None,
                        session_id: str | None = None, cycle_id: str | None = None,
                        source_event_id: int | None = None) -> dict[str, Any]:
        if status not in TASK_STATUSES:
            raise GoalError("Invalid task status")
        with self.store.write() as db:
            task = self._task(db, task_id)
            current = task["status"]
            if current == status:
                return task
            if status not in TRANSITIONS[current]:
                raise InvalidTransition(f"Task {task_id} cannot move from {current} to {status}")
            if current == "done" and not reason:
                raise InvalidTransition("Reopening a completed task requires a reason")
            if status in {"in_progress", "done"}:
                pending = [r[0] for r in db.execute(
                    "SELECT d.depends_on FROM task_dependencies d JOIN tasks t ON t.id=d.depends_on "
                    "WHERE d.task_id=? AND t.status NOT IN ('done','cancelled')", (task_id,))]
                if pending:
                    raise DependencyBlocked(f"Task {task_id} depends on unfinished task(s): {pending}")
            now = to_iso(self.store.now())
            proof = None
            if status == "done":
                if evidence is None and actor == "user":
                    evidence = {"type": "user_confirmation", "event_id": source_event_id}
                proof = self._check_evidence(db, evidence, actor)
                db.execute("UPDATE tasks SET status='done', blocker=NULL, completion_evidence_json=?, completed_at=?, "
                           "updated_at=? WHERE id=?", (dumps(proof), now, now, task_id))
            elif status == "blocked":
                db.execute("UPDATE tasks SET status='blocked', blocker=?, updated_at=? WHERE id=?",
                           (bounded(blocker or reason or "unspecified", 500), now, task_id))
            else:
                db.execute("UPDATE tasks SET status=?, blocker=NULL, completion_evidence_json=NULL, completed_at=NULL, "
                           "updated_at=? WHERE id=?", (status, now, task_id))
            event_id = self.store.events.append(
                "task.status_changed", actor=actor, target=f"task:{task_id}", task_id=task_id,
                goal_id=task["goal_id"], session_id=session_id, cycle_id=cycle_id, caused_by=source_event_id,
                payload={"from": current, "to": status, "evidence": proof, "reason": bounded(reason, 300),
                         "blocker": bounded(blocker or "", 300) or None}, conn=db)
            if task["goal_id"]:
                self._refresh_goal(db, task["goal_id"], event_id, session_id, cycle_id)
            return self._task(db, task_id)

    def record_attempt(self, task_id: int, *, error: str | None, operation_id: int | None = None,
                       session_id: str | None = None, cycle_id: str | None = None) -> dict[str, Any]:
        """Record an execution attempt for a task (failed attempts keep the error for replanning)."""
        with self.store.write() as db:
            task = self._task(db, task_id)
            db.execute("UPDATE tasks SET attempts=attempts+1, last_error=?, updated_at=? WHERE id=?",
                       (bounded(error, 500) if error else None, to_iso(self.store.now()), task_id))
            self.store.events.append("task.attempted", actor="jarvis", target=f"task:{task_id}", task_id=task_id,
                                     goal_id=task["goal_id"], operation_id=operation_id, session_id=session_id,
                                     cycle_id=cycle_id, decision="error" if error else "success",
                                     payload={"error": bounded(error or "", 300) or None}, conn=db)
            return self._task(db, task_id)

    def _refresh_goal(self, db: sqlite3.Connection, goal_id: int, cause_event: int,
                      session_id: str | None, cycle_id: str | None) -> None:
        goal = self._goal(db, goal_id)
        rows = db.execute("SELECT id, status FROM tasks WHERE goal_id=?", (goal_id,)).fetchall()
        live = [r for r in rows if r["status"] != "cancelled"]
        complete = bool(live) and all(r["status"] == "done" for r in live)
        now = to_iso(self.store.now())
        if complete and goal["status"] == "active":
            proof = {"type": "all_tasks_complete", "tasks": [r["id"] for r in live],
                     "completion_events": [r[0] for r in db.execute(
                         "SELECT max(id) FROM events WHERE kind='task.status_changed' AND goal_id=? "
                         "GROUP BY task_id", (goal_id,))]}
            db.execute("UPDATE goals SET status='completed', completed_at=?, completion_evidence_json=?, updated_at=? "
                       "WHERE id=?", (now, dumps(proof), now, goal_id))
            self.store.events.append("goal.completed", actor="jarvis", target=f"goal:{goal_id}", goal_id=goal_id,
                                     session_id=session_id, cycle_id=cycle_id, caused_by=cause_event,
                                     payload=proof, conn=db)
        elif not complete and goal["status"] == "completed":
            db.execute("UPDATE goals SET status='active', completed_at=NULL, completion_evidence_json=NULL, "
                       "updated_at=? WHERE id=?", (now, goal_id))
            self.store.events.append("goal.reopened", actor="jarvis", target=f"goal:{goal_id}", goal_id=goal_id,
                                     session_id=session_id, cycle_id=cycle_id, caused_by=cause_event, conn=db)

    def list_tasks(self, status: str | None = None, project: str | None = None,
                   goal_id: int | None = None) -> list[dict[str, Any]]:
        if status and status not in TASK_STATUSES:
            raise GoalError("Invalid task status")
        sql = "SELECT t.*, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id=t.project_id"
        clauses, args = [], []
        if status:
            clauses.append("t.status=?")
            args.append(status)
        if project:
            clauses.append("p.name=? COLLATE NOCASE")
            args.append(project)
        if goal_id is not None:
            clauses.append("t.goal_id=?")
            args.append(goal_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY t.priority ASC, t.created_at ASC, t.id ASC"
        with self.store.read() as db:
            rows = [dict(r) for r in db.execute(sql, args)]
        for row in rows:
            row["completion_evidence"] = loads(row.pop("completion_evidence_json", None))
        return rows
