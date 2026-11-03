"""Web dashboard API: security guards, CRUD through the dashboard, and change detection for live updates."""

import pytest
from starlette.testclient import TestClient

from memlayer.importer import import_text
from memlayer.store import MemoryStore
from memlayer.web import create_app

from .conftest import PROJECT, SAMPLES

W = {"X-MemLayer": "1"}


@pytest.fixture
def client(store, imported):
    with TestClient(create_app(store.path, store=store), base_url="http://127.0.0.1:8765") as c:
        yield c


@pytest.fixture
def pid(store, imported):
    return store.get_project(PROJECT)["id"]


def test_rejects_foreign_host_and_unsigned_writes(client, pid):
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/projects", json={"name": "x"}).status_code == 403  # no X-MemLayer header
    assert client.delete(f"/api/projects/{pid}").status_code == 403
    assert client.get("/api/projects").status_code == 200


def test_index_uses_brand_and_static_is_not_cached(client):
    page = client.get("/")
    assert page.status_code == 200 and "<title>MemLayer</title>" in page.text
    js = client.get("/static/js/main.js")
    assert js.status_code == 200 and js.headers["cache-control"] == "no-cache"


def test_project_overview(client, pid, imported):
    d = client.get(f"/api/projects/{pid}").json()
    assert d["project"]["name"] == PROJECT
    assert d["status_counts"]["evidence_supported"] == 3
    assert d["model_counts"] == {"chatgpt": len(imported)}
    assert any(g["type"] == "possible_contradiction" for g in d["gaps"])
    assert all(g["records"] for g in d["gaps"])  # titles available for display, not just ids
    assert d["activity"]


def test_records_filters(client, pid, imported):
    base = f"/api/projects/{pid}/records"
    assert len(client.get(base).json()["records"]) == len(imported)
    decisions = client.get(base, params={"kind": "decision"}).json()["records"]
    assert {r["kind"] for r in decisions} == {"decision"} and len(decisions) == 4
    supported = client.get(base, params={"status": "evidence_supported"}).json()["records"]
    assert {r["id"] for r in supported} == {imported["F1"], imported["F2"], imported["F3"]}
    hits = client.get(base, params={"q": "revisit"}).json()["records"]
    assert {r["id"] for r in hits} == {imported["F2"], imported["F6"]}


def test_dashboard_edit_flow(client, pid, imported):
    rid = imported["F4"]
    bad = client.patch(f"/api/records/{rid}", json={"reason": "x", "verification": "evidence_supported"}, headers=W)
    assert bad.status_code == 400 and "evidence" in bad.json()["error"]
    ok = client.patch(f"/api/records/{rid}", headers=W, json={
        "reason": "Benchmarked", "verification": "evidence_supported",
        "add_evidence": [{"url": "https://example.org/bench", "checked_at": "2026-11-02"}],
    }).json()
    assert ok["status"] == "evidence_supported" and ok["history"][0]["changed_by"] == "dashboard"
    assert client.patch(f"/api/records/{rid}", json={"title": "no reason"}, headers=W).status_code == 400

    new = client.post(f"/api/projects/{pid}/records", headers=W, json={
        "kind": "decision", "title": "Use dry-season composites", "origin_model": "human",
        "relations": [{"type": "supersedes", "target_id": imported["D3"]}],
    })
    assert new.status_code == 201
    old = client.get(f"/api/records/{imported['D3']}").json()
    assert old["status"] == "superseded" and old["superseded_by"] == new.json()["id"]

    rel = next(r for r in client.get(f"/api/records/{imported['D2']}").json()["relations_out"] if r["type"] == "depends_on")
    assert client.delete(f"/api/relations/{rel['relation_id']}", headers=W).status_code == 200
    assert client.delete(f"/api/records/{imported['Q1']}", headers=W).status_code == 200
    assert client.get(f"/api/records/{imported['Q1']}").status_code == 404


def test_graph_gaps_sources_search(client, pid, imported):
    g = client.get(f"/api/projects/{pid}/graph").json()
    ids = {n["id"] for n in g["nodes"]}
    assert len(g["nodes"]) == len(imported) and all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    assert client.get(f"/api/projects/{pid}/gaps").json()["counts"]["open_question"] == 2
    sources = client.get(f"/api/projects/{pid}/sources").json()["sources"]
    assert len(sources) == 2 and sum(s["record_count"] for s in sources) == len(imported)
    src = client.get(f"/api/sources/{sources[-1]['id']}").json()
    assert "Sentinel-2 provides free multispectral imagery" in src["content"] and src["records"]
    found = client.get("/api/search", params={"q": "wetland"}).json()
    assert found["results"] and found["results"][0]["project_id"] == pid and found["source_matches"]
    assert client.get("/api/search", params={"q": "environmental"}).json()["projects"][0]["id"] == pid


def test_import_and_create_project(client):
    p = client.post("/api/projects", json={"name": "Second project", "objective": "Test"}, headers=W)
    assert p.status_code == 201
    text = (SAMPLES / "followup_notes.md").read_text()
    res = client.post(f"/api/projects/{p.json()['id']}/import", json={"text": text, "origin_model": "chatgpt"}, headers=W).json()
    assert res["source"]["created"] and len(res["records_created"]) == 3
    assert client.post("/api/projects", json={"name": "Second project"}, headers=W).status_code == 400


def test_external_write_changes_revision_and_activity(store, imported, db_path):
    """What the live stream relies on: a write from another process (e.g. the MCP server) is visible."""
    before = store.revision()
    last = store.activity(limit=1)[0]["at"]
    other = MemoryStore(db_path)  # separate connection, like the MCP server process
    other.save_record(PROJECT, kind="finding", title="Saved by another client", origin_model="claude",
                      created_by="mcp:claude")
    other.close()
    assert store.revision() != before
    feed = store.activity(PROJECT, limit=5)
    assert feed[0]["title"] == "Saved by another client" and feed[0]["actor"] == "mcp:claude"
    assert store.activity(since="1970-01-01T00:00:00+00:00", limit=500)  # since-filter works
    assert all(a["at"] >= last for a in store.activity(since=last))


def test_brand_is_configurable(monkeypatch, store, imported):
    from memlayer import brand
    monkeypatch.setitem(brand.BRAND, "name", "Cortex")
    with TestClient(create_app(store.path, store=store), base_url="http://localhost:8765") as c:
        assert "<title>Cortex</title>" in c.get("/").text
        assert c.get("/api/meta").json()["brand"]["name"] == "Cortex"
