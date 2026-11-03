"""SQLite connection setup. Everything SQLite-specific outside store.py lives here."""

from __future__ import annotations

import os
import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 1


def default_db_path() -> Path:
    """MEMLAYER_DB if set, otherwise ~/.memlayer/memory.db (local by default)."""
    env = os.environ.get("MEMLAYER_DB")
    return Path(env).expanduser() if env else Path.home() / ".memlayer" / "memory.db"


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
    schema = resources.files("memlayer").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
