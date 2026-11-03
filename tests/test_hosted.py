"""Hosted service: OAuth sign-in as a real MCP client performs it, and per-user isolation.

A real uvicorn server runs on a free local port; the MCP SDK's own OAuth client
registers itself, follows the sign-in redirect, and calls tools with its token.
"""

import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.auth import OAuthClientMetadata

from memlayer.accounts import AccountStore, digest
from memlayer.hosted import create_hosted_app

pytestmark = pytest.mark.anyio
CALLBACK = "http://localhost:3030/callback"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(tmp_path, signup_code=None):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    app, accounts = create_hosted_app(str(tmp_path / "data"), base, signup_code=signup_code)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    return server, thread, base, accounts


@pytest.fixture
def hosted(tmp_path):
    server, thread, base, accounts = serve(tmp_path)
    yield base, accounts
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def invite_only(tmp_path):
    server, thread, base, accounts = serve(tmp_path, signup_code="beta-123")
    yield base, accounts
    server.should_exit = True
    thread.join(5)


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
            r = await http.post(f"{base}/consent", data={"request": rid, "mode": mode, "email": email,
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
        assert f"{base}/mcp" in home.text and home.headers["x-frame-options"] == "DENY"
        # "no-referrer" would make browsers send "Origin: null" on our own form posts, which the consent check refuses.
        assert home.headers["referrer-policy"] == "same-origin"


async def test_each_user_gets_only_their_own_memory(hosted, tmp_path):
    base, accounts = hosted
    alice, _, page = oauth(base, "alice@example.com", "correct horse battery")
    async with connect(base, alice) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            assert "save_memory" in {t.name for t in (await s.list_tools()).tools}
            await call(s, "save_memory", project="Wetlands", kind="finding", title="Alice's private finding",
                       origin_model="claude", create_project_if_missing=True)
    assert "Test AI" in page["page"] and "localhost:3030" in page["page"]  # consent names the app and return host

    bob, _, _ = oauth(base, "bob@example.com", "another long password")
    async with connect(base, bob) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            assert (await call(s, "list_projects"))["projects"] == []
            assert (await call(s, "search_memory", query="private finding"))["results"] == []

    users = {u["email"]: u["id"] for u in
             (dict(r) for r in accounts.conn.execute("SELECT id, email FROM users").fetchall())}
    assert accounts.memory_for(users["alice@example.com"]).list_projects()[0]["name"] == "Wetlands"
    assert accounts.memory_for(users["bob@example.com"]).list_projects() == []
    assert (tmp_path / "data" / "users" / users["alice@example.com"] / "memory.db").exists()

    # Signing in again (a second app, same account) reaches the same memory.
    again, _, _ = oauth(base, "ALICE@example.com", "correct horse battery", mode="signin")
    async with connect(base, again) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            assert (await call(s, "list_projects"))["projects"][0]["name"] == "Wetlands"
    assert len(accounts.grants_for(users["alice@example.com"])) == 2


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


async def test_invite_code_gates_signup(invite_only):
    base, _ = invite_only
    provider, _, _ = oauth(base, "erin@example.com", "a very good password", extra={"invite": "nope"})
    with pytest.raises(BaseException):
        async with connect(base, provider) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
    ok, storage, _ = oauth(base, "erin@example.com", "a very good password", extra={"invite": "beta-123"})
    async with connect(base, ok) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
    assert storage.tokens.access_token.startswith("mla_")


async def test_revoked_access_stops_working(hosted):
    base, accounts = hosted
    provider, storage, _ = oauth(base, "frank@example.com", "a very good password")
    async with connect(base, provider) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
    token = storage.tokens.access_token
    user = accounts.conn.execute("SELECT id FROM users WHERE email = 'frank@example.com'").fetchone()[0]
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
    params = {"state": None, "scopes": ["memory"], "code_challenge": "c", "redirect_uri": CALLBACK,
              "redirect_uri_provided_explicitly": True, "resource": "http://127.0.0.1/mcp"}
    code = acc.issue_code(user["id"], "client-x", "X", params)
    grant = acc.redeem_code(code)
    assert grant and acc.redeem_code(code) is None
    t = acc.issue_tokens(grant)
    assert acc.lookup_token(t["access_token"], "access")["user_id"] == user["id"]
    assert acc.lookup_token(t["access_token"], "refresh") is None  # kinds are not interchangeable
    assert acc.rotate_refresh(t["refresh_token"]) == grant and acc.rotate_refresh(t["refresh_token"]) is None
    assert acc.lookup_token(t["access_token"], "access") is None  # rotation drops the old access token
    dumped = "\n".join(acc.conn.iterdump())
    for secret in (code, t["access_token"], t["refresh_token"], "a very good password"):
        assert secret not in dumped
    fresh = acc.issue_tokens(grant)
    assert digest(fresh["access_token"]) in "\n".join(acc.conn.iterdump())
    assert acc.authenticate("gina@example.com", "wrong") is None
    assert acc.authenticate("GINA@example.com", "a very good password")["id"] == user["id"]
    with pytest.raises(Exception, match="already exists"):
        acc.create_user("gina@example.com", "a very good password")
    with pytest.raises(Exception, match="at least"):
        acc.create_user("h@example.com", "short")
    acc.close()


async def test_landing_static_and_invite_requests(hosted):
    base, accounts = hosted
    async with httpx.AsyncClient(follow_redirects=False) as http:
        page = await http.get(f"{base}/")
        assert "Request access" in page.text and f"{base}/mcp" in page.text and "{{" not in page.text
        assert (await http.get(f"{base}/static/site/site.js")).status_code == 200
        font = await http.get(f"{base}/static/fonts/archivo-var.woff2")
        assert font.status_code == 200 and "immutable" in font.headers["cache-control"]
        for path in ("/static/js/main.js", "/static/site/../../server.py", "/static/fonts/../schema.sql"):
            assert (await http.get(f"{base}{path}")).status_code == 404  # only the public site's files
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
        r = await http.post(f"{base}/consent", data={"request": rid, "mode": "signup", "email": "pat@example.com",
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
        assert listed.status_code == 200 and "memlayer" in listed.text


@pytest.mark.skipif(not __import__("os").environ.get("MEMLAYER_TEST_PG_URL"), reason="needs MEMLAYER_TEST_PG_URL")
def test_accounts_on_postgres_keep_users_apart():
    import os
    import uuid

    import psycopg

    url = os.environ["MEMLAYER_TEST_PG_URL"]
    acc = AccountStore(url)
    a = acc.create_user(f"a-{uuid.uuid4().hex[:6]}@Example.com", "a very good password")
    b = acc.create_user(f"b-{uuid.uuid4().hex[:6]}@example.com", "a very good password")
    try:
        assert acc.authenticate(a["email"].upper(), "a very good password")["id"] == a["id"]
        with pytest.raises(Exception, match="already exists"):
            acc.create_user(a["email"], "a very good password")
        acc.memory_for(a["id"]).save_record("Mine", kind="finding", title="Only for A", origin_model="claude") \
            if acc.memory_for(a["id"]).ensure_project("Mine") else None
        assert acc.memory_for(b["id"]).list_projects() == []
        assert acc.memory_for(b["id"]).search("Only for A")["results"] == []
        code = acc.issue_code(a["id"], "client-x", "X", {"state": None, "scopes": ["memory"], "code_challenge": "c",
                                                          "redirect_uri": CALLBACK, "redirect_uri_provided_explicitly": True,
                                                          "resource": "http://x/mcp"})
        tokens = acc.issue_tokens(acc.redeem_code(code))
        assert acc.lookup_token(tokens["access_token"], "access")["user_id"] == a["id"]
        assert acc.add_beta_request(f"x-{uuid.uuid4().hex[:6]}@example.com") is True
    finally:
        with psycopg.connect(url, autocommit=True) as c:
            for u in (a, b):
                c.execute(f'DROP SCHEMA IF EXISTS "u_{u["id"][4:]}" CASCADE')
                c.execute("DELETE FROM memlayer_accounts.users WHERE id = %s", (u["id"],))
