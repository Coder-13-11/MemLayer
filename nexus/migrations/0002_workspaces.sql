-- Teams. A workspace owns one memory store (a file, or a Postgres schema); people reach it through a membership.
-- Tenant isolation is physical: a workspace's records live in a store no other workspace's queries can open.

ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN email_verified_at TEXT;

CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    -- Where the memory lives: a path under the data directory (SQLite) or a schema name (Postgres).
    store_ref TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL DEFAULT 'free',
    limits TEXT NOT NULL DEFAULT '{}',
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE memberships (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX memberships_user ON memberships(user_id);

-- An invitation is a single-use link. Only the hash of its token is stored.
CREATE TABLE invitations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
    token_hash TEXT NOT NULL UNIQUE,
    invited_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    expires_at BIGINT NOT NULL,
    accepted_at TEXT,
    revoked_at TEXT
);
CREATE INDEX invitations_workspace ON invitations(workspace_id);

-- An app's access is to one workspace, and can be read-only.
ALTER TABLE grants ADD COLUMN workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE;
ALTER TABLE grants ADD COLUMN access TEXT NOT NULL DEFAULT 'write';

-- Single-use links sent by email: password reset and address verification.
CREATE TABLE email_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose IN ('reset', 'verify')),
    expires_at BIGINT NOT NULL
);

-- Every message the service wants to send. With no mail server configured, messages stay here for the operator.
CREATE TABLE outbox (
    id TEXT PRIMARY KEY,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    error TEXT
);
