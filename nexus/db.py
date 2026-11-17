"""SQLite connection setup. Everything SQLite-specific outside store.py lives here."""

from __future__ import annotations

import os
import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 2

# Changes applied to a store created at an earlier version, keyed by the version they bring it to.
UPGRADES = {
    2: ["ALTER TABLE records ADD COLUMN confirmed_by TEXT", "ALTER TABLE records ADD COLUMN confirmed_at TEXT"],
}


def default_db_path() -> Path:
    """NEXUS_DB if set, otherwise ~/.nexus/memory.db (local by default)."""
    env = os.environ.get("NEXUS_DB") or os.environ.get("MEMLAYER_DB")
    if env:
        return Path(env).expanduser()
    current, legacy = Path.home() / ".nexus" / "memory.db", Path.home() / ".memlayer" / "memory.db"
    # Installations from before the rename keep using their existing file until it is moved by hand.
    return legacy if legacy.exists() and not current.exists() else current


def connect(path: str | os.PathLike) -> sqlite3.Connection:
    path = str(path)
    if path != ":memory:":
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: the store issues BEGIN/COMMIT itself.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    if current == 0:  # a new file gets the current schema in one step
        conn.executescript(resources.files("nexus").joinpath("schema.sql").read_text(encoding="utf-8"))
    else:
        conn.execute("BEGIN IMMEDIATE")
        for version in range(current + 1, SCHEMA_VERSION + 1):
            for statement in UPGRADES[version]:
                conn.execute(statement)
        conn.execute("COMMIT")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
