"""Numbered migrations for the control plane (accounts, workspaces, keys, metering).

Files in ``nexus/migrations`` are named ``NNNN_name.sql`` and applied in order, each in its own transaction, and
recorded in ``schema_migrations``. They are written in the SQL subset SQLite and Postgres share, so one set of
files serves both. A migration that has been released is never edited; changes go in a new file.

Workspace memory stores migrate themselves when opened (``db.py`` and ``pg.py``), because there is one per
workspace and they are created on demand.
"""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

from .pg import _statements

_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_ADVISORY_LOCK = 7274_2026  # any fixed number: serializes concurrent instances migrating the same database


def available() -> list[tuple[int, str, str]]:
    """(version, name, sql) for every migration file, in order."""
    out = []
    for entry in resources.files("nexus").joinpath("migrations").iterdir():
        m = _NAME.match(entry.name)
        if m:
            out.append((int(m.group(1)), m.group(2), entry.read_text(encoding="utf-8")))
    return sorted(out)


def apply(store: Any) -> list[str]:
    """Bring the control plane up to date. Returns the names of the migrations that ran."""
    from .store import now

    ran: list[str] = []
    with store._tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                  "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
    for version, name, sql in available():
        with store._tx() as c:
            if store.dialect == "pg":
                c.execute(f"SELECT pg_advisory_xact_lock({_ADVISORY_LOCK})")
            if c.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone():
                continue
            for statement in _statements(sql):
                c.execute(statement)
            c.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                      (version, name, now()))
            ran.append(f"{version:04d}_{name}")
    return ran


def status(store: Any) -> dict[str, Any]:
    with store._lock:
        applied = {r["version"]: r["applied_at"] for r in
                   store.conn.execute("SELECT version, applied_at FROM schema_migrations")}
    return {"applied": [{"version": v, "name": n, "applied_at": applied[v]} for v, n, _ in available() if v in applied],
            "pending": [f"{v:04d}_{n}" for v, n, _ in available() if v not in applied]}
