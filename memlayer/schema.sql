-- memlayer schema v1
-- Original source text (sources) is kept separate from structured records
-- extracted from it (records). Records link to each other through typed
-- relations, and every change to a record snapshots the prior version into
-- record_history.

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    objective   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Immutable original material: pasted conversations, LLM outputs, documents.
CREATE TABLE IF NOT EXISTS sources (
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
    UNIQUE (project_id, content_sha256)
);

-- Structured knowledge: findings, decisions, assumptions, questions, ...
-- verification = how well-supported the claim is.
-- lifecycle    = whether the record still applies.
CREATE TABLE IF NOT EXISTS records (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('objective', 'finding', 'decision', 'assumption', 'hypothesis',
                                                  'evidence', 'question', 'task', 'note')),
    title           TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    subject         TEXT,            -- optional normalized topic key, e.g. "sentinel2.revisit_days"
    value           TEXT,            -- optional claimed value for that subject, e.g. "5"
    importance      INTEGER NOT NULL DEFAULT 2 CHECK (importance BETWEEN 1 AND 3),
    verification    TEXT NOT NULL DEFAULT 'unverified'
                    CHECK (verification IN ('unverified', 'evidence_supported', 'challenged', 'contradicted')),
    lifecycle       TEXT NOT NULL DEFAULT 'active'
                    CHECK (lifecycle IN ('active', 'superseded', 'resolved', 'retracted')),
    superseded_by   TEXT REFERENCES records(id) ON DELETE SET NULL,
    tags            TEXT NOT NULL DEFAULT '[]',   -- JSON array
    origin_model    TEXT,            -- which model/app produced the claim (chatgpt, claude, human, ...)
    conversation_id TEXT,
    created_by      TEXT,            -- which client wrote the row
    source_id       TEXT REFERENCES sources(id) ON DELETE SET NULL,
    source_excerpt  TEXT,            -- the exact span of the source this record came from
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_project ON records(project_id, lifecycle);
CREATE INDEX IF NOT EXISTS records_subject ON records(project_id, subject);

CREATE TABLE IF NOT EXISTS evidence (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    url         TEXT,
    citation    TEXT,
    excerpt     TEXT,
    source_id   TEXT REFERENCES sources(id) ON DELETE SET NULL,
    checked_at  TEXT,                -- when someone actually checked this evidence
    added_at    TEXT NOT NULL,
    added_by    TEXT
);
CREATE INDEX IF NOT EXISTS evidence_record ON evidence(record_id);

-- Directed: from_id <type> to_id, e.g. decision depends_on finding.
CREATE TABLE IF NOT EXISTS relations (
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
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id     TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    version       INTEGER NOT NULL,
    snapshot      TEXT NOT NULL,      -- JSON of the row as it was before the change
    change_reason TEXT NOT NULL,
    changed_by    TEXT,
    changed_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_record ON record_history(record_id);

-- Full-text search (external-content FTS5 tables kept in sync by triggers).
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    title, content, subject, value, tags, content='records', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
    INSERT INTO records_fts(rowid, title, content, subject, value, tags)
    VALUES (new.rowid, new.title, new.content, coalesce(new.subject, ''), coalesce(new.value, ''), new.tags);
END;
CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, title, content, subject, value, tags)
    VALUES ('delete', old.rowid, old.title, old.content, coalesce(old.subject, ''), coalesce(old.value, ''), old.tags);
END;
CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, title, content, subject, value, tags)
    VALUES ('delete', old.rowid, old.title, old.content, coalesce(old.subject, ''), coalesce(old.value, ''), old.tags);
    INSERT INTO records_fts(rowid, title, content, subject, value, tags)
    VALUES (new.rowid, new.title, new.content, coalesce(new.subject, ''), coalesce(new.value, ''), new.tags);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts USING fts5(
    title, content, content='sources', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS sources_ai AFTER INSERT ON sources BEGIN
    INSERT INTO sources_fts(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS sources_ad AFTER DELETE ON sources BEGIN
    INSERT INTO sources_fts(sources_fts, rowid, title, content) VALUES ('delete', old.rowid, old.title, old.content);
END;
