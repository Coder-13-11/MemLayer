"""Hosted service: OAuth sign-in as a real MCP client performs it, and per-user isolation.

A real uvicorn server runs on a free local port; the MCP SDK's own OAuth client
registers itself, follows the sign-in redirect, and calls tools with its token.
"""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.auth import OAuthClientMetadata

from nexus.accounts import AccountStore, digest
from tests.conftest import PASSWORD, browser, sign_up, start_nexus

pytestmark = pytest.mark.anyio
CALLBACK = "http://localhost:3030/callback"



@pytest.fixture
def hosted(tmp_path):
    base, accounts, stop = start_nexus(tmp_path)
    yield base, accounts
    stop()


@pytest.fixture
def invite_only(tmp_path):
    base, accounts, stop = start_nexus(tmp_path, signup="invite", signup_code="beta-123")
    yield base, accounts
    stop()


class MemoryTokens:
    def __init__(self):
        self.tokens = self.client = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, info):
        self.client = info


def oauth(base, email, password, *, mode="signup", extra=None):
    """An OAuth client whose 'browser' signs in on the consent page with the given credentials."""
    result = {}

    async def browser(auth_url: str) -> None:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            r = await http.get(auth_url)
            assert r.status_code == 302 and "/consent?request=" in r.headers["location"]
            consent = r.headers["location"]
            page = await http.get(consent)
            result["page"] = page.text
            rid = parse_qs(urlparse(consent).query)["request"][0]
            r = await http.post(f"{base}/consent", data={"request": rid, "mode": mode, "email": email, "name": "Tester",
                                                         "password": password, "action": "allow", **(extra or {})})
            result["response"] = r
            assert r.status_code == 302, r.text
            q = parse_qs(urlparse(r.headers["location"]).query)
            result["code"], result["state"] = q["code"][0], q.get("state", [None])[0]

    async def callback():
        return result["code"], result["state"]

    storage = MemoryTokens()
    provider = OAuthClientProvider(
        server_url=f"{base}/mcp",
        client_metadata=OAuthClientMetadata(client_name="Test AI", redirect_uris=[CALLBACK],
                                            grant_types=["authorization_code", "refresh_token"],
                                            response_types=["code"]),
        storage=storage, redirect_handler=browser, callback_handler=callback)
    return provider, storage, result


def connect(base, auth):
    return streamable_http_client(f"{base}/mcp", http_client=create_mcp_http_client(auth=auth))


async def call(session, tool, **args):
    res = await session.call_tool(tool, args)
    if res.isError:
        raise RuntimeError(res.content[0].text)
    return res.structuredContent


async def test_endpoint_requires_auth_and_advertises_metadata(hosted):
    base, _ = hosted
    async with httpx.AsyncClient() as http:
        r = await http.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401
        assert "oauth-protected-resource/mcp" in r.headers["www-authenticate"]
        prm = (await http.get(f"{base}/.well-known/oauth-protected-resource/mcp")).json()
        assert prm["resource"] == f"{base}/mcp"
        asm = (await http.get(f"{base}/.well-known/oauth-authorization-server")).json()
        assert asm["registration_endpoint"] == f"{base}/register" and "S256" in asm["code_challenge_methods_supported"]
        home = await http.get(f"{base}/")
        assert home.status_code == 200 and "{{" not in home.text and home.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in home.headers["content-security-policy"]
        assert "script-src 'self'" in home.headers["content-security-policy"]
        # "no-referrer" would make browsers send "Origin: null" on our own form posts, which the consent check refuses.
        assert home.headers["referrer-policy"] == "same-origin"



def workspace_of(accounts, email):
    user = accounts.find_user(email)
    return user, accounts.workspaces_for(user["id"])[0]


async def test_each_workspace_gets_only_its_own_memory(hosted, tmp_path):
    base, accounts = hosted
    alice, _, page = oauth(base, "alice@example.com", "correct horse battery")
    async with connect(base, alice) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            assert {"save_memory", "check_plan", "get_project_context"} <= tools and "delete_memory" not in tools
            saved = await call(s, "save_memory", project="Wetlands", kind="finding", title="Alice's private finding",
                               origin_model="claude", create_project_if_missing=True)
            ctx = await call(s, "get_project_context", project="Wetlands")
    assert "Test AI" in page["page"] and "localhost:3030" in page["page"]  # consent names the app and return host
    # History says which model, through which app, for which person.
    assert saved["created_by"] == "claude · Test AI · alice@example.com"
    assert ctx["receipt_id"].startswith("rcp_")

    bob, _, _ = oauth(base, "bob@example.com", "another long password")
    async with connect(base, bob) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            assert (await call(s, "list_projects"))["projects"] == []
            assert (await call(s, "search_memory", query="private finding"))["results"] == []

    a_user, a_ws = workspace_of(accounts, "alice@example.com")
    _, b_ws = workspace_of(accounts, "bob@example.com")
    assert accounts.memory_for(a_ws["id"]).list_projects()[0]["name"] == "Wetlands"
    assert accounts.memory_for(b_ws["id"]).list_projects() == []
    assert (tmp_path / "data" / "workspaces" / a_ws["id"] / "memory.db").exists()
    receipt = accounts.get_receipt(a_ws["id"], ctx["receipt_id"])
    assert receipt["channel"] == "mcp" and [x["id"] for x in receipt["record_refs"]] == [saved["id"]]
    with pytest.raises(LookupError):
        accounts.get_receipt(b_ws["id"], ctx["receipt_id"])  # a receipt is not readable from another workspace

    # Signing in again (a second app, same account) reaches the same memory.
    again, _, _ = oauth(base, "ALICE@example.com", "correct horse battery", mode="signin")
    async with connect(base, again) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            assert (await call(s, "list_projects"))["projects"][0]["name"] == "Wetlands"
    assert len(accounts.grants_for(a_user["id"])) == 2
    usage = accounts.usage_summary(a_ws["id"])
    assert sum(x["calls"] for x in usage["by_day"] if x["channel"] == "mcp") == 3  # save, context, list


async def test_read_only_connection_and_role_changes_apply_at_once(hosted):
    base, accounts = hosted
    owner = sign_up(base, "olga@example.com", workspace="Shared")
    _, ws = workspace_of(accounts, "olga@example.com")
    link = owner.post("/api/workspace/invitations", json={"email": "vic@example.com", "role": "editor"}).json()["link"]
    sign_up(base, "vic@example.com", invitation=link.rsplit("/", 1)[1])
    vic = accounts.find_user("vic@example.com")

    # Vic connects one app read-only and one with write access, both to the shared workspace.
    ro, _, _ = oauth(base, "vic@example.com", PASSWORD, mode="signin", extra={"access": "read"})
    async with connect(base, ro) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            with pytest.raises(RuntimeError, match="read-only"):
                await call(s, "create_project", name="Nope")
            assert (await call(s, "list_projects"))["projects"] == []
    rw, storage, _ = oauth(base, "vic@example.com", PASSWORD, mode="signin")
    async with connect(base, rw) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await call(s, "create_project", name="Allowed")
            # Demoted to viewer: the same token can no longer write on its very next call.
            assert owner.patch(f"/api/workspace/members/{vic['id']}", json={"role": "viewer"}).status_code == 200
            with pytest.raises(RuntimeError, match="only read"):
                await call(s, "save_memory", project="Allowed", kind="note", title="x", origin_model="claude")
            assert (await call(s, "list_projects"))["projects"][0]["name"] == "Allowed"
    # Removed from the workspace: the token stops working entirely.
    assert owner.delete(f"/api/workspace/members/{vic['id']}").status_code == 200
    async with httpx.AsyncClient() as http:
        r = await http.post(f"{base}/mcp", headers={"Authorization": f"Bearer {storage.tokens.access_token}"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert accounts.memory_for(ws["id"]).list_projects()[0]["name"] == "Allowed"


async def test_signed_in_person_chooses_the_workspace_when_connecting(hosted):
    base, accounts = hosted
    b = sign_up(base, "wes@example.com", workspace="First")
    second = b.post("/api/workspaces", json={"name": "Second"}).json()
    rid = accounts.create_request("client-x", {"state": "s", "scopes": ["memory"], "code_challenge": "c",
                                               "redirect_uri": CALLBACK, "redirect_uri_provided_explicitly": True,
                                               "resource": f"{base}/mcp"})
    page = b.get(f"/consent?request={rid}")
    assert "Signed in as" in page.text and "First" in page.text and "Second" in page.text and "password" not in page.text
    other = b.post("/consent", data={"request": rid, "workspace": "wsp_000000000000", "action": "allow"})
    assert other.status_code == 400  # not one of theirs
    ok = b.post("/consent", data={"request": rid, "workspace": second["id"], "access": "read", "action": "allow"})
    assert ok.status_code == 302 and "code=" in ok.headers["location"]
    grant = accounts.grants_for(accounts.find_user("wes@example.com")["id"])[0]
    assert grant["workspace"] == "Second" and grant["access"] == "read"


async def test_wrong_password_deny_and_expired_requests(hosted):
    base, accounts = hosted
    accounts.create_user("carol@example.com", "a very good password")
    rid = accounts.create_request("client-x", {"state": "s1", "scopes": ["memory"], "code_challenge": "c",
                                               "redirect_uri": CALLBACK, "redirect_uri_provided_explicitly": True,
                                               "resource": f"{base}/mcp"})
    async with httpx.AsyncClient(follow_redirects=False) as http:
        bad = await http.post(f"{base}/consent", data={"request": rid, "email": "carol@example.com",
                                                       "password": "wrong password", "action": "allow"})
        assert bad.status_code == 400 and "incorrect" in bad.text and "code=" not in bad.headers.get("location", "")
        evil = await http.post(f"{base}/consent", headers={"Origin": "https://evil.example"},
                               data={"request": rid, "action": "deny"})
        assert evil.status_code == 403
        same = await http.post(f"{base}/consent", headers={"Origin": base}, data={"request": rid, "email": "carol@example.com",
                                                                                 "password": "wrong again", "action": "allow"})
        assert same.status_code == 400  # our own origin passes the check and reaches the password check
        deny = await http.post(f"{base}/consent", data={"request": rid, "action": "deny"})
        assert deny.status_code == 302 and "error=access_denied" in deny.headers["location"]
        assert "state=s1" in deny.headers["location"]
        reuse = await http.post(f"{base}/consent", data={"request": rid, "email": "carol@example.com",
                                                         "password": "a very good password", "action": "allow"})
        assert reuse.status_code == 400 and "expired" in reuse.text


async def test_signin_is_rate_limited(hosted):
    base, accounts = hosted
    accounts.create_user("dave@example.com", "a very good password")
    rid = accounts.create_request("client-x", {"state": None, "scopes": ["memory"], "code_challenge": "c",
                                               "redirect_uri": CALLBACK, "redirect_uri_provided_explicitly": True,
                                               "resource": f"{base}/mcp"})
    async with httpx.AsyncClient(follow_redirects=False) as http:
        codes = [(await http.post(f"{base}/consent", data={"request": rid, "email": "dave@example.com",
                                                           "password": f"guess {i}", "action": "allow"})).status_code
                 for i in range(11)]
        assert codes[:10] == [400] * 10 and codes[10] == 429
        right = await http.post(f"{base}/consent", data={"request": rid, "email": "dave@example.com",
                                                         "password": "a very good password", "action": "allow"})
        assert right.status_code == 429  # still blocked even with the right password
        login = await http.post(f"{base}/login", data={"email": "dave@example.com", "password": "a very good password"})
        assert login.status_code == 429  # the sign-in page shares the limit


async def test_invite_code_gates_signup(invite_only):
    base, _ = invite_only
    provider, _, _ = oauth(base, "erin@example.com", "a very good password", extra={"code": "nope"})
    with pytest.raises(BaseException):
        async with connect(base, provider) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
    ok, storage, _ = oauth(base, "erin@example.com", "a very good password", extra={"code": "beta-123"})
    async with connect(base, ok) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
    assert storage.tokens.access_token.startswith("nxa_")
    with browser(base) as b:
        assert "Invite code" in b.get("/signup").text
        assert b.post("/signup", data={"name": "X", "email": "x@example.com", "password": PASSWORD}).status_code == 400


async def test_revoked_access_stops_working(hosted):
    base, accounts = hosted
    provider, storage, _ = oauth(base, "frank@example.com", "a very good password")
    async with connect(base, provider) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
    token = storage.tokens.access_token
    user = accounts.find_user("frank@example.com")["id"]
    grant = accounts.grants_for(user)[0]
    assert grant["client_name"] == "Test AI" and grant["last_used_at"]
    assert accounts.revoke_grant(grant["id"], user_id=user)
    async with httpx.AsyncClient() as http:
        r = await http.post(f"{base}/mcp", headers={"Authorization": f"Bearer {token}"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_secrets_are_hashed_and_single_use(tmp_path):
    acc = AccountStore(tmp_path)
    user = acc.create_user("gina@example.com", "a very good password")
    ws = acc.create_workspace("Gina's", user["id"])
    params = {"state": None, "scopes": ["memory"], "code_challenge": "c", "redirect_uri": CALLBACK,
              "redirect_uri_provided_explicitly": True, "resource": "http://127.0.0.1/mcp"}
    code = acc.issue_code(user["id"], "client-x", "X", params, workspace_id=ws["id"])
    grant = acc.redeem_code(code)
    assert grant and acc.redeem_code(code) is None
    t = acc.issue_tokens(grant)
    assert acc.lookup_token(t["access_token"], "access")["user_id"] == user["id"]
    assert acc.lookup_token(t["access_token"], "access")["workspace_id"] == ws["id"]
    assert acc.lookup_token(t["access_token"], "refresh") is None  # kinds are not interchangeable
    assert acc.rotate_refresh(t["refresh_token"]) == grant and acc.rotate_refresh(t["refresh_token"]) is None
    assert acc.lookup_token(t["access_token"], "access") is None  # rotation drops the old access token
    key, secret = acc.create_api_key(ws["id"], "ci")
    acc.set_credential(ws["id"], "openai", "sk-live-0123456789abcdef")
    invitation, invite_token = acc.create_invitation(ws["id"], "new@example.com", "viewer", user["id"])
    session = acc.create_session(user["id"])
    reset = acc.create_email_token(user["id"], "reset")
    dumped = "\n".join(acc.conn.iterdump())
    for value in (code, t["access_token"], t["refresh_token"], "a very good password", secret,
                  "sk-live-0123456789abcdef", invite_token, session, reset):
        assert value not in dumped
    fresh = acc.issue_tokens(grant)
    assert digest(fresh["access_token"]) in "\n".join(acc.conn.iterdump())
    assert acc.authenticate("gina@example.com", "wrong") is None
    assert acc.authenticate("GINA@example.com", "a very good password")["id"] == user["id"]
    with pytest.raises(Exception, match="already exists"):
        acc.create_user("gina@example.com", "a very good password")
    with pytest.raises(Exception, match="at least"):
        acc.create_user("h@example.com", "short")
    acc.close()


def test_account_from_before_workspaces_keeps_its_memory_and_connections(tmp_path):
    """An installation that predates workspaces: a user, their memory file and an app approval, no workspace."""
    from nexus.store import MemoryStore
    acc = AccountStore(tmp_path)
    user = acc.create_user("old@example.com", "a very good password")
    legacy = MemoryStore(tmp_path / "users" / user["id"] / "memory.db")
    legacy.create_project("From before")
    legacy.close()
    with acc._tx() as c:  # an approval as the earlier version stored it: no workspace
        c.execute("INSERT INTO grants (id, user_id, client_id, client_name, scopes, resource, created_at) "
                  "VALUES ('grt_legacy000001', ?, 'client-x', 'Old app', '[\"memory\"]', 'http://x/mcp', '2026-11-01')",
                  (user["id"],))
    tokens = acc.issue_tokens("grt_legacy000001")
    found = acc.lookup_token(tokens["access_token"], "access")
    assert found and found["role"] == "owner" and found["can_write"]
    assert acc.memory_for(found["workspace_id"]).list_projects()[0]["name"] == "From before"
    assert acc.workspaces_for(user["id"])[0]["id"] == found["workspace_id"]
    assert acc.ensure_personal_workspace(user)["id"] == found["workspace_id"]  # and it is not created twice
    acc.close()


async def test_static_files_and_access_requests(hosted):
    base, accounts = hosted
    async with httpx.AsyncClient(follow_redirects=False) as http:
        assert (await http.get(f"{base}/static/site/site.css")).status_code == 200
        font = await http.get(f"{base}/static/fonts/archivo-var.woff2")
        assert font.status_code == 200 and "immutable" in font.headers["cache-control"]
        for path in ("/static/index.html", "/static/site/../../server.py", "/static/fonts/../schema.sql",
                     "/static/site/_layout.html"):
            assert (await http.get(f"{base}{path}")).status_code == 404  # only the public site's files
        assert (await http.get(f"{base}/healthz")).json()["ok"]
        assert (await http.get(f"{base}/readyz")).json() == {"ok": True, "migrations": 4, "pending": [], "secrets": True}
        bad = await http.post(f"{base}/beta", json={"email": "not an email"})
        assert bad.status_code == 400
        ok = await http.post(f"{base}/beta", json={"email": "ivy@example.com", "topic": "Wetlands"})
        assert ok.json()["ok"] and "on the list" in ok.json()["message"]
        again = await http.post(f"{base}/beta", json={"email": "IVY@example.com"})
        assert "already" in again.json()["message"]
        bot = await http.post(f"{base}/beta", json={"email": "bot@example.com", "website": "spam"})
        assert bot.json()["ok"]
        cross = await http.post(f"{base}/beta", headers={"Origin": "https://evil.example"}, json={"email": "x@example.com"})
        assert cross.status_code == 403
        form = await http.post(f"{base}/beta", data={"email": "jo@example.com"})
        assert form.status_code == 303
        limited = await http.post(f"{base}/beta", json={"email": "kim@example.com"})
        assert limited.status_code == 429  # five per hour per network
    assert [r["email"] for r in accounts.beta_requests()] == ["ivy@example.com", "jo@example.com"]

async def test_strict_discovery_and_public_client_without_secret(hosted):
    """What ChatGPT does: exact issuer match, then a PKCE client registered with no secret."""
    import base64
    import hashlib
    base, _ = hosted
    async with httpx.AsyncClient(follow_redirects=False) as http:
        asm = (await http.get(f"{base}/.well-known/oauth-authorization-server")).json()
        assert asm["issuer"] == base and "none" in asm["token_endpoint_auth_methods_supported"]
        assert (await http.get(f"{base}/.well-known/openid-configuration")).json()["issuer"] == base
        prm = (await http.get(f"{base}/.well-known/oauth-protected-resource/mcp")).json()
        assert prm["authorization_servers"] == [base]

        redirect = "https://chatgpt.com/connector_platform_oauth_redirect"
        reg = (await http.post(f"{base}/register", json={
            "client_name": "ChatGPT", "redirect_uris": [redirect], "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})).json()
        assert "client_secret" not in reg or not reg["client_secret"]
        verifier = "v" * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        r = await http.get(f"{base}/authorize", params={
            "response_type": "code", "client_id": reg["client_id"], "redirect_uri": redirect, "state": "s",
            "code_challenge": challenge, "code_challenge_method": "S256", "resource": f"{base}/mcp"})
        rid = parse_qs(urlparse(r.headers["location"]).query)["request"][0]
        r = await http.post(f"{base}/consent", data={"request": rid, "mode": "signup", "email": "pat@example.com", "name": "Pat",
                                                     "password": "a very good password", "action": "allow"})
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        tok = await http.post(f"{base}/token", data={"grant_type": "authorization_code", "code": code,
                                                     "redirect_uri": redirect, "client_id": reg["client_id"],
                                                     "code_verifier": verifier})
        assert tok.status_code == 200, tok.text
        listed = await http.post(f"{base}/mcp", headers={
            "Authorization": f"Bearer {tok.json()['access_token']}", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        assert listed.status_code == 200 and "nexus" in listed.text


@pytest.mark.skipif(not __import__("os").environ.get("NEXUS_TEST_PG_URL"), reason="needs NEXUS_TEST_PG_URL")
def test_control_plane_on_postgres_keeps_workspaces_apart():
    import os
    import uuid

    url = os.environ["NEXUS_TEST_PG_URL"]
    acc = AccountStore(url)
    assert acc.migrations_applied in ([], ["0001_accounts", "0002_workspaces", "0003_keys", "0004_metering"])
    a = acc.create_user(f"a-{uuid.uuid4().hex[:6]}@Example.com", "a very good password")
    b = acc.create_user(f"b-{uuid.uuid4().hex[:6]}@example.com", "a very good password")
    wa, wb = acc.create_workspace("A team", a["id"]), acc.create_workspace("B team", b["id"])
    try:
        assert acc.authenticate(a["email"].upper(), "a very good password")["id"] == a["id"]
        with pytest.raises(Exception, match="already exists"):
            acc.create_user(a["email"], "a very good password")
        acc.memory_for(wa["id"]).ensure_project("Mine")
        acc.memory_for(wa["id"]).save_record("Mine", kind="finding", title="Only for A", origin_model="claude")
        assert acc.memory_for(wb["id"]).list_projects() == []
        assert acc.memory_for(wb["id"]).search("Only for A")["results"] == []
        code = acc.issue_code(a["id"], "client-x", "X", {"state": None, "scopes": ["memory"], "code_challenge": "c",
                                                          "redirect_uri": CALLBACK, "redirect_uri_provided_explicitly": True,
                                                          "resource": "http://x/mcp"}, workspace_id=wa["id"])
        tokens = acc.issue_tokens(acc.redeem_code(code))
        assert acc.lookup_token(tokens["access_token"], "access")["workspace_id"] == wa["id"]
        key, secret = acc.create_api_key(wa["id"], "ci")
        assert acc.lookup_api_key(secret)["workspace_id"] == wa["id"]
        if acc.keyring:
            acc.set_credential(wa["id"], "openai", "sk-test-0123456789")
            assert acc.open_credential(wa["id"], "openai")["secret"] == "sk-test-0123456789"
        acc.record_usage(wa["id"], channel="gateway", operation="chat", model="m", input_tokens=3)
        assert acc.usage_summary(wa["id"])["month"]["gateway_requests"] == 1
        assert acc.usage_summary(wb["id"])["month"]["gateway_requests"] == 0
        inv, token = acc.create_invitation(wa["id"], b["email"], "viewer", a["id"])
        assert acc.accept_invitation(token, b)["id"] == wa["id"] and acc.role_in(wa["id"], b["id"]) == "viewer"
    finally:
        for u in (a, b):
            acc.delete_user(u["id"]) if len(acc.members(wa["id"])) < 2 or u is b else None
        acc.delete_user(a["id"]) if acc.find_user(a["email"]) else None


async def test_app_sign_in_and_isolation(hosted):
    base, accounts = hosted
    a0 = sign_up(base, "dash-a@example.com", workspace="Alice team")
    assert a0.post("/api/projects", json={"name": "Alice's project"}).status_code == 201
    a0.close()
    sign_up(base, "dash-b@example.com").close()
    async with httpx.AsyncClient(base_url=base, follow_redirects=False) as anon:
        assert (await anon.get("/app")).headers["location"].startswith("/login")
        assert (await anon.get("/api/projects")).status_code == 401
        assert (await anon.get("/api/me")).status_code == 401
        assert "Sign in" in (await anon.get("/login")).text
        bad = await anon.post("/login", data={"email": "dash-a@example.com", "password": "nope"})
        assert bad.status_code == 400 and "incorrect" in bad.text
        away = await anon.get("/login?next=https://evil.example/")
        assert "evil.example" not in away.text  # sign-in only ever leads back into this site
    async with httpx.AsyncClient(base_url=base, follow_redirects=False) as a:
        r = await a.post("/login", data={"email": "DASH-A@example.com", "password": PASSWORD, "next": "/app"})
        assert r.status_code == 303 and r.headers["location"] == "/app"
        cookie = r.headers["set-cookie"]
        assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
        assert "<title>" in (await a.get("/app")).text
        meta = (await a.get("/api/meta")).json()
        assert meta["actor"] == "dash-a@example.com" and meta["mcp"]["url"] == f"{base}/mcp"
        assert meta["workspace"]["name"] == "Alice team" and meta["role"] == "owner"
        assert meta["live"] == "poll" and "db_path" not in meta
        assert [p["name"] for p in (await a.get("/api/projects")).json()["projects"]] == ["Alice's project"]
        assert (await a.post("/api/projects", json={"name": "No header"})).status_code == 403
        assert (await a.post("/api/projects", json={"name": "Cross"}, headers={
            "X-Nexus": "1", "Origin": "https://evil.example"})).status_code == 403
        assert (await a.post("/api/projects", json={"name": "Second"}, headers={"X-Nexus": "1"})).status_code == 201
        assert (await a.get("/api/revision")).json()["revision"]
        assert (await a.get("/api/events")).status_code == 404  # hosted pages poll instead
        # Asking for a workspace you are not in is refused, not silently redirected to your own.
        assert (await a.get("/api/projects", headers={"X-Nexus-Workspace": "dash-bs-workspace"})).status_code == 403
        await a.post("/logout")
        assert (await a.get("/api/projects")).status_code == 401
    async with httpx.AsyncClient(base_url=base, follow_redirects=False) as b:
        await b.post("/login", data={"email": "dash-b@example.com", "password": PASSWORD})
        assert (await b.get("/api/projects")).json()["projects"] == []
