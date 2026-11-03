import json
import os
import uuid
from pathlib import Path

import pytest

from memlayer.importer import import_text
from memlayer.store import MemoryStore

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
PROJECT = "Environmental AI Research"


@pytest.fixture
def anyio_backend():
    return "asyncio"


PG_URL = os.environ.get("MEMLAYER_TEST_PG_URL")  # e.g. postgresql://memlayer@127.0.0.1:54329/memlayer_test


@pytest.fixture(params=["sqlite", "pg"])
def db_path(request, tmp_path):
    """Every store test runs on SQLite, and on Postgres too when MEMLAYER_TEST_PG_URL is set."""
    if request.param == "sqlite":
        yield tmp_path / "memory.db"
        return
    if not PG_URL:
        pytest.skip("set MEMLAYER_TEST_PG_URL to run the Postgres variant")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    yield f"{PG_URL}#{schema}"
    import psycopg
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.fixture
def store(db_path):
    s = MemoryStore(db_path)
    yield s
    s.close()


@pytest.fixture
def imported(store):
    """Store with both synthetic sample sessions imported; returns {key: record_id}."""
    keys = {}
    for name in ("chatgpt_feasibility_session.md", "followup_notes.md"):
        result = import_text(store, (SAMPLES / name).read_text(encoding="utf-8"))
        keys.update({r["key"]: r["id"] for r in result["records_created"]})
    return keys


def dump(store: MemoryStore) -> list[str]:
    """Everything stored, for "this read changed nothing" checks."""
    if store.dialect == "sqlite":
        return list(store.conn.iterdump())
    data = store.export()
    data.pop("exported_at")
    return [json.dumps(data, sort_keys=True, default=str), store.revision()]
