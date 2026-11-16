"""The REST API that programs use with a NEXUS API key, and the limits a workspace's plan sets."""

import httpx
import pytest

from nexus.demo import PROJECT
from tests.conftest import sign_up


@pytest.fixture
def api(nexus):
    base, accounts = nexus
    owner = sign_up(base, "olga@example.com", workspace="Acme")
    owner.post("/api/workspace/demo", json={})
    editor = owner.post("/api/workspace/keys", json={"name": "agent", "role": "editor"}).json()["secret"]
    viewer = owner.post("/api/workspace/keys", json={"name": "dashboard", "role": "viewer"}).json()["secret"]
    other = sign_up(base, "zed@example.com", workspace="Rival")
    rival = other.post("/api/workspace/keys", json={"name": "rival", "role": "editor"}).json()["secret"]

    def client(secret):
        return httpx.Client(base_url=base, headers={"Authorization": f"Bearer {secret}"}, timeout=20)

    clients = {"editor": client(editor), "viewer": client(viewer), "rival": client(rival)}
    yield {"base": base, "accounts": accounts, "owner": owner, "other": other, **clients}
    for c in (*clients.values(), owner, other):
        c.close()


def test_a_program_reads_context_checks_a_plan_and_writes_back(api):
    e = api["editor"]
    projects = e.get("/v1/projects").json()["projects"]
    assert [p["name"] for p in projects] == [PROJECT]
    pid = projects[0]["id"]
    ctx = e.get(f"/v1/projects/{pid}/context", params={"q": "webhooks"}).json()
    assert ctx["filtered_by_query"] and any("webhook" in d["title"].lower() for d in ctx["assumptions"])
    brief = e.get(f"/v1/projects/{pid}/briefing", params={"budget": 500, "policy": "confirmed"}).json()
    assert brief["text"].startswith("<nexus_project_context") and brief["token_estimate"] <= 500
    receipt = e.get(f"/v1/receipts/{brief['receipt_id']}").json()
    assert receipt["channel"] == "api" and receipt["actor"] == "key:agent"
    assert len(receipt["record_refs"]) == len(brief["record_refs"])
    assert "receipt_id" not in e.get(f"/v1/projects/{pid}/briefing", params={"receipt": "0"}).json()
    check = e.post(f"/v1/projects/{pid}/check", json={"statements": ["Use Jest for the new unit tests"]}).json()
    assert check["verdict"] == "resembles_replaced"
    one = e.post(f"/v1/projects/{pid}/check", json={"statement": "Rename a variable"}).json()
    assert one["verdict"] == "clear"
    saved = e.post(f"/v1/projects/{pid}/records", json={
        "kind": "decision", "title": "Refunds are issued by the ledger service", "origin_model": "codex",
        "evidence": [{"url": "https://example.com/acme/adr/12", "citation": "ADR 12"}]})
    assert saved.status_code == 201 and saved.json()["created_by"] == "key:agent"
    assert saved.json()["status"] == "unverified" and saved.json()["confirmed_by"] is None  # a program's claim starts unverified
    rid = saved.json()["id"]
    assert e.get("/v1/search", params={"q": "ledger service"}).json()["results"][0]["id"] == rid
    assert e.get(f"/v1/projects/{pid}/gaps").json()["counts"]["possible_contradiction"] == 1
    assert e.get(f"/v1/projects/{pid}/export").json()["projects"][0]["project"]["name"] == PROJECT
    for bad in ({"budget": "lots"}, {"policy": "trusted"}):
        assert e.get(f"/v1/projects/{pid}/briefing", params=bad).status_code == 400
    assert e.post(f"/v1/projects/{pid}/check", json={"statements": "not a list"}).status_code == 400
    usage = api["owner"].get("/api/workspace/usage").json()
    assert sum(d["calls"] for d in usage["by_day"] if d["channel"] == "api") >= 10
    assert {"channel": "api", "actor": "key:agent"}.items() <= usage["by_actor"][0].items()


def test_viewer_key_reads_and_cannot_write(api):
    v, pid = api["viewer"], api["editor"].get("/v1/projects").json()["projects"][0]["id"]
    assert v.get(f"/v1/projects/{pid}").status_code == 200
    assert v.post(f"/v1/projects/{pid}/check", json={"statements": ["Use Jest"]}).status_code == 200  # a read
    rid = v.get(f"/v1/projects/{pid}/records").json()["records"][0]["id"]
    for r in (v.post("/v1/projects", json={"name": "x"}),
              v.post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "x"}),
              v.patch(f"/v1/records/{rid}", json={"reason": "x", "title": "y"}),
              v.delete(f"/v1/records/{rid}"), v.delete(f"/v1/projects/{pid}"),
              v.post(f"/v1/projects/{pid}/import", json={"text": "## Notes\n- [N1] x"})):
        assert r.status_code == 403, r.text
    assert api["editor"].delete(f"/v1/projects/{pid}").status_code == 403  # deleting a project needs an admin


def test_one_workspace_cannot_reach_another_through_any_route(api):
    pid = api["editor"].get("/v1/projects").json()["projects"][0]["id"]
    records = api["editor"].get(f"/v1/projects/{pid}/records").json()["records"]
    rid = records[0]["id"]
    source = api["editor"].get(f"/v1/projects/{pid}/sources").json()["sources"][0]["id"]
    brief = api["editor"].get(f"/v1/projects/{pid}/briefing").json()["receipt_id"]
    r = api["rival"]
    assert r.get("/v1/projects").json()["projects"] == []
    assert r.get("/v1/search", params={"q": "Vitest"}).json()["results"] == []
    assert r.get("/v1/activity").json()["activity"] == []
    for path in (f"/v1/projects/{pid}", f"/v1/projects/{pid}/records", f"/v1/projects/{pid}/graph",
                 f"/v1/projects/{pid}/gaps", f"/v1/projects/{pid}/context", f"/v1/projects/{pid}/briefing",
                 f"/v1/projects/{pid}/export", f"/v1/projects/{PROJECT}", f"/v1/records/{rid}",
                 f"/v1/sources/{source}", f"/v1/receipts/{brief}"):
        assert r.get(path).status_code == 404, path
    for call in (lambda: r.patch(f"/v1/records/{rid}", json={"reason": "x", "title": "pwned"}),
                 lambda: r.delete(f"/v1/records/{rid}"),
                 lambda: r.post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "x"}),
                 lambda: r.post(f"/v1/projects/{pid}/check", json={"statements": ["x y z"]}),
                 lambda: r.post("/v1/chat/completions", headers={"x-nexus-project": pid},
                                json={"model": "mock-1", "messages": [{"role": "user", "content": "hi"}]})):
        assert call().status_code == 404
    # The rival's own records cannot link to, or supersede, a record in the other workspace.
    own = r.post("/v1/projects", json={"name": "Rival project"}).json()["id"]
    linked = r.post(f"/v1/projects/{own}/records", json={"kind": "note", "title": "x",
                                                         "relations": [{"type": "supersedes", "target_id": rid}]})
    assert linked.status_code == 404
    assert api["editor"].get(f"/v1/records/{rid}").json()["title"] == records[0]["title"]
    # The signed-in app is fenced the same way, even when it names the other workspace.
    assert api["other"].get(f"/api/records/{rid}").status_code == 404
    assert api["other"].get("/api/projects", headers={"X-Nexus-Workspace": "acme"}).status_code == 403
    assert api["other"].get(f"/api/workspace/receipts/{brief}").status_code == 404


def test_plan_limits_apply_to_every_way_of_writing(api):
    accounts, ws = api["accounts"], api["accounts"].get_workspace("acme")
    pid = api["editor"].get("/v1/projects").json()["projects"][0]["id"]
    accounts.set_plan(ws["id"], "free", {"projects": 1, "records": 19})
    over = api["editor"].post("/v1/projects", json={"name": "Second"})
    assert over.status_code == 402 and over.json()["code"] == "limit_reached" and "1 projects" in over.json()["error"]
    assert api["owner"].post("/api/projects", json={"name": "Second"}).status_code == 402
    assert api["owner"].post("/api/workspace/demo", json={}).status_code == 402
    assert api["editor"].post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "the 19th"}).status_code == 201
    assert api["editor"].post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "the 20th"}).status_code == 402
    assert api["owner"].post(f"/api/projects/{pid}/import", json={"text": "## Notes\n- [N1] x"}).status_code == 402
    # Reading and correcting existing records still works at the limit.
    rid = api["editor"].get(f"/v1/projects/{pid}/records").json()["records"][0]["id"]
    assert api["editor"].patch(f"/v1/records/{rid}", json={"reason": "typo", "content": "fixed"}).status_code == 200


def test_cli_brief_and_check_use_the_same_api(api, capsys, monkeypatch):
    """`nexus brief` prints the block at the start of a coding-agent session; `nexus check` gates a plan in CI."""
    from nexus.cli import main

    key = api["editor"].headers["Authorization"].split()[1]
    monkeypatch.setenv("NEXUS_URL", api["base"])
    monkeypatch.setenv("NEXUS_API_KEY", key)
    main(["brief", PROJECT, "--budget", "600"])
    out = capsys.readouterr().out
    assert out.startswith("<nexus_project_context") and "Use Vitest for unit tests" in out
    with pytest.raises(SystemExit) as stop:
        main(["check", PROJECT, "Add Jest tests for the cart", "Rename a variable"])
    assert stop.value.code == 2  # a non-zero exit fails a CI step
    report = capsys.readouterr().out
    assert "resembles a replaced record" in report and "Use Vitest for unit tests" in report
    main(["check", PROJECT, "Rename a variable"])
    assert "clear" in capsys.readouterr().out
    monkeypatch.setenv("NEXUS_API_KEY", "nxk_wrong")
    with pytest.raises(SystemExit) as stop:
        main(["brief", PROJECT])
    assert stop.value.code == 1 and "API key" in capsys.readouterr().err
