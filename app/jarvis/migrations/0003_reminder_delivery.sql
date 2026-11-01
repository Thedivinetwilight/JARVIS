-- Reminder delivery to explicitly paired clients (migration 0003).
--
-- Delivery here means: a durable, local, per-client queue that a paired client retrieves by
-- asking for it. There is no push channel, no vendor service and no speech: the rows below are
-- the queue, and nothing in this schema can reach the network.
--
-- The UNIQUE constraint is the idempotency guarantee: a reminder can be queued for a given
-- client at most once, so re-running the scheduler, restarting the process or retrying a
-- request can never duplicate a delivery.

-- The target is explicit. NULL means "no delivery target was configured", which is reported
-- as pending-and-unrouted rather than guessed.
ALTER TABLE reminders ADD COLUMN delivery_client_id TEXT REFERENCES clients(id);

-- How the schedule was written: 'explicit_offset' (a Z or ±HH:MM), 'assumed_utc' (a bare wall
-- clock that the server had to interpret), or NULL for rows created before this column existed.
-- Storing it is the only way to stay honest after the value has been normalised to UTC.
ALTER TABLE tasks ADD COLUMN due_at_basis TEXT;

CREATE INDEX idx_reminders_delivery_client ON reminders(delivery_client_id, fire_at);

CREATE TABLE reminder_deliveries (
    id                 TEXT PRIMARY KEY,
    reminder_id        TEXT NOT NULL REFERENCES reminders(id) ON DELETE CASCADE,
    client_id          TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    state              TEXT NOT NULL CHECK (state IN (
                           'pending', 'delivered', 'acknowledged', 'expired', 'failed', 'cancelled')),
    enqueued_at        TEXT NOT NULL,
    first_delivered_at TEXT,
    last_delivered_at  TEXT,
    acknowledged_at    TEXT,
    closed_at          TEXT,
    attempts           INTEGER NOT NULL DEFAULT 0,
    max_attempts       INTEGER NOT NULL,
    expires_at         TEXT NOT NULL,
    failure_reason     TEXT,
    UNIQUE (reminder_id, client_id)
);

CREATE INDEX idx_reminder_deliveries_inbox
    ON reminder_deliveries(client_id, state, enqueued_at);

CREATE INDEX idx_reminder_deliveries_open
    ON reminder_deliveries(state, expires_at);
