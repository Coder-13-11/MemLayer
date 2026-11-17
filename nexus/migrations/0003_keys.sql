-- Programmatic access and bring-your-own-key provider credentials.

-- A NEXUS API key acts as a workspace member with a fixed role. Only its hash is stored; the prefix is for display.
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('editor', 'viewer')),
    scopes TEXT NOT NULL DEFAULT '["memory","gateway"]',
    default_project TEXT,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    expires_at BIGINT,
    revoked_at TEXT
);
CREATE INDEX api_keys_workspace ON api_keys(workspace_id);

-- A provider API key the workspace brought. Envelope-encrypted: the key is sealed with a per-row data key, and
-- the data key is sealed with the service's master key (key_version says which one). See nexus/crypto.py.
CREATE TABLE provider_credentials (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT 'default',
    base_url TEXT,
    ciphertext TEXT NOT NULL,
    wrapped_key TEXT NOT NULL,
    key_version TEXT NOT NULL,
    hint TEXT NOT NULL,
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    UNIQUE (workspace_id, provider, label)
);
