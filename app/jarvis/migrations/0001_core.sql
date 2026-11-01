-- Core identity, audit and audio-session tables (migration 0001).

CREATE TABLE clients (
    id              TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    platform        TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('operator', 'device')),
    scopes          TEXT NOT NULL,           -- JSON array of granted scope ids
    token_hash      TEXT NOT NULL UNIQUE,    -- HMAC-SHA256(server secret, token); never the token
    token_prefix    TEXT NOT NULL,           -- first 8 characters, for operator display only
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    revoked_at      TEXT,
    revoked_reason  TEXT,
    last_seen_at    TEXT,
    paired_from     TEXT REFERENCES pairing_codes(id)
);

CREATE INDEX idx_clients_token_hash ON clients(token_hash);
CREATE INDEX idx_clients_kind ON clients(kind, revoked_at);

CREATE TABLE pairing_codes (
    id               TEXT PRIMARY KEY,
    code_hash        TEXT NOT NULL,          -- HMAC-SHA256(server secret, code)
    label_hint       TEXT NOT NULL,
    platform         TEXT NOT NULL,
    scopes           TEXT NOT NULL,          -- JSON array the paired device may receive
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    used_at          TEXT,
    failed_attempts  INTEGER NOT NULL DEFAULT 0,
    created_by       TEXT NOT NULL
);

CREATE INDEX idx_pairing_codes_created_at ON pairing_codes(created_at);

CREATE TABLE client_grants (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id    TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    manifest_id  TEXT NOT NULL,
    scopes       TEXT NOT NULL,
    granted_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    revoked_at   TEXT
);

CREATE INDEX idx_client_grants_client ON client_grants(client_id);

CREATE TABLE audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    request_id  TEXT,
    actor_kind  TEXT NOT NULL,
    actor_id    TEXT,
    action      TEXT NOT NULL,
    target      TEXT,
    outcome     TEXT NOT NULL CHECK (outcome IN ('allowed', 'denied', 'error', 'info')),
    detail      TEXT
);

CREATE INDEX idx_audit_at ON audit(at);
CREATE INDEX idx_audit_actor ON audit(actor_id, at);

CREATE TABLE audio_sessions (
    id            TEXT PRIMARY KEY,
    client_id     TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    started_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    ended_at      TEXT,
    content_type  TEXT,
    byte_count    INTEGER NOT NULL DEFAULT 0,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    state         TEXT NOT NULL CHECK (state IN ('open', 'ended', 'expired')),
    max_bytes     INTEGER NOT NULL
);

CREATE INDEX idx_audio_sessions_client ON audio_sessions(client_id, state);
