-- Tasks and reminders: the first real everyday-assistance capability (migration 0002).
-- No model is involved: these are explicit structured records with local storage.

CREATE TABLE tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    notes         TEXT NOT NULL DEFAULT '',
    due_at        TEXT,
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'structured',
    completed_at  TEXT,
    deleted_at    TEXT
);

CREATE INDEX idx_tasks_open ON tasks(deleted_at, completed_at, due_at);

CREATE TABLE reminders (
    id               TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    fire_at          TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    acknowledged_at  TEXT
);

CREATE INDEX idx_reminders_due ON reminders(acknowledged_at, fire_at);
