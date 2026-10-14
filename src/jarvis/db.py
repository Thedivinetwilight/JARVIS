"""SQLite persistence and audit log. Uses only the standard library."""
from __future__ import annotations
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _init(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS projects (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
          description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
          id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
          title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'todo', priority INTEGER NOT NULL DEFAULT 3,
          due_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority);
        CREATE TABLE IF NOT EXISTS memories (
          id INTEGER PRIMARY KEY, category TEXT NOT NULL DEFAULT 'general',
          key TEXT NOT NULL, value TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 1.0,
          source TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(category, key)
        );
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, actor TEXT NOT NULL,
          action TEXT NOT NULL, target TEXT NOT NULL, decision TEXT NOT NULL,
          details_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_audit_time ON audit(timestamp);
        """
        with self._connect() as db:
            db.executescript(schema)

    def audit(self, actor: str, action: str, target: str, decision: str,
              details: dict[str, Any] | None = None) -> None:
        # Keep this record small and never pass provider keys to details.
        safe = json.dumps(details or {}, ensure_ascii=False, default=str)
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO audit(timestamp,actor,action,target,decision,details_json) VALUES(?,?,?,?,?,?)",
                       (now_iso(), actor, action, target, decision, safe))

    def add_project(self, name: str, description: str = "") -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("Project name must contain 1-100 characters")
        stamp = now_iso()
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO projects(name,description,created_at,updated_at) VALUES(?,?,?,?)",
                       (name, description.strip(), stamp, stamp))
            row = db.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
        self.audit("user", "project.create", name, "allowed")
        return dict(row)

    def list_projects(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY name")]

    def get_project(self, name: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM projects WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            return dict(row) if row else None

    def add_task(self, title: str, project: str | None = None, description: str = "",
                 priority: int = 3, due_at: str | None = None) -> dict[str, Any]:
        title = title.strip()
        if not title or len(title) > 240:
            raise ValueError("Task title must contain 1-240 characters")
        if not 1 <= priority <= 5:
            raise ValueError("Priority must be between 1 (highest) and 5 (lowest)")
        stamp = now_iso()
        with self._lock, self._connect() as db:
            project_id = None
            if project:
                row = db.execute("SELECT id FROM projects WHERE name=? COLLATE NOCASE", (project,)).fetchone()
                if not row:
                    raise ValueError(f"Unknown project: {project}")
                project_id = row["id"]
            cur = db.execute("""INSERT INTO tasks(project_id,title,description,priority,due_at,created_at,updated_at)
                                VALUES(?,?,?,?,?,?,?)""",
                             (project_id, title, description.strip(), priority, due_at, stamp, stamp))
            task = db.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
        self.audit("user", "task.create", str(task["id"]), "allowed", {"title": title, "project": project})
        return dict(task)

    def list_tasks(self, status: str | None = None, project: str | None = None) -> list[dict[str, Any]]:
        allowed = {"todo", "in_progress", "blocked", "done", "cancelled"}
        if status and status not in allowed:
            raise ValueError("Invalid task status")
        sql = """SELECT t.*, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id=t.project_id"""
        clauses, args = [], []
        if status:
            clauses.append("t.status=?"); args.append(status)
        if project:
            clauses.append("p.name=? COLLATE NOCASE"); args.append(project)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY t.priority ASC, t.created_at ASC"
        with self._connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def set_task_status(self, task_id: int, status: str) -> dict[str, Any]:
        allowed = {"todo", "in_progress", "blocked", "done", "cancelled"}
        if status not in allowed:
            raise ValueError("Invalid task status")
        with self._lock, self._connect() as db:
            cur = db.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, now_iso(), task_id))
            if not cur.rowcount:
                raise ValueError(f"Unknown task id: {task_id}")
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        self.audit("user", "task.status", str(task_id), "allowed", {"status": status})
        return dict(row)

    def remember(self, key: str, value: str, category: str = "general",
                 confidence: float = 1.0, source: str = "user") -> dict[str, Any]:
        if not key.strip() or len(key) > 160:
            raise ValueError("Memory key must contain 1-160 characters")
        if not 0 <= confidence <= 1:
            raise ValueError("Confidence must be between 0 and 1")
        stamp = now_iso()
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO memories(category,key,value,confidence,source,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(category,key) DO UPDATE SET
              value=excluded.value, confidence=excluded.confidence, source=excluded.source, updated_at=excluded.updated_at""",
              (category, key.strip(), value, confidence, source, stamp, stamp))
            row = db.execute("SELECT * FROM memories WHERE category=? AND key=?", (category, key.strip())).fetchone()
        self.audit("user", "memory.upsert", f"{category}:{key}", "allowed")
        return dict(row)

    def search_memories(self, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        with self._connect() as db:
            if query.strip():
                needle = f"%{query.strip()}%"
                rows = db.execute("SELECT * FROM memories WHERE key LIKE ? OR value LIKE ? OR category LIKE ? ORDER BY updated_at DESC LIMIT ?",
                                  (needle, needle, needle, limit))
            else:
                rows = db.execute("SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with self._connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            counts = {row["status"]: row["n"] for row in db.execute("SELECT status,COUNT(*) n FROM tasks GROUP BY status")}
            return {"projects": db.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                    "tasks": db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                    "memories": db.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
                    "tasks_by_status": counts, "database": str(self.path)}
