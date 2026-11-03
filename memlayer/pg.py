"""Postgres backend: the same MemoryStore and AccountStore code running on Postgres instead of SQLite.

The stores talk to a small connection interface (``execute``, ``executescript``, rows that act like
``sqlite3.Row``). ``PgConnection`` implements it on psycopg and translates the few SQLite-isms the stores use.
Search is the one place the SQL differs by backend; ``MemoryStore.search`` branches on ``conn.dialect``.

Isolation: every account's memory lives in its own Postgres schema. Each transaction starts with
``SET LOCAL search_path`` for that schema, which also works through a transaction-mode connection pooler.
One connection per process is shared, so a process-wide lock keeps transactions from interleaving.
"""

from __future__ import annotations

import re
import threading
from functools import lru_cache
from importlib import resources
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 1
HIDDEN = {"fts", "seq"}  # backend columns that never leave the store
_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class Row(dict):
    """A dict that also answers ``row[0]``, like sqlite3.Row. Positions are kept separately, so unnamed columns
    that share a name (three ``count`` columns, say) are all still reachable by index."""

    def __init__(self, pairs: Iterable[tuple[str, Any]]):
        pairs = list(pairs)
        super().__init__(pairs)
        self._values = [v for _, v in pairs]

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


@lru_cache(maxsize=512)
def translate(sql: str) -> str:
    """Rewrite the SQLite dialect the stores use into Postgres."""
    sql = sql.replace("%", "%%")
    sql = re.sub(r"([\w.]+) = \? COLLATE NOCASE", r"lower(\1) = lower(?)", sql)
    sql = re.sub(r"json_extract\(([\w.]+), '\$\.(\w+)'\)", r"(\1::jsonb ->> '\2')", sql)
    sql = re.sub(r"\browid\b", "seq", sql)
    sql = sql.replace("?", "%s")
    if sql.lstrip().upper().startswith("INSERT OR IGNORE"):
        sql = re.sub(r"(?i)INSERT OR IGNORE", "INSERT", sql, count=1) + " ON CONFLICT DO NOTHING"
    return sql


class Cursor:
    def __init__(self, cur: Any):
        self._cur = cur
        self.rowcount = cur.rowcount

    def _rows(self) -> Iterator[Row]:
        if self._cur.description is None:
            return iter(())
        names = [d.name for d in self._cur.description]
        keep = [i for i, n in enumerate(names) if n not in HIDDEN]
        return (Row((names[i], r[i]) for i in keep) for r in self._cur.fetchall())

    def fetchone(self) -> Row | None:
        return next(self._rows(), None)

    def fetchall(self) -> list[Row]:
        return list(self._rows())

    def __iter__(self) -> Iterator[Row]:
        return self._rows()


class _Shared:
    """One psycopg connection per database URL per process, guarded by a lock."""

    _instances: dict[str, "_Shared"] = {}
    _guard = threading.Lock()

    def __init__(self, url: str):
        import psycopg

        # autocommit: the stores issue BEGIN/COMMIT themselves. prepare_threshold=None: no server-side
        # prepared statements, which transaction-mode poolers (Neon's pooled endpoint) cannot keep.
        self.conn = psycopg.connect(url, autocommit=True, prepare_threshold=None)
        self.lock = threading.RLock()
        self.ready_schemas: set[str] = set()

    @classmethod
    def get(cls, url: str) -> "_Shared":
        with cls._guard:
            inst = cls._instances.get(url)
            if inst is None or inst.conn.closed:
                inst = cls._instances[url] = cls(url)
            return inst


class PgConnection:
    """The connection interface the stores use, bound to one Postgres schema."""

    dialect = "pg"

    def __init__(self, url: str, schema: str, schema_sql: str | None = "schema_pg.sql"):
        if not _SCHEMA_NAME.match(schema):
            raise ValueError(f"invalid schema name {schema!r}")
        self.schema = schema
        self.shared = _Shared.get(url)
        self.lock = self.shared.lock
        self._in_tx = False
        if schema_sql and schema not in self.shared.ready_schemas:
            self._migrate(schema_sql)

    def _migrate(self, schema_sql: str) -> None:
        with self.lock:
            self.begin()
            try:
                self.shared.conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
                self.shared.conn.execute(f'SET LOCAL search_path TO "{self.schema}"')
                self.shared.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
                row = self.shared.conn.execute("SELECT max(version) FROM schema_version").fetchone()
                if (row[0] or 0) < SCHEMA_VERSION:
                    sql = resources.files("memlayer").joinpath(schema_sql).read_text(encoding="utf-8")
                    for stmt in _statements(sql):
                        self.shared.conn.execute(stmt)
                    self.shared.conn.execute("INSERT INTO schema_version VALUES (%s)", (SCHEMA_VERSION,))
                self.commit()
            except BaseException:
                self.rollback()
                raise
        self.shared.ready_schemas.add(self.schema)

    # transactions -----------------------------------------------------------------

    def begin(self, read_only: bool = False) -> None:
        self.shared.conn.execute("BEGIN READ ONLY" if read_only else "BEGIN")
        self.shared.conn.execute(f'SET LOCAL search_path TO "{self.schema}"')
        self._in_tx = True

    def commit(self) -> None:
        self.shared.conn.execute("COMMIT")
        self._in_tx = False

    def rollback(self) -> None:
        self.shared.conn.execute("ROLLBACK")
        self._in_tx = False

    # statements ---------------------------------------------------------------------

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Cursor:
        if not self._in_tx:  # a lone statement still runs in this schema
            with self.lock:
                self.begin()
                try:
                    cur = Cursor(self.shared.conn.execute(translate(sql), tuple(params)))
                    rows = cur.fetchall()
                    self.commit()
                except BaseException:
                    self.rollback()
                    raise
            return _Materialized(rows, cur.rowcount)
        return Cursor(self.shared.conn.execute(translate(sql), tuple(params)))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        for params in rows:
            self.execute(sql, params)

    def close(self) -> None:  # the shared connection outlives any one store
        pass


class _Materialized:
    def __init__(self, rows: list[Row], rowcount: int):
        self._rows, self.rowcount = rows, rowcount

    def fetchone(self) -> Row | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Row]:
        return list(self._rows)

    def __iter__(self) -> Iterator[Row]:
        return iter(self._rows)


def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def tsquery_terms(text: str, stopwords: frozenset[str]) -> str:
    """Free text to a safe OR tsquery string: word characters only, joined with |."""
    seen: list[str] = []
    for t in re.findall(r"\w+", text.lower()):
        if t not in stopwords and t not in seen:
            seen.append(t)
    return " | ".join(f"'{t}'" for t in seen[:32])


def is_pg_url(value: str) -> bool:
    return value.startswith(("postgres://", "postgresql://"))
