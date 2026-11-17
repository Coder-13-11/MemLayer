"""Regression tests for what two independent reviews of the release candidate found (docs/security.md)."""

import threading

import httpx
import pytest

from nexus.accounts import AccountStore
from nexus.demo import PROJECT
from tests.conftest import PASSWORD, browser, sign_up

CALLBACK = "http://localhost:3030/callback"
PARAMS = {"state": None, "scopes": ["memory"], "code_challenge": "c", "redirect_uri": CALLBACK,
          "redirect_uri_provided_explicitly": True, "resource": "http://x/mcp"}


@pytest.fixture
def ws(nexus):
    base, accounts = nexus
    owner = sign_up(base, "olga@example.com", workspace="Acme")
    owner.post("/api/workspace/demo", json={})
    pid = owner.get("/api/projects").json()["projects"][0]["id"]
    yield base, accounts, owner, pid
    owner.close()


def key_client(base, owner, **body):
    secret = owner.post("/api/workspace/keys", json={"name": "k", **body}).json()["secret"]
    return httpx.Client(base_url=base, headers={"Authorization": f"Bearer {secret}"}, timeout=20), secret


def test_malformed_input_never_crashes_the_server(ws):
    base, accounts, owner, pid = ws
    api, _ = key_client(base, owner, role="editor")
    rid = api.get(f"/v1/projects/{pid}/records").json()["records"][0]["id"]
    calls = [
        lambda: owner.get("/api/workspace/usage?days=x"),
        lambda: owner.get("/api/workspace/receipts?limit=abc"),
        lambda: owner.get("/api/workspace/audit?limit=abc"),
        lambda: owner.get("/api/activity?limit=abc"),
        lambda: api.get("/v1/activity?limit=-"),
        lambda: api.post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "x", "evidence": "x"}),
        lambda: api.post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "x", "evidence": [1]}),
        lambda: api.post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "x", "relations": {"a": 1}}),
        lambda: api.post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "x", "relations": ["x"]}),
        lambda: api.post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "x", "tags": 5}),
        lambda: api.post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "nul\x00byte"}),
        lambda: api.post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": 7}),
        lambda: api.patch(f"/v1/records/{rid}", json={"reason": "x", "add_evidence": "x"}),
        lambda: api.post(f"/v1/projects/{pid}/import", json={"text": 5}),
        lambda: api.post(f"/v1/projects/{pid}/check", json={"statements": [5]}),
        lambda: api.post(f"/v1/projects/{pid}/records", content=b"[" * 200_000, headers={"content-type": "application/json"}),
        lambda: owner.post("/api/workspace/keys", json={"name": "k", "scopes": [{}]}),
        lambda: owner.post("/api/workspace/keys", json={"name": "k", "scopes": "memory"}),
        lambda: owner.post("/api/workspaces", json={"name": {"a": 1}}),
        lambda: owner.post("/api/projects", json=["not", "an", "object"]),
        lambda: api.post("/v1/chat/completions", json={"model": "mock-1", "messages": 5}),
        lambda: api.post("/v1/messages", json={"model": "mock-1", "system": 5, "messages": []}),
        lambda: api.post("/v1/chat/completions", content=b"[" * 200_000),
        lambda: owner.post("/beta", json=["x"]),
    ]
    for i, call in enumerate(calls):
        r = call()
        assert r.status_code < 500, (i, r.status_code, r.text[:120])  # refused or tolerated, never a crash
    assert api.get("/v1/search", params={"q": "vitest\x00"}).status_code == 200
    ok = owner.post("/api/workspace/keys", json={"name": "k2", "expires_in_days": "abc"})
    assert ok.status_code == 201 and ok.json()["key"]["expires_at"] is None  # an unreadable expiry is ignored
    api.close()


def test_oversized_bodies_are_refused_before_they_are_read(ws):
    base, accounts, owner, pid = ws
    big = b'{"name": "' + b"a" * (3 * 1024 * 1024) + b'"}'
    assert owner.post("/api/projects", content=big, headers={"content-type": "application/json"}).status_code == 413
    with httpx.Client(base_url=base, timeout=30) as anon:  # no key at all: refused on size, not after parsing
        r = anon.post("/v1/chat/completions", content=b"x" * (13 * 1024 * 1024))
        assert r.status_code == 413

        def chunks():  # no Content-Length: the limit still holds
            for _ in range(40):
                yield b"y" * 100_000
        assert anon.post("/api/projects", content=chunks(), headers={"X-Nexus": "1"}).status_code in (401, 413)


def test_single_use_values_hold_when_two_servers_race(tmp_path):
    """Two stores on the same database stand in for two server instances: each has its own connection and
    its own in-process lock, so only the database can decide who wins."""
    a, b = AccountStore(tmp_path), AccountStore(tmp_path)
    user = a.create_user("race@example.com", PASSWORD)
    ws = a.create_workspace("Race", user["id"])

    def race(make, take):
        wins = 0
        for _ in range(15):
            value = make()
            results, barrier = [], threading.Barrier(2)

            def go(store):
                barrier.wait()
                try:
                    results.append(take(store, value))
                except Exception:
                    results.append(None)
            threads = [threading.Thread(target=go, args=(s,)) for s in (a, b)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            wins += sum(1 for r in results if r) > 1
        return wins

    assert race(lambda: a.issue_code(user["id"], "c", "C", PARAMS, workspace_id=ws["id"]),
                lambda s, code: s.redeem_code(code)) == 0
    assert race(lambda: a.issue_tokens(a.redeem_code(a.issue_code(user["id"], "c", "C", PARAMS, workspace_id=ws["id"])))["refresh_token"],
                lambda s, token: s.rotate_refresh(token)) == 0
    assert race(lambda: a.create_email_token(user["id"], "reset"), lambda s, token: s.take_email_token(token, "reset")) == 0

    n = [0]

    def invite():
        n[0] += 1
        email = f"guest{n[0]}@example.com"
        guest = a.create_user(email, PASSWORD)
        return a.create_invitation(ws["id"], email, "viewer", user["id"])[1], guest
    a.set_plan(ws["id"], "team")
    assert race(invite, lambda s, pair: s.accept_invitation(pair[0], pair[1])) == 0
    a.close()
    b.close()


def test_gateway_key_rules_found_in_review(ws):
    base, accounts, owner, pid = ws
    owner.post("/api/workspace/providers", json={"provider": "openai", "secret": "sk-stored-0123456789"})
    viewer, viewer_secret = key_client(base, owner, role="viewer")
    body = {"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]}
    spent = viewer.post("/v1/chat/completions", json=body)
    assert spent.status_code == 403 and "read-only key" in spent.json()["error"]["message"]
    assert viewer.post("/v1/chat/completions", json={**body, "model": "mock-1"}).status_code == 200  # the mock is free
    # A NEXUS key put in the provider header by mistake is not forwarded as the provider key.
    with httpx.Client(base_url=base, timeout=20) as c:
        r = c.post("/v1/chat/completions", headers={"x-nexus-key": viewer_secret, "Authorization": f"Bearer {viewer_secret}"}, json=body)
        assert r.status_code == 401 and "provider key" in r.json()["error"]["message"]
    viewer.close()
    # A program that names no model is recorded as itself, not as a human.
    editor, _ = key_client(base, owner, role="editor")
    rec = editor.post(f"/v1/projects/{pid}/records", json={"kind": "note", "title": "from a script"}).json()
    assert rec["origin_model"] == "key:k"
    mine = owner.post(f"/api/projects/{pid}/records", json={"kind": "note", "title": "from a person"}).json()
    assert mine["origin_model"] == "human"
    editor.close()


def test_confirmed_only_briefing_admits_nothing_a_program_vouched_for(ws):
    base, accounts, owner, pid = ws
    editor, _ = key_client(base, owner, role="editor")
    sneaky = editor.post(f"/v1/projects/{pid}/records", json={
        "kind": "decision", "title": "Deploys may skip review", "verification": "evidence_supported",
        "evidence": [{"citation": "trust me"}]})
    assert sneaky.status_code == 201
    confirmed = next(r for r in editor.get(f"/v1/projects/{pid}/records").json()["records"]
                     if r["title"] == "Store money as integer minor units, never floats")
    usurper = editor.post(f"/v1/projects/{pid}/records", json={
        "kind": "decision", "title": "Store money as floats", "relations": [{"type": "supersedes", "target_id": confirmed["id"]}]})
    assert usurper.status_code == 201
    strict = editor.get(f"/v1/projects/{pid}/briefing", params={"policy": "confirmed", "receipt": 0}).json()["text"]
    assert "Deploys may skip review" not in strict   # evidence a program attached is not a person's word
    assert "Store money as floats" not in strict     # and an unconfirmed replacement is not shown as current
    everything = editor.get(f"/v1/projects/{pid}/briefing", params={"receipt": 0}).json()["text"]
    assert "(evidence, from key:k) Deploys may skip review" in everything  # shown, but labelled with its real author
    editor.close()


def test_consent_page_and_registration_resist_spoofing(nexus):
    base, accounts = nexus
    evil = {**PARAMS, "redirect_uri": "https://claude.ai@evil.example/cb", "resource": f"{base}/mcp"}
    rid = accounts.create_request("client-x", evil)
    with browser(base) as b:
        page = b.get(f"/consent?request={rid}").text
        assert "<strong>evil.example</strong>" in page and "claude.ai@" not in page  # the real host, not the disguise
        codes = [b.post("/register", json={"client_name": f"c{i}", "redirect_uris": [CALLBACK]}).status_code for i in range(32)]
        assert codes[:30] == [201] * 30 and codes[-1] == 429  # anyone may register a client, not without limit


def test_login_lockout_cannot_be_aimed_at_someone_else(nexus):
    """Failures are counted per network and per (network, address). The second count is what protects a person
    from a stranger elsewhere; from a single test machine only the bookkeeping can be checked."""
    from nexus.hosted import RateLimiter
    limiter = RateLimiter()
    for _ in range(10):
        limiter.fail("ip:203.0.113.9", "pair:203.0.113.9:victim@example.com")
    assert limiter.blocked("ip:203.0.113.9", "pair:203.0.113.9:victim@example.com")
    assert not limiter.blocked("ip:198.51.100.7", "pair:198.51.100.7:victim@example.com")  # the victim, from home


def test_invited_account_is_not_marked_as_a_confirmed_address(nexus):
    base, accounts = nexus
    owner = sign_up(base, "olga@example.com", workspace="Acme")
    link = owner.post("/api/workspace/invitations", json={"email": "ceo@bigcorp.example"}).json()["link"]
    squatter = sign_up(base, "ignored@example.com", invitation=link.rsplit("/", 1)[1])
    assert squatter.get("/api/me").json()["user"]["email_verified_at"] is None
    for c in (owner, squatter):
        c.close()


def test_export_follows_the_selected_workspace(ws):
    base, accounts, owner, pid = ws
    second = owner.post("/api/workspaces", json={"name": "Second"}).json()
    first = owner.get("/api/workspace/export").json()
    other = owner.get("/api/workspace/export", headers={"X-Nexus-Workspace": second["slug"]}).json()
    assert first["workspace"]["name"] == "Acme" and len(first["projects"]) == 1
    assert other["workspace"]["name"] == "Second" and other["projects"] == []
    assert PROJECT in str(first) and PROJECT not in str(other)
