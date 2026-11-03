"""MemoryStore: the only module that talks SQL.

Context building and gap analysis work on plain dicts returned by
``project_snapshot`` so the storage backend can be swapped later without
touching them.

Reads run under ``PRAGMA query_only`` so a read path cannot modify memory even
by accident. Every write runs in one transaction and snapshots the previous
version of any record it changes into ``record_history``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Mapping

from . import models as m
from .db import connect
from .pg import PgConnection, is_pg_url, tsquery_terms
from .models import NotFound, ValidationError

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with".split()
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 OR-query of quoted terms."""
    terms = [t for t in re.findall(r"\w+", text.lower()) if t not in _STOPWORDS]
    seen: list[str] = []
    for t in terms:
        if t not in seen:
            seen.append(t)
    return " OR ".join(f'"{t}"' for t in seen[:32])


def _record_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["status"] = m.effective_status(d["verification"], d["lifecycle"])
    d["importance_label"] = m.IMPORTANCE_NAMES[d["importance"]]
    d.pop("rowid", None)
    return d


class MemoryStore:
    def __init__(self, path: str | os.PathLike = ":memory:", *, schema: str = "public"):
        """``path`` is a SQLite file (local use) or a postgres:// URL, in which case ``schema`` (or a ``#schema``
        suffix on the URL) names the Postgres schema that holds this memory (one per account when hosted)."""
        self.path = str(path)
        if is_pg_url(self.path):
            url, _, in_url = self.path.partition("#")  # "postgresql://.../db#schema" also names the schema
            self.conn = PgConnection(url, in_url or schema)
            self._lock = self.conn.lock  # one shared connection per process: transactions must not interleave
        else:
            self.conn = connect(path)
            self._lock = threading.RLock()
        self.dialect = getattr(self.conn, "dialect", "sqlite")
        self._read_depth = 0

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ transactions

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._read_depth == 0:
                if self.dialect == "pg":
                    self.conn.begin(read_only=True)
                else:
                    self.conn.execute("PRAGMA query_only = ON")
            self._read_depth += 1
            try:
                yield self.conn
            finally:
                self._read_depth -= 1
                if self._read_depth == 0:
                    if self.dialect == "pg":
                        self.conn.commit()
                    else:
                        self.conn.execute("PRAGMA query_only = OFF")

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._read_depth:
                raise RuntimeError("write attempted inside a read-only operation")
            if self.dialect == "pg":
                self.conn.begin()
            else:
                self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                if self.dialect == "pg":
                    self.conn.rollback()
                else:
                    self.conn.execute("ROLLBACK")
                raise
            if self.dialect == "pg":
                self.conn.commit()
            else:
                self.conn.execute("COMMIT")

    # ---------------------------------------------------------------- projects

    def _project(self, c: sqlite3.Connection, ref: str) -> sqlite3.Row:
        row = c.execute("SELECT * FROM projects WHERE id = ? OR name = ? COLLATE NOCASE", (ref, ref)).fetchone()
        if row is None:
            raise NotFound(f"No project named or with id {ref!r}")
        return row

    def create_project(self, name: str, description: str = "", objective: str = "") -> dict[str, Any]:
        name = m.require_text(name, "name", 200)
        description = m.require_text(description, "description", 5000, allow_empty=True)
        objective = m.require_text(objective, "objective", 5000, allow_empty=True)
        with self._write() as c:
            if c.execute("SELECT 1 FROM projects WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
                raise ValidationError(f"A project named {name!r} already exists")
            pid, ts = new_id("prj"), now()
            c.execute(
                "INSERT INTO projects (id, name, description, objective, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (pid, name, description, objective, ts, ts),
            )
        return self.get_project(pid)

    def ensure_project(self, name: str, description: str = "", objective: str = "") -> tuple[dict[str, Any], bool]:
        try:
            return self.get_project(name), False
        except NotFound:
            return self.create_project(name, description, objective), True

    def get_project(self, ref: str) -> dict[str, Any]:
        with self._read() as c:
            return dict(self._project(c, ref))

    def update_project(self, ref: str, *, description: str | None = None, objective: str | None = None) -> dict[str, Any]:
        with self._write() as c:
            p = self._project(c, ref)
            c.execute(
                "UPDATE projects SET description = ?, objective = ?, updated_at = ? WHERE id = ?",
                (
                    p["description"] if description is None else m.require_text(description, "description", 5000, True),
                    p["objective"] if objective is None else m.require_text(objective, "objective", 5000, True),
                    now(),
                    p["id"],
                ),
            )
        return self.get_project(p["id"])

    def list_projects(self) -> list[dict[str, Any]]:
        with self._read() as c:
            rows = c.execute(
                """SELECT p.*,
                          (SELECT count(*) FROM records r WHERE r.project_id = p.id) AS record_count,
                          (SELECT count(*) FROM records r WHERE r.project_id = p.id AND r.lifecycle = 'active') AS active_count,
                          (SELECT count(*) FROM sources s WHERE s.project_id = p.id) AS source_count
                   FROM projects p ORDER BY p.updated_at DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def _touch_project(self, c: sqlite3.Connection, project_id: str) -> None:
        c.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now(), project_id))

    # ----------------------------------------------------------------- sources

    def _insert_source(
        self,
        c: sqlite3.Connection,
        project_id: str,
        content: str,
        *,
        kind: str,
        title: str = "",
        url: str | None = None,
        origin_model: str | None = None,
        conversation_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        content = m.require_text(content, "source content", m.MAX_CONTENT)
        m.require_choice(kind, m.SOURCE_KINDS, "source kind")
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = c.execute(
            "SELECT * FROM sources WHERE project_id = ? AND content_sha256 = ?", (project_id, sha)
        ).fetchone()
        if existing:
            return dict(existing), False
        sid = new_id("src")
        c.execute(
            """INSERT INTO sources (id, project_id, kind, title, content, url, origin_model, conversation_id,
                                    content_sha256, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                sid,
                project_id,
                kind,
                m.require_text(title, "title", m.MAX_TITLE, allow_empty=True),
                content,
                m.require_url(url),
                m.optional_text(origin_model, "origin_model", 100),
                m.optional_text(conversation_id, "conversation_id", 200),
                sha,
                now(),
            ),
        )
        return dict(c.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone()), True

    def add_source(self, project_ref: str, content: str, **kw: Any) -> tuple[dict[str, Any], bool]:
        """Store original text. Re-importing identical text returns the existing source."""
        kw.setdefault("kind", "document")
        with self._write() as c:
            p = self._project(c, project_ref)
            source, created = self._insert_source(c, p["id"], content, **kw)
            if created:
                self._touch_project(c, p["id"])
            return source, created

    def get_source(self, source_id: str) -> dict[str, Any]:
        with self._read() as c:
            row = c.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise NotFound(f"No source with id {source_id!r}")
        return dict(row)

    # ----------------------------------------------------------------- records

    def _record(self, c: sqlite3.Connection, record_id: str) -> sqlite3.Row:
        row = c.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise NotFound(f"No memory record with id {record_id!r}")
        return row

    def _snapshot(self, c: sqlite3.Connection, row: sqlite3.Row, reason: str, by: str | None) -> None:
        c.execute(
            "INSERT INTO record_history (record_id, version, snapshot, change_reason, changed_by, changed_at) "
            "VALUES (?,?,?,?,?,?)",
            (row["id"], row["version"], json.dumps(dict(row)), reason, by, now()),
        )

    def _insert_evidence(self, c: sqlite3.Connection, record_id: str, ev: Mapping[str, Any], by: str | None) -> str:
        url = m.require_url(ev.get("url"))
        citation = m.optional_text(ev.get("citation"), "evidence citation", 1000)
        excerpt = m.optional_text(ev.get("excerpt"), "evidence excerpt", 20_000)
        source_id = ev.get("source_id")
        if not (url or citation or excerpt or source_id):
            raise ValidationError("evidence needs at least one of url, citation, excerpt or source_id")
        if source_id and not c.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone():
            raise NotFound(f"No source with id {source_id!r}")
        eid = new_id("evd")
        c.execute(
            "INSERT INTO evidence (id, record_id, url, citation, excerpt, source_id, checked_at, added_at, added_by) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (eid, record_id, url, citation, excerpt, source_id, m.optional_text(ev.get("checked_at"), "checked_at", 40),
             now(), by),
        )
        return eid

    def _insert_relation(
        self,
        c: sqlite3.Connection,
        from_id: str,
        to_id: str,
        rel_type: str,
        note: str | None,
        by: str | None,
    ) -> dict[str, Any]:
        m.require_choice(rel_type, m.RELATION_TYPES, "relation type")
        if from_id == to_id:
            raise ValidationError("a record cannot relate to itself")
        src, dst = self._record(c, from_id), self._record(c, to_id)
        if src["project_id"] != dst["project_id"]:
            raise ValidationError("relations must stay within one project")
        existing = c.execute(
            "SELECT * FROM relations WHERE from_id = ? AND to_id = ? AND type = ?", (from_id, to_id, rel_type)
        ).fetchone()
        if existing:
            return dict(existing)
        rid = new_id("rel")
        c.execute(
            "INSERT INTO relations (id, project_id, from_id, to_id, type, note, created_at, created_by) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (rid, src["project_id"], from_id, to_id, rel_type, m.optional_text(note, "note", 2000), now(), by),
        )
        if rel_type == "supersedes":
            self._apply_supersede(c, old_id=to_id, new_id_=from_id, reason=note or f"Superseded by {from_id}", by=by)
        return dict(c.execute("SELECT * FROM relations WHERE id = ?", (rid,)).fetchone())

    def _apply_supersede(self, c: sqlite3.Connection, *, old_id: str, new_id_: str, reason: str, by: str | None) -> None:
        old = self._record(c, old_id)
        new = self._record(c, new_id_)
        if old["lifecycle"] == "superseded":
            if old["superseded_by"] == new_id_:
                return
            raise ValidationError(f"{old_id} is already superseded by {old['superseded_by']}")
        if new["lifecycle"] != "active":
            raise ValidationError(f"{new_id_} is {new['lifecycle']}; only an active record can supersede another")
        self._snapshot(c, old, reason, by)
        c.execute(
            "UPDATE records SET lifecycle = 'superseded', superseded_by = ?, version = version + 1, updated_at = ? "
            "WHERE id = ?",
            (new_id_, now(), old_id),
        )

    def _possible_duplicates(self, c: sqlite3.Connection, project_id: str, record_id: str, kind: str, title: str) -> list[dict]:
        norm = re.sub(r"\W+", " ", title.lower()).strip()
        rows = c.execute(
            "SELECT id, title FROM records WHERE project_id = ? AND id <> ? AND kind = ? AND lifecycle = 'active'",
            (project_id, record_id, kind),
        ).fetchall()
        return [
            {"id": r["id"], "title": r["title"]}
            for r in rows
            if re.sub(r"\W+", " ", r["title"].lower()).strip() == norm
        ]

    def save_record(
        self,
        project_ref: str,
        *,
        kind: str,
        title: str,
        content: str = "",
        verification: str = "unverified",
        importance: int | str = 2,
        subject: str | None = None,
        value: str | None = None,
        tags: Iterable[str] = (),
        origin_model: str | None = None,
        conversation_id: str | None = None,
        created_by: str | None = None,
        source_id: str | None = None,
        source_excerpt: str | None = None,
        source_text: str | None = None,
        source_url: str | None = None,
        evidence: Iterable[Mapping[str, Any]] = (),
        relations: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Create a record (plus its evidence and outgoing relations) in one transaction.

        ``relations`` are outgoing: ``{"type": "supersedes", "target_id": X}`` means
        *this record supersedes X*, and marks X superseded (history preserved).
        ``source_text`` stores the original wording as an immutable source first.
        """
        m.require_choice(kind, m.RECORD_KINDS, "kind")
        title = m.require_text(title, "title")
        content = m.require_text(content, "content", m.MAX_CONTENT, allow_empty=True)
        m.require_choice(verification, m.VERIFICATION, "verification")
        imp = m.importance_value(importance)
        tags = [m.require_text(t, "tag", 60) for t in tags]
        evidence, relations = list(evidence), list(relations)
        if verification == "evidence_supported" and not evidence:
            raise ValidationError("verification 'evidence_supported' requires at least one evidence item")

        with self._write() as c:
            p = self._project(c, project_ref)
            if source_text:
                src, _ = self._insert_source(
                    c, p["id"], source_text,
                    kind="llm_output" if origin_model and origin_model != "human" else "manual_note",
                    title=title, url=source_url, origin_model=origin_model, conversation_id=conversation_id,
                )
                source_id = src["id"]
            elif source_id and not c.execute(
                "SELECT 1 FROM sources WHERE id = ? AND project_id = ?", (source_id, p["id"])
            ).fetchone():
                raise NotFound(f"No source {source_id!r} in this project")

            rid, ts = new_id("rec"), now()
            c.execute(
                """INSERT INTO records (id, project_id, kind, title, content, subject, value, importance, verification,
                                        lifecycle, tags, origin_model, conversation_id, created_by, source_id,
                                        source_excerpt, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?, 'active', ?,?,?,?,?,?,?,?)""",
                (
                    rid, p["id"], kind, title, content,
                    m.optional_text(subject, "subject", 200), m.optional_text(value, "value", 500),
                    imp, verification, json.dumps(tags),
                    m.optional_text(origin_model, "origin_model", 100),
                    m.optional_text(conversation_id, "conversation_id", 200),
                    m.optional_text(created_by, "created_by", 100),
                    source_id, m.optional_text(source_excerpt, "source_excerpt", 50_000), ts, ts,
                ),
            )
            for ev in evidence:
                self._insert_evidence(c, rid, ev, created_by)
            for rel in relations:
                self._insert_relation(c, rid, m.require_id(rel.get("target_id", ""), "target_id"),
                                      rel.get("type", ""), rel.get("note"), created_by)
            duplicates = self._possible_duplicates(c, p["id"], rid, kind, title)
            self._touch_project(c, p["id"])

        out = self.get_record(rid)
        out["possible_duplicates"] = duplicates
        return out

    def update_record(
        self,
        record_id: str,
        *,
        reason: str,
        changed_by: str | None = None,
        title: str | None = None,
        content: str | None = None,
        verification: str | None = None,
        lifecycle: str | None = None,
        importance: int | str | None = None,
        subject: str | None = None,
        value: str | None = None,
        tags: Iterable[str] | None = None,
        superseded_by: str | None = None,
        add_evidence: Iterable[Mapping[str, Any]] = (),
        add_relations: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Correct, re-status or supersede a record. The old version goes to record_history."""
        reason = m.require_text(reason, "reason", 2000)
        add_evidence, add_relations = list(add_evidence), list(add_relations)
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = m.require_text(title, "title")
        if content is not None:
            changes["content"] = m.require_text(content, "content", m.MAX_CONTENT, allow_empty=True)
        if verification is not None:
            changes["verification"] = m.require_choice(verification, m.VERIFICATION, "verification")
        if lifecycle is not None:
            if lifecycle == "superseded":
                raise ValidationError("use superseded_by=<newer record id> to supersede a record")
            changes["lifecycle"] = m.require_choice(lifecycle, m.LIFECYCLE, "lifecycle")
        if importance is not None:
            changes["importance"] = m.importance_value(importance)
        if subject is not None:
            changes["subject"] = m.optional_text(subject, "subject", 200)
        if value is not None:
            changes["value"] = m.optional_text(value, "value", 500)
        if tags is not None:
            changes["tags"] = json.dumps([m.require_text(t, "tag", 60) for t in tags])
        if not (changes or superseded_by or add_evidence or add_relations):
            raise ValidationError("update_memory was called without any change")

        with self._write() as c:
            row = self._record(c, record_id)
            if superseded_by:
                m.require_id(superseded_by, "superseded_by")
            if changes:
                if changes.get("lifecycle") == "active" and row["lifecycle"] == "superseded":
                    # Undoing a supersession: drop the link, keep the history snapshot.
                    changes["superseded_by"] = None
                    c.execute("DELETE FROM relations WHERE to_id = ? AND type = 'supersedes'", (record_id,))
                self._snapshot(c, row, reason, changed_by)
                cols = ", ".join(f"{k} = ?" for k in changes)
                c.execute(
                    f"UPDATE records SET {cols}, version = version + 1, updated_at = ? WHERE id = ?",
                    (*changes.values(), now(), record_id),
                )
            for ev in add_evidence:
                self._insert_evidence(c, record_id, ev, changed_by)
            for rel in add_relations:
                self._insert_relation(c, record_id, m.require_id(rel.get("target_id", ""), "target_id"),
                                      rel.get("type", ""), rel.get("note"), changed_by)
            if superseded_by:
                self._insert_relation(c, superseded_by, record_id, "supersedes", reason, changed_by)
            after = self._record(c, record_id)
            if after["verification"] == "evidence_supported":
                n = c.execute("SELECT count(*) FROM evidence WHERE record_id = ?", (record_id,)).fetchone()[0]
                if n == 0:
                    raise ValidationError("verification 'evidence_supported' requires at least one evidence item")
            self._touch_project(c, row["project_id"])

        out = self.get_record(record_id)
        out["changed_fields"] = sorted(set(changes) - {"superseded_by"}) + (["lifecycle"] if superseded_by else [])
        return out

    def delete_record(self, record_id: str) -> dict[str, Any]:
        """Hard delete (human control). Cascades to its evidence, relations and history."""
        with self._write() as c:
            row = self._record(c, record_id)
            c.execute("UPDATE records SET superseded_by = NULL WHERE superseded_by = ?", (record_id,))
            c.execute("DELETE FROM records WHERE id = ?", (record_id,))
        return {"deleted": record_id, "title": row["title"]}

    def delete_project(self, ref: str) -> dict[str, Any]:
        with self._write() as c:
            p = self._project(c, ref)
            c.execute("UPDATE records SET superseded_by = NULL WHERE project_id = ?", (p["id"],))
            c.execute("DELETE FROM projects WHERE id = ?", (p["id"],))
        return {"deleted": p["id"], "name": p["name"]}

    # ------------------------------------------------------------------- reads

    def get_record(self, record_id: str, include_source_content: bool = False) -> dict[str, Any]:
        with self._read() as c:
            rec = _record_dict(self._record(c, record_id))
            project = c.execute("SELECT id, name FROM projects WHERE id = ?", (rec["project_id"],)).fetchone()
            rec["project"] = dict(project)
            rec["evidence"] = [
                dict(r) for r in c.execute("SELECT * FROM evidence WHERE record_id = ? ORDER BY added_at", (record_id,))
            ]

            def rel_rows(sql: str) -> list[dict[str, Any]]:
                return [
                    {
                        "relation_id": r["id"], "type": r["type"], "record_id": r["other_id"],
                        "title": r["other_title"], "kind": r["other_kind"],
                        "status": m.effective_status(r["other_verification"], r["other_lifecycle"]),
                        "note": r["note"], "created_by": r["created_by"],
                    }
                    for r in c.execute(sql, (record_id,))
                ]

            rec["relations_out"] = rel_rows(
                """SELECT rel.*, o.id AS other_id, o.title AS other_title, o.kind AS other_kind,
                          o.verification AS other_verification, o.lifecycle AS other_lifecycle
                   FROM relations rel JOIN records o ON o.id = rel.to_id WHERE rel.from_id = ? ORDER BY rel.created_at"""
            )
            rec["relations_in"] = rel_rows(
                """SELECT rel.*, o.id AS other_id, o.title AS other_title, o.kind AS other_kind,
                          o.verification AS other_verification, o.lifecycle AS other_lifecycle
                   FROM relations rel JOIN records o ON o.id = rel.from_id WHERE rel.to_id = ? ORDER BY rel.created_at"""
            )
            rec["source"] = None
            if rec["source_id"]:
                s = c.execute("SELECT * FROM sources WHERE id = ?", (rec["source_id"],)).fetchone()
                if s:
                    src = dict(s)
                    src["content_length"] = len(src["content"])
                    if not include_source_content:
                        src.pop("content")
                    rec["source"] = src
            rec["history"] = [
                {**dict(h), "snapshot": json.loads(h["snapshot"])}
                for h in c.execute("SELECT * FROM record_history WHERE record_id = ? ORDER BY id", (record_id,))
            ]
        return rec

    def search(
        self,
        query: str = "",
        *,
        project: str | None = None,
        kinds: Iterable[str] | None = None,
        statuses: Iterable[str] | None = None,
        include_inactive: bool = False,
        include_sources: bool = True,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Keyword search (SQLite FTS5, BM25-ranked) over records and raw sources."""
        limit = max(1, min(int(limit), 100))
        kinds = [m.require_choice(k, m.RECORD_KINDS, "kind") for k in kinds or []]
        statuses = [m.require_choice(s, m.STATUSES, "status") for s in statuses or []]
        match = fts_query(query or "")
        pg_match = tsquery_terms(query or "", _STOPWORDS)
        with self._read() as c:
            where, params = [], []
            if project:
                where.append("r.project_id = ?")
                params.append(self._project(c, project)["id"])
            if kinds:
                where.append(f"r.kind IN ({','.join('?' * len(kinds))})")
                params += kinds
            if not include_inactive and not statuses:
                where.append("r.lifecycle = 'active'")
            if statuses:
                where.append(
                    "(CASE WHEN r.lifecycle = 'active' THEN r.verification ELSE r.lifecycle END) "
                    f"IN ({','.join('?' * len(statuses))})"
                )
                params += statuses
            if match and self.dialect == "pg":
                sql = (
                    "SELECT r.*, p.name AS project_name, -ts_rank(r.fts, q) AS score, "
                    "ts_headline('english', r.content, q, 'StartSel=[,StopSel=],MaxWords=16,MinWords=6') AS snippet "
                    "FROM records r JOIN projects p ON p.id = r.project_id, to_tsquery('english', ?) q "
                    "WHERE r.fts @@ q" + "".join(f" AND {w}" for w in where)
                    + " ORDER BY score, r.importance DESC LIMIT ?"
                )
                rows = c.execute(sql, (pg_match, *params, limit)).fetchall()
            elif match:
                sql = (
                    "SELECT r.*, p.name AS project_name, bm25(records_fts) AS score, "
                    "snippet(records_fts, 1, '[', ']', ' … ', 16) AS snippet "
                    "FROM records_fts JOIN records r ON r.rowid = records_fts.rowid "
                    "JOIN projects p ON p.id = r.project_id WHERE records_fts MATCH ? "
                    + "".join(f" AND {w}" for w in where)
                    + " ORDER BY score, r.importance DESC LIMIT ?"
                )
                rows = c.execute(sql, (match, *params, limit)).fetchall()
            else:
                sql = (
                    "SELECT r.*, p.name AS project_name, NULL AS score, substr(r.content, 1, 160) AS snippet "
                    "FROM records r JOIN projects p ON p.id = r.project_id"
                    + (" WHERE " + " AND ".join(where) if where else "")
                    + " ORDER BY r.updated_at DESC, r.rowid DESC LIMIT ?"
                )
                rows = c.execute(sql, (*params, limit)).fetchall()
            results = []
            for r in rows:
                d = _record_dict(r)
                results.append({
                    "id": d["id"], "project": d["project_name"], "project_id": d["project_id"],
                    "kind": d["kind"], "title": d["title"],
                    "status": d["status"], "importance": d["importance_label"], "origin_model": d["origin_model"],
                    "superseded_by": d["superseded_by"], "snippet": d["snippet"], "updated_at": d["updated_at"],
                })
            sources = []
            if include_sources and match:
                sparams: list[Any] = [pg_match if self.dialect == "pg" else match]
                swhere = ""
                if project:
                    swhere = " AND s.project_id = ?"
                    sparams.append(self._project(c, project)["id"])
                source_sql = (
                    "SELECT s.id, s.project_id, s.title, s.kind, s.origin_model, s.imported_at, "
                    "ts_headline('english', s.content, q, 'StartSel=[,StopSel=],MaxWords=16,MinWords=6') AS snippet "
                    "FROM sources s, to_tsquery('english', ?) q "
                    f"WHERE s.fts @@ q{swhere} ORDER BY ts_rank(s.fts, q) DESC LIMIT ?"
                ) if self.dialect == "pg" else (
                    "SELECT s.id, s.project_id, s.title, s.kind, s.origin_model, s.imported_at, "
                    "snippet(sources_fts, 1, '[', ']', ' … ', 16) AS snippet "
                    "FROM sources_fts JOIN sources s ON s.rowid = sources_fts.rowid "
                    f"WHERE sources_fts MATCH ?{swhere} ORDER BY bm25(sources_fts) LIMIT ?"
                )
                for s in c.execute(source_sql, (*sparams, min(limit, 10))):
                    sources.append(dict(s))
        return {"query": query, "results": results, "source_matches": sources}

    def delete_relation(self, relation_id: str) -> dict[str, Any]:
        """Remove one relation (human control). Undoing a supersession goes through update_record instead."""
        with self._write() as c:
            row = c.execute("SELECT * FROM relations WHERE id = ?", (relation_id,)).fetchone()
            if row is None:
                raise NotFound(f"No relation with id {relation_id!r}")
            if row["type"] == "supersedes":
                raise ValidationError("reactivate the superseded record instead of deleting its supersedes link")
            c.execute("DELETE FROM relations WHERE id = ?", (relation_id,))
        return {"deleted": relation_id}

    def revision(self) -> str:
        """Cheap fingerprint of the whole database; changes whenever any client writes."""
        with self._read() as c:
            row = c.execute(
                """SELECT (SELECT count(*) FROM projects), (SELECT max(updated_at) FROM projects),
                          (SELECT count(*) FROM records), (SELECT max(updated_at) FROM records),
                          (SELECT count(*) FROM relations), (SELECT count(*) FROM evidence),
                          (SELECT count(*) FROM sources), (SELECT coalesce(max(id), 0) FROM record_history)"""
            ).fetchone()
        return "|".join(str(row[i]) for i in range(8))

    def activity(self, project_ref: str | None = None, limit: int = 50, since: str | None = None) -> list[dict[str, Any]]:
        """Newest-first feed of what happened: records saved, changed, linked, evidenced; sources imported."""
        limit = max(1, min(int(limit), 500))
        with self._read() as c:
            pid = self._project(c, project_ref)["id"] if project_ref else None
            rows = c.execute(
                """SELECT * FROM (
                     SELECT r.created_at AS at, 'created' AS action, r.project_id, r.id AS record_id, r.kind,
                            r.title, coalesce(r.created_by, r.origin_model) AS actor, r.origin_model, NULL AS detail
                       FROM records r
                     UNION ALL
                     SELECT h.changed_at, CASE WHEN json_extract(h.snapshot, '$.lifecycle') = 'active'
                                                AND r.lifecycle = 'superseded' AND r.version = h.version + 1
                                               THEN 'superseded' ELSE 'updated' END,
                            r.project_id, r.id, r.kind, r.title, h.changed_by, r.origin_model, h.change_reason
                       FROM record_history h JOIN records r ON r.id = h.record_id
                     UNION ALL
                     SELECT e.added_at, 'evidence_added', r.project_id, r.id, r.kind, r.title, e.added_by,
                            r.origin_model, coalesce(e.citation, e.url, substr(e.excerpt, 1, 120))
                       FROM evidence e JOIN records r ON r.id = e.record_id
                     UNION ALL
                     SELECT rel.created_at, 'linked', rel.project_id, rel.from_id, r.kind, r.title, rel.created_by,
                            r.origin_model, rel.type || ' ' || t.title
                       FROM relations rel JOIN records r ON r.id = rel.from_id JOIN records t ON t.id = rel.to_id
                      WHERE rel.type <> 'supersedes'
                     UNION ALL
                     SELECT s.imported_at, 'source_imported', s.project_id, NULL, s.kind, s.title, s.origin_model,
                            s.origin_model, s.id
                       FROM sources s
                   ) a
                   WHERE (CAST(? AS TEXT) IS NULL OR a.project_id = ?) AND (CAST(? AS TEXT) IS NULL OR a.at > ?)
                   ORDER BY a.at DESC LIMIT ?""",
                (pid, pid, since, since, limit),
            ).fetchall()
            names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM projects")}
        return [{**dict(r), "project": names.get(r["project_id"])} for r in rows]

    def project_snapshot(self, project_ref: str) -> dict[str, Any]:
        """Everything about one project as plain dicts: input for context building and gap analysis."""
        with self._read() as c:
            p = dict(self._project(c, project_ref))
            records = {
                r["id"]: _record_dict(r)
                for r in c.execute("SELECT * FROM records WHERE project_id = ? ORDER BY rowid", (p["id"],))
            }
            evidence_counts = {
                r["record_id"]: r["n"]
                for r in c.execute(
                    "SELECT e.record_id, count(*) AS n FROM evidence e JOIN records r ON r.id = e.record_id "
                    "WHERE r.project_id = ? GROUP BY e.record_id",
                    (p["id"],),
                )
            }
            relations = [dict(r) for r in c.execute("SELECT * FROM relations WHERE project_id = ? ORDER BY rowid", (p["id"],))]
            sources = [
                dict(r)
                for r in c.execute(
                    "SELECT id, kind, title, url, origin_model, conversation_id, imported_at, length(content) AS content_length "
                    "FROM sources WHERE project_id = ? ORDER BY imported_at",
                    (p["id"],),
                )
            ]
        for rid, rec in records.items():
            rec["evidence_count"] = evidence_counts.get(rid, 0)
        return {"project": p, "records": records, "relations": relations, "sources": sources}

    def export(self, project_ref: str | None = None) -> dict[str, Any]:
        """Full JSON-serializable dump (all projects, or one) for backup and portability."""
        with self._read() as c:
            if project_ref:
                pids = [self._project(c, project_ref)["id"]]
            else:
                pids = [r["id"] for r in c.execute("SELECT id FROM projects")]
            out: dict[str, Any] = {"exported_at": now(), "schema_version": 1, "projects": []}
            for pid in pids:
                rec_ids = [r["id"] for r in c.execute("SELECT id FROM records WHERE project_id = ?", (pid,))]
                q = ",".join("?" * len(rec_ids))
                out["projects"].append({
                    "project": dict(self._project(c, pid)),
                    "sources": [dict(r) for r in c.execute("SELECT * FROM sources WHERE project_id = ?", (pid,))],
                    "records": [_record_dict(r) for r in c.execute("SELECT * FROM records WHERE project_id = ?", (pid,))],
                    "relations": [dict(r) for r in c.execute("SELECT * FROM relations WHERE project_id = ?", (pid,))],
                    "evidence": [dict(r) for r in c.execute(f"SELECT * FROM evidence WHERE record_id IN ({q})", rec_ids)]
                    if rec_ids else [],
                    "history": [dict(r) for r in c.execute(f"SELECT * FROM record_history WHERE record_id IN ({q})", rec_ids)]
                    if rec_ids else [],
                })
        return out
