import json
import os
import uuid
from pathlib import Path

import pytest

from nexus.importer import import_text
from nexus.store import MemoryStore

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
PROJECT = "Environmental AI Research"


@pytest.fixture
def anyio_backend():
    return "asyncio"


PG_URL = os.environ.get("NEXUS_TEST_PG_URL")  # e.g. postgresql://nexus@127.0.0.1:54329/nexus_test


@pytest.fixture(params=["sqlite", "pg"])
def db_path(request, tmp_path):
    """Every store test runs on SQLite, and on Postgres too when NEXUS_TEST_PG_URL is set."""
    if request.param == "sqlite":
        yield tmp_path / "memory.db"
        return
    if not PG_URL:
        pytest.skip("set NEXUS_TEST_PG_URL to run the Postgres variant")
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


# ----------------------------------------------------------------- hosted service helpers

import socket
import threading
import time

import httpx
import uvicorn

PASSWORD = "correct horse battery"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_server(app, port: int):
    """A real uvicorn server on a local port, in a thread. Returns a function that stops it."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)

    def stop():
        server.should_exit = True
        thread.join(5)
    return stop


def start_nexus(tmp_path, **settings):
    """The hosted service on a free port. Returns (base_url, accounts, stop)."""
    from nexus.config import Settings
    from nexus.hosted import create_hosted_app

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    location = settings.pop("location", str(tmp_path / "data"))
    cfg = Settings(public_url=base, location=location, **settings)
    app, accounts = create_hosted_app(location, base, settings=cfg)
    return base, accounts, run_server(app, port)


@pytest.fixture
def nexus(tmp_path):
    base, accounts, stop = start_nexus(tmp_path)
    yield base, accounts
    stop()


def browser(base: str) -> httpx.Client:
    """A cookie-keeping client that sends what the app's own pages send."""
    return httpx.Client(base_url=base, follow_redirects=False, headers={"X-Nexus": "1"}, timeout=20)


def sign_up(base: str, email: str, *, name: str = "Test Person", workspace: str = "", invitation: str = "",
            code: str = "") -> httpx.Client:
    b = browser(base)
    r = b.post("/signup", data={"name": name, "email": email, "password": PASSWORD, "workspace": workspace,
                                "invitation": invitation, "code": code})
    assert r.status_code == 303, r.text
    return b


def as_workspace(b: httpx.Client, slug: str) -> dict:
    return {"X-Nexus-Workspace": slug}
