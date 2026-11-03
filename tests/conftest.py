from pathlib import Path

import pytest

from memlayer.importer import import_text
from memlayer.store import MemoryStore

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
PROJECT = "Environmental AI Research"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "memory.db"


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
    return list(store.conn.iterdump())
