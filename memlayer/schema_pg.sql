-- memlayer schema v1, Postgres edition. Same tables and meaning as schema.sql; each account gets its own schema,
-- so these statements run with search_path set to that schema. Differences from SQLite:
--   * seq columns stand in for SQLite's rowid (insertion order);
--   * full-text search uses generated tsvector columns with GIN indexes instead of FTS5 tables and triggers;
--   * case-insensitive project names use a unique index on lower(name).

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    objective   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS projects_name ON projects (lower(name));

CREATE TABLE IF NOT EXISTS sources (
    seq             BIGSERIAL,
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('conversation', 'llm_output', 'document', 'manual_note', 'web_page')),
    title           TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL,
    url             TEXT,
    origin_model    TEXT,
    conversation_id TEXT,
    content_sha256  TEXT NOT NULL,
    imported_at     TEXT NOT NULL,
    fts             tsvector GENERATED ALWAYS AS (
                        setweight(to_tsvector('english', title), 'A') || to_tsvector('english', content)) STORED,
    UNIQUE (project_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS sources_fts ON sources USING gin (fts);

CREATE TABLE IF NOT EXISTS records (
    seq             BIGSERIAL,
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('objective', 'finding', 'decision', 'assumption', 'hypothesis',
                                                  'evidence', 'question', 'task', 'note')),
    title           TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    subject         TEXT,
    value           TEXT,
    importance      INTEGER NOT NULL DEFAULT 2 CHECK (importance BETWEEN 1 AND 3),
    verification    TEXT NOT NULL DEFAULT 'unverified'
                    CHECK (verification IN ('unverified', 'evidence_supported', 'challenged', 'contradicted')),
    lifecycle       TEXT NOT NULL DEFAULT 'active'
                    CHECK (lifecycle IN ('active', 'superseded', 'resolved', 'retracted')),
    superseded_by   TEXT REFERENCES records(id) ON DELETE SET NULL,
    tags            TEXT NOT NULL DEFAULT '[]',
    origin_model    TEXT,
    conversation_id TEXT,
    created_by      TEXT,
    source_id       TEXT REFERENCES sources(id) ON DELETE SET NULL,
    source_excerpt  TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    fts             tsvector GENERATED ALWAYS AS (
                        setweight(to_tsvector('english', title), 'A')
                        || setweight(to_tsvector('english', content), 'B')
                        || to_tsvector('simple', coalesce(subject, '') || ' ' || coalesce(value, '') || ' ' || tags)
                    ) STORED
);
CREATE INDEX IF NOT EXISTS records_project ON records(project_id, lifecycle);
CREATE INDEX IF NOT EXISTS records_subject ON records(project_id, subject);
CREATE INDEX IF NOT EXISTS records_fts ON records USING gin (fts);

CREATE TABLE IF NOT EXISTS evidence (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    url         TEXT,
    citation    TEXT,
    excerpt     TEXT,
    source_id   TEXT REFERENCES sources(id) ON DELETE SET NULL,
    checked_at  TEXT,
    added_at    TEXT NOT NULL,
    added_by    TEXT
);
CREATE INDEX IF NOT EXISTS evidence_record ON evidence(record_id);

CREATE TABLE IF NOT EXISTS relations (
    seq         BIGSERIAL,
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    from_id     TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    to_id       TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    type        TEXT NOT NULL CHECK (type IN ('supports', 'challenges', 'contradicts', 'depends_on', 'requires',
                                              'supersedes', 'answers', 'relates_to')),
    note        TEXT,
    created_at  TEXT NOT NULL,
    created_by  TEXT,
    UNIQUE (from_id, to_id, type),
    CHECK (from_id <> to_id)
);
CREATE INDEX IF NOT EXISTS relations_to ON relations(to_id);

CREATE TABLE IF NOT EXISTS record_history (
    id            BIGSERIAL PRIMARY KEY,
    record_id     TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    version       INTEGER NOT NULL,
    snapshot      TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    changed_by    TEXT,
    changed_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_record ON record_history(record_id);
