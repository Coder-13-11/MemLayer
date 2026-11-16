-- Control plane v1: users, OAuth clients, grants, tokens, sessions (the tables the hosted service started with).
-- Written in the SQL subset SQLite and Postgres share. IF NOT EXISTS makes this a no-op on databases created
-- before migrations were numbered. Emails are stored lowercased by the code, so plain UNIQUE is case-insensitive.

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    info TEXT NOT NULL,
    created_at TEXT NOT NULL
);
-- An authorization request waiting for the user to sign in and approve it.
CREATE TABLE IF NOT EXISTS auth_requests (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    params TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    data TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);
-- One grant per approval of an app; every token issued under it shares its id, so revoking is one update.
CREATE TABLE IF NOT EXISTS grants (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    client_name TEXT,
    scopes TEXT NOT NULL,
    resource TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS grants_user ON grants(user_id);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    grant_id TEXT NOT NULL REFERENCES grants(id) ON DELETE CASCADE,
    expires_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS tokens_grant ON tokens(grant_id);
CREATE TABLE IF NOT EXISTS beta_requests (
    email TEXT PRIMARY KEY,
    topic TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
-- Browser sign-in sessions (only the hash of the session id is stored).
CREATE TABLE IF NOT EXISTS web_sessions (
    id_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at BIGINT NOT NULL
);
