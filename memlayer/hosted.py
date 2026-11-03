"""The hosted service: one MCP address on the internet that ChatGPT, Claude, Gemini and Perplexity connect to.

    memlayer hosted --public-url https://memory.example.com --data-dir /data

Users paste ``<public-url>/mcp`` into their AI app. The app registers itself
(OAuth dynamic client registration), sends the user to /consent to sign in or
create an account and approve access, and then calls the memory tools with a
bearer token. Every call is answered from that user's own database file.

Run it behind a reverse proxy or platform that terminates HTTPS. Plain HTTP is
accepted only for localhost testing.
"""

from __future__ import annotations

import argparse
import hmac
import html
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import construct_redirect_uri
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .accounts import MIN_PASSWORD, AccountStore
from .brand import BRAND
from .models import ValidationError
from .oauth import SCOPE, MemLayerOAuthProvider
from .server import create_server


class RateLimiter:
    """Failed sign-ins per key in a sliding window. In memory: enough for a single instance."""

    def __init__(self, limit: int = 10, window: float = 900):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def blocked(self, *keys: str) -> bool:
        cutoff = time.monotonic() - self.window
        with self._lock:
            for k in keys:
                q = self._hits[k]
                while q and q[0] < cutoff:
                    q.popleft()
                if len(q) >= self.limit:
                    return True
        return False

    def fail(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._hits[k].append(time.monotonic())


class SecurityHeaders:
    """Pure ASGI so the MCP endpoint's streaming responses pass through untouched."""

    def __init__(self, app: ASGIApp, https: bool):
        self.app = app
        self.headers = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            # Not "no-referrer": with it, browsers send "Origin: null" on our own form posts and the cross-site
            # check below refuses them. "same-origin" keeps the one-time sign-in URL from leaking to other sites.
            (b"referrer-policy", b"same-origin"),
            (b"content-security-policy", b"frame-ancestors 'none'"),
        ]
        if https:
            self.headers.append((b"strict-transport-security", b"max-age=31536000"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + self.headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class WellKnown:
    """Serves the OAuth discovery documents ourselves, ahead of the SDK's.

    The SDK writes the issuer as ``https://host/`` (with a slash). Strict clients, ChatGPT among them, fetch
    ``/.well-known/oauth-authorization-server`` and require the issuer to equal ``https://host`` exactly, so
    they reject the slash. This also advertises ``none`` for public clients that use PKCE without a secret,
    and answers the OpenID path some clients try first.
    """

    AS_PATHS = {"/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/mcp",
                "/.well-known/openid-configuration", "/mcp/.well-known/openid-configuration"}
    PRM_PATHS = {"/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"}

    def __init__(self, app: ASGIApp, public_url: str):
        self.app = app
        auth_methods = ["client_secret_post", "client_secret_basic", "none"]
        self.as_meta = {
            "issuer": public_url,
            "authorization_endpoint": f"{public_url}/authorize",
            "token_endpoint": f"{public_url}/token",
            "registration_endpoint": f"{public_url}/register",
            "revocation_endpoint": f"{public_url}/revoke",
            "scopes_supported": [SCOPE],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": auth_methods,
            "revocation_endpoint_auth_methods_supported": auth_methods,
            "code_challenge_methods_supported": ["S256"],
        }
        self.prm = {"resource": f"{public_url}/mcp", "authorization_servers": [public_url],
                    "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"],
                    "resource_name": BRAND["name"]}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and scope["method"] == "GET" and (path in self.AS_PATHS or path in self.PRM_PATHS):
            body = self.as_meta if path in self.AS_PATHS else self.prm
            response = JSONResponse(body, headers={"access-control-allow-origin": "*", "cache-control": "no-store"})
            return await response(scope, receive, send)
        await self.app(scope, receive, send)


def create_hosted_app(data_dir: str, public_url: str, *, signup_code: str | None = None,
                      accounts: AccountStore | None = None) -> tuple[ASGIApp, AccountStore]:
    public_url = public_url.rstrip("/")
    parsed = urlparse(public_url)
    accounts = accounts or AccountStore(data_dir)
    provider = MemLayerOAuthProvider(accounts, public_url)
    limiter = RateLimiter()
    beta_limiter = RateLimiter(limit=5, window=3600)

    def store_for_request():
        token = get_access_token()
        if token is None or not token.subject:  # the auth middleware rejects these before any tool runs
            raise PermissionError("Not signed in")
        return accounts.memory_for(token.subject)

    mcp: FastMCP = create_server(
        store_for_request=store_for_request,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=f"{public_url}/mcp",
            validate_token_resource=True,
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE],
                                                                  default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True),
        ),
        host="0.0.0.0",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[parsed.netloc, f"{parsed.hostname}:*"],
            allowed_origins=[f"{parsed.scheme}://{parsed.netloc}"],
        ),
    )

    # ----------------------------------------------------------- pages

    @mcp.custom_route("/", methods=["GET"])
    async def home(request: Request) -> Response:
        return HTMLResponse(_landing(public_url), headers={"cache-control": "no-cache"})

    @mcp.custom_route("/static/{path:path}", methods=["GET"])
    async def static(request: Request) -> Response:
        rel = request.path_params["path"]
        target = (STATIC_DIR / rel).resolve()
        if not rel.startswith(PUBLIC_STATIC) or STATIC_DIR not in target.parents or not target.is_file():
            return Response("Not found", status_code=404)
        cache = "public, max-age=31536000, immutable" if rel.startswith("fonts/") else "no-cache"
        return FileResponse(target, headers={"cache-control": cache})

    @mcp.custom_route("/beta", methods=["POST"])
    async def beta(request: Request) -> Response:
        origin = request.headers.get("origin")
        if origin and origin != f"{parsed.scheme}://{parsed.netloc}":
            return JSONResponse({"error": "Cross-site request refused."}, status_code=403)
        is_json = request.headers.get("content-type", "").startswith("application/json")
        try:
            data = await request.json() if is_json else dict(await request.form())
        except ValueError:
            return JSONResponse({"error": "Send the form again."}, status_code=400)
        ip = request.client.host if request.client else "unknown"
        if beta_limiter.blocked(f"ip:{ip}"):
            return JSONResponse({"error": "Too many requests from this network. Try again in an hour."}, status_code=429)
        beta_limiter.fail(f"ip:{ip}")
        if str(data.get("website", "")).strip():  # honeypot: bots fill every field
            return JSONResponse({"ok": True, "message": "Thank you. You're on the list."})
        try:
            created = accounts.add_beta_request(str(data.get("email", "")), str(data.get("topic", "")))
        except ValidationError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if not is_json:
            return RedirectResponse("/?requested=1#request", status_code=303)
        message = ("Thank you. You're on the list, and we'll email you when an invite is ready." if created else
                   "You're already on the list. We'll email you when an invite is ready.")
        return JSONResponse({"ok": True, "message": message})

    @mcp.custom_route("/healthz", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True})

    @mcp.custom_route("/consent", methods=["GET", "POST"])
    async def consent(request: Request) -> Response:
        if request.method == "GET":
            rid = request.query_params.get("request", "")
            mode = "signup" if request.query_params.get("mode") == "signup" else "signin"
            req = accounts.get_request(rid)
            if not req:
                return HTMLResponse(_expired_page(), status_code=400)
            return HTMLResponse(_consent_page(req, accounts.get_client(req["client_id"]), mode, signup_code is not None))

        origin = request.headers.get("origin")
        if origin and origin != f"{parsed.scheme}://{parsed.netloc}":
            return HTMLResponse("Cross-site request refused.", status_code=403)
        form = await request.form()
        rid = str(form.get("request", ""))
        mode = "signup" if form.get("mode") == "signup" else "signin"
        req = accounts.get_request(rid)
        if not req:
            return HTMLResponse(_expired_page(), status_code=400)
        client = accounts.get_client(req["client_id"])
        params = req["params"]

        if form.get("action") == "deny":
            if accounts.take_request(rid):
                return RedirectResponse(construct_redirect_uri(params["redirect_uri"], error="access_denied",
                                                               state=params["state"]), status_code=302)
            return HTMLResponse(_expired_page(), status_code=400)

        email = str(form.get("email", "")).strip()
        password = str(form.get("password", ""))
        ip = request.client.host if request.client else "unknown"
        keys = (f"ip:{ip}", f"email:{email.lower()}")

        def again(message: str, status: int = 400) -> Response:
            return HTMLResponse(_consent_page(req, client, mode, signup_code is not None, error=message, email=email),
                                status_code=status)

        if limiter.blocked(*keys):
            return again("Too many attempts. Wait 15 minutes and try again.", 429)
        if mode == "signup":
            if signup_code is not None and not hmac.compare_digest(str(form.get("invite", "")), signup_code):
                limiter.fail(*keys)
                return again("That invite code isn't valid.")
            try:
                user = accounts.create_user(email, password)
            except ValidationError as e:
                return again(str(e))
        else:
            user = accounts.authenticate(email, password)
            if not user:
                limiter.fail(*keys)
                return again("Email or password is incorrect.")

        if not accounts.take_request(rid):
            return HTMLResponse(_expired_page(), status_code=400)
        code = accounts.issue_code(user["id"], req["client_id"], (client or {}).get("client_name"), params)
        return RedirectResponse(construct_redirect_uri(params["redirect_uri"], code=code, state=params["state"]),
                                status_code=302)

    app = WellKnown(mcp.streamable_http_app(), public_url)
    return SecurityHeaders(app, https=parsed.scheme == "https"), accounts


# --------------------------------------------------------------------- HTML

STATIC_DIR = Path(__file__).resolve().parent / "static"
PUBLIC_STATIC = ("site/", "fonts/")  # the dashboard's own files are not served by the hosted app


def _landing(public_url: str) -> str:
    e = html.escape
    url = f"{public_url}/mcp"
    page = (STATIC_DIR / "site" / "index.html").read_text(encoding="utf-8")
    return (page.replace("{{BRAND_NAME}}", e(BRAND["name"])).replace("{{MCP_URL}}", e(url))
            .replace("{{CLAUDE_CODE_CMD}}", e(f"claude mcp add --transport http {BRAND['mcp_server_name']} {url}")))


def _page(title: str, body: str) -> str:
    name = html.escape(BRAND["name"])
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{html.escape(title)} · {name}</title>"
            "<link rel='stylesheet' href='/static/site/site.css'></head><body class='auth'>"
            f"<header class='masthead'><div class='masthead-row'><a class='wordmark' href='/'>{name}</a></div></header>"
            f"<main class='auth-main'>{body}</main></body></html>")


def _consent_page(req: dict[str, Any], client: dict[str, Any] | None, mode: str, needs_invite: bool,
                  error: str | None = None, email: str = "") -> str:
    e = html.escape
    client = client or {}
    # Any app can register itself under any name, so the page also shows where the user will be sent back to.
    app_name = e(client.get("client_name") or "An app")
    back_to = e(urlparse(req["params"]["redirect_uri"]).netloc or req["params"]["redirect_uri"])
    signup = mode == "signup"
    rid = e(req["id"])
    other = (f"Already have an account? <a href='/consent?request={rid}'>Sign in</a>" if signup else
             f"New here? <a href='/consent?request={rid}&amp;mode=signup'>Create an account</a>")
    invite = ("<label for='invite'>Invite code</label><input id='invite' name='invite' required autocomplete='off'>"
              if signup and needs_invite else "")
    body = f"""<h1 class='auth-title'>Connect <span>{app_name}</span></h1>
<p class='auth-lede'>{'Create an account' if signup else 'Sign in'} to let <strong>{app_name}</strong> use your
{e(BRAND['name'])} memory.</p>
<dl class='grant'>
<div><dt>It can</dt><dd>Read, add and update records in all your projects</dd></div>
<div><dt>It cannot</dt><dd>Delete anything. Every change keeps the previous version.</dd></div>
<div><dt>Then</dt><dd>You go back to <strong>{back_to}</strong></dd></div>
</dl>
{f"<p class='auth-err' role='alert'>{e(error)}</p>" if error else ""}
<form class='beta-form auth-form' method='post' action='/consent'>
<input type='hidden' name='request' value='{rid}'><input type='hidden' name='mode' value='{mode}'>
<label for='email'>Email</label><input id='email' name='email' type='email' required autocomplete='email' value='{e(email)}'>
<label for='password'>Password{" <span class='opt'>At least " + str(MIN_PASSWORD) + " characters</span>" if signup else ""}</label>
<input id='password' name='password' type='password' required minlength='{MIN_PASSWORD if signup else 1}'
 autocomplete='{'new-password' if signup else 'current-password'}'>
{invite}
<div class='auth-actions'><button class='btn' type='submit' name='action' value='deny' formnovalidate>Cancel</button>
<button class='btn btn-red' type='submit' name='action' value='allow'>{'Create account and allow' if signup else 'Allow access'}</button></div>
</form><p class='auth-switch'>{other}</p>"""
    return _page("Connect", body)


def _expired_page() -> str:
    return _page("Link expired", "<h1 class='auth-title'>This sign-in link has expired</h1><p class='auth-lede'>Go back "
                 "to your AI app and connect again. Sign-in links last 15 minutes and work once.</p>")


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Hosted memlayer service (MCP over HTTPS with sign-in)")
    ap.add_argument("--public-url", default=os.environ.get("MEMLAYER_PUBLIC_URL"),
                    help="Public https:// address users connect to ($MEMLAYER_PUBLIC_URL)")
    ap.add_argument("--data-dir", default=os.environ.get("MEMLAYER_DATA_DIR", "~/.memlayer/hosted"),
                    help="Where accounts.db and each user's memory live ($MEMLAYER_DATA_DIR)")
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--list-beta", action="store_true", help="print the private-beta invite requests and exit")
    args = ap.parse_args(argv)
    if args.list_beta:
        for r in AccountStore(args.data_dir).beta_requests():
            print(f"{r['created_at'][:16]}  {r['email']}  {r['topic']}")
        return
    if not args.public_url:
        ap.error("--public-url (or MEMLAYER_PUBLIC_URL) is required, e.g. https://memory.example.com")
    app, _ = create_hosted_app(args.data_dir, args.public_url, signup_code=os.environ.get("MEMLAYER_SIGNUP_CODE") or None)
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, log_level="info")


if __name__ == "__main__":
    main()
