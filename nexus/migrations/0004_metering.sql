-- What happened, for billing, limits and audit.

-- One row per metered call: a gateway request, an MCP tool call, or a REST call.
CREATE TABLE usage_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('gateway', 'mcp', 'api', 'app')),
    actor TEXT,
    operation TEXT NOT NULL,
    project_id TEXT,
    provider TEXT,
    model TEXT,
    status INTEGER NOT NULL DEFAULT 200,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    context_tokens BIGINT NOT NULL DEFAULT 0,
    latency_ms BIGINT NOT NULL DEFAULT 0,
    receipt_id TEXT
);
CREATE INDEX usage_workspace_at ON usage_events(workspace_id, at);

-- A context receipt: exactly which records, at which versions and statuses, were handed to a model.
CREATE TABLE receipts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    at TEXT NOT NULL,
    channel TEXT NOT NULL,
    actor TEXT,
    model TEXT,
    query TEXT,
    record_refs TEXT NOT NULL,
    token_estimate BIGINT NOT NULL DEFAULT 0,
    briefing_sha256 TEXT NOT NULL
);
CREATE INDEX receipts_workspace_at ON receipts(workspace_id, at);

-- Security-relevant actions. Append-only: nothing in the code updates or deletes these rows.
CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    user_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT NOT NULL DEFAULT '{}',
    ip TEXT,
    at TEXT NOT NULL
);
CREATE INDEX audit_workspace_at ON audit_log(workspace_id, at);
