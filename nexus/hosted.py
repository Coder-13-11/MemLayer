"""The hosted service: the public site, the signed-in app, the REST API, the gateway and the MCP endpoint in
one ASGI application.

    nexus hosted --public-url https://nexus.example.com --data-dir /data

Requests are routed by path, and each family has exactly one way of deciding who is asking:

    /app, /api/*     a browser session cookie            -> a person in one of their workspaces
    /v1/*            a NEXUS API key (nxk_...)            -> a workspace, as viewer or editor
    /mcp             an OAuth access token                -> the workspace the person connected the app to
    everything else  public pages, sign-in and OAuth endpoints

Run it behind a reverse proxy or platform that terminates HTTPS. Plain HTTP is accepted only for localhost.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import construct_redirect_uri
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__, pages
from .account_api import account_routes
from .accounts import SESSION_TTL, AccountStore
from .brand import BRAND
from .config import Settings
from .demo import load_demo
from .gateway import Gateway
from .mailer import Mailer
from .models import NotFound, ValidationError
from .oauth import SCOPE, NexusOAuthProvider
from .principal import Principal, current, reset_current, set_current
from .server import create_server
from .tenancy import LimitReached, PermissionDenied, require_under_limit_hook
from .web import create_app as create_memory_api

SESSION_COOKIE = "nx_session"
PUBLIC_STATIC = ("site/", "fonts/", "js/", "app.css")  # the public site, fonts and the app's own code
WRITES = {"POST", "PATCH", "PUT", "DELETE"}
GATEWAY_PATHS = {"/v1/chat/completions": "chat", "/v1/responses": "responses", "/v1/messages": "messages",
                 "/v1/messages/count_tokens": "count_tokens"}
NEEDS = {"write": "editor", "create_project": "editor", "create_record": "editor", "delete_record": "editor",
         "delete_project": "admin"}


def client_ip(request: Request) -> str:
    """The visitor's address, for rate limiting and the audit log. Behind Vercel's proxy the socket address is
    the proxy, and Vercel sets x-real-ip itself; anywhere else that header could be forged, so it is ignored.
    Behind another proxy, run uvicorn with --proxy-headers and --forwarded-allow-ips set to that proxy."""
    if os.environ.get("VERCEL") and request.headers.get("x-real-ip"):
        return request.headers["x-real-ip"]
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Failed attempts per key in a sliding window. In memory: enough for a single instance."""

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
    """Pure ASGI so streaming responses (MCP, the gateway) pass through untouched."""

    HTML_CSP = (b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                b"connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
                b"frame-ancestors 'none'")

    def __init__(self, app: ASGIApp, https: bool):
        self.app = app
        self.headers = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            # Not "no-referrer": with it, browsers send "Origin: null" on our own form posts and the cross-site
            # check refuses them. "same-origin" keeps one-time links from leaking to other sites.
            (b"referrer-policy", b"same-origin"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        ]
        if https:
            self.headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                content_type = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                csp = self.HTML_CSP if content_type.startswith(b"text/html") else b"default-src 'none'; frame-ancestors 'none'"
                message["headers"] = headers + self.headers + [(b"content-security-policy", csp)]
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


class PerRequestSessions:
    """Stateless MCP that needs no startup hook.

    FastMCP normally starts one session manager in the app's lifespan. Serverless hosts (Vercel) may never
    run lifespan events, so each request gets its own short-lived manager instead. In stateless mode nothing
    is shared between requests anyway.
    """

    def __init__(self, server: Any, security: TransportSecuritySettings):
        self.server, self.security = server, security

    @asynccontextmanager
    async def run(self):
        yield

    async def handle_request(self, scope: Scope, receive: Receive, send: Send) -> None:
        manager = StreamableHTTPSessionManager(app=self.server, stateless=True, json_response=True,
                                               security_settings=self.security)
        async with manager.run():
            await manager.handle_request(scope, receive, send)


class Dispatcher:
    """Routes by path family and establishes who is asking before anything else runs."""

    def __init__(self, site: ASGIApp, app_api: ASGIApp, key_api: ASGIApp, gateway: Gateway,
                 accounts: AccountStore, settings: Settings):
        self.site, self.app_api, self.key_api = site, app_api, key_api
        self.gateway, self.accounts, self.settings = gateway, accounts, settings
        self.key_limiter = gateway.limiter  # one count per key across the REST API and the gateway

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.site(scope, receive, send)
        path = scope.get("path", "")
        if path.startswith("/v1/"):
            return await self.with_key(scope, receive, send, path)
        if path in ("/app", "/app/") or path.startswith("/api/"):
            return await self.with_session(scope, receive, send, path)
        await self.site(scope, receive, send)

    # A person in a browser ---------------------------------------------------------------

    def session_principal(self, request: Request) -> Principal | None:
        user = self.accounts.session_user(request.cookies.get(SESSION_COOKIE))
        if user is None:
            return None
        spaces = self.accounts.workspaces_for(user["id"]) or [
            {**self.accounts.ensure_personal_workspace(user), "role": "owner"}]
        wanted = request.headers.get("x-nexus-workspace")
        chosen = next((w for w in spaces if wanted in (w["id"], w["slug"])), None) if wanted else spaces[0]
        if chosen is None:
            raise PermissionDenied("You are not a member of that workspace.")
        return Principal(channel="app", actor=user["email"], workspace=self.accounts.get_workspace(chosen["id"]),
                         role=chosen["role"], user=user)

    async def with_session(self, scope: Scope, receive: Receive, send: Send, path: str) -> None:
        request = Request(scope)
        try:
            principal = await run_in_threadpool(self.session_principal, request)
        except PermissionDenied as e:
            return await JSONResponse({"error": str(e)}, status_code=403)(scope, receive, send)
        if principal is None:
            response: Response = (RedirectResponse("/login?next=/app", status_code=303) if path.startswith("/app")
                                  else JSONResponse({"error": "Your session ended. Sign in again."}, status_code=401))
            return await response(scope, receive, send)
        if scope["method"] in WRITES:
            # Another site cannot add this header without a CORS preflight, which is never granted; and when the
            # browser says where the request came from, it must be here.
            origin = request.headers.get("origin")
            if request.headers.get("x-nexus") != "1" or (origin and origin != self.settings.origin):
                return await JSONResponse({"error": "Cross-site request refused."}, status_code=403)(scope, receive, send)
        token = set_current(principal)
        try:
            await self.app_api(scope if path != "/app/" else {**scope, "path": "/app"}, receive, send)
        finally:
            reset_current(token)

    # A program with an API key -----------------------------------------------------------

    async def with_key(self, scope: Scope, receive: Receive, send: Send, path: str) -> None:
        request = Request(scope)
        if path in GATEWAY_PATHS and scope["method"] == "POST":
            return await (await self.gateway.handle(Request(scope, receive), GATEWAY_PATHS[path]))(scope, receive, send)
        if path == "/v1/models" and scope["method"] == "GET":
            return await (await self.gateway.models(request))(scope, receive, send)

        def resolve() -> Principal | JSONResponse:
            bearer = request.headers.get("authorization", "")
            secret = bearer[7:].strip() if bearer.lower().startswith("bearer ") else request.headers.get("x-nexus-key", "")
            key = self.accounts.lookup_api_key(secret)
            if not key:
                return JSONResponse({"error": "Send a NEXUS API key as 'Authorization: Bearer nxk_...'."},
                                    status_code=401, headers={"www-authenticate": "Bearer"})
            if "memory" not in key["scopes"]:
                return JSONResponse({"error": "This key does not have the memory scope."}, status_code=403)
            ws = self.accounts.get_workspace(key["workspace_id"])
            if not self.key_limiter.allow(key["id"], ws["limits"]["api_requests_minute"]):
                return JSONResponse({"error": "Too many requests on this key in the last minute."}, status_code=429,
                                    headers={"retry-after": "30"})
            return Principal(channel="api", actor=f"key:{key['name']}", workspace=ws, role=key["role"], key=key)

        principal = await run_in_threadpool(resolve)
        if isinstance(principal, JSONResponse):
            return await principal(scope, receive, send)
        status = {"code": 0}

        async def watch(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        token, started = set_current(principal), time.monotonic()
        try:
            await self.key_api(scope, receive, watch)
        finally:
            reset_current(token)
            route = scope.get("route")
            operation = f"{scope['method']} {getattr(route, 'path', path)}"
            await run_in_threadpool(
                lambda: self.accounts.record_usage(principal.workspace_id, channel="api", operation=operation[:120],
                                                   actor=principal.actor, status=status["code"],
                                                   latency_ms=int((time.monotonic() - started) * 1000)))


def create_hosted_app(location: str, public_url: str, *, signup_code: str | None = None,
                      accounts: AccountStore | None = None, settings: Settings | None = None
                      ) -> tuple[ASGIApp, AccountStore]:
    """``location`` is a data folder or a Postgres URL. Returns the ASGI app and its account store."""
    if settings is None:
        settings = Settings.from_env(public_url=public_url, location=location)
        if signup_code:
            settings.signup, settings.signup_code = "invite", signup_code
    accounts = accounts or AccountStore(settings.location, allow_insecure_upstreams=settings.allow_insecure_upstreams)
    public_url, origin, https = settings.public_url, settings.origin, settings.https
    parsed = urlparse(public_url)
    provider = NexusOAuthProvider(accounts, public_url)
    mailer = Mailer(accounts, settings.smtp_url, settings.mail_from)
    gateway = Gateway(accounts, settings)
    limiter = RateLimiter()
    beta_limiter = RateLimiter(limit=5, window=3600)
    signup_limiter = RateLimiter(limit=20, window=3600)

    # ------------------------------------------------------ MCP: a connected app

    def grant() -> dict[str, Any]:
        token = get_access_token()
        if token is None or not token.subject or not (token.claims or {}).get("workspace_id"):
            raise PermissionDenied("Not signed in.")  # the auth middleware rejects these before any tool runs
        return token.claims

    def mcp_store():
        return accounts.memory_for(grant()["workspace_id"])

    def mcp_gate(tool: str, writes: bool) -> None:
        g = grant()
        if writes and not g["can_write"]:
            raise PermissionDenied(
                "This connection can only read. " + ("It was connected as read-only." if g["access"] == "read" else
                                                     f"Your role in this workspace ({g['role']}) cannot change records."))
        if tool in ("create_project", "save_memory", "import_source"):
            ws = accounts.get_workspace(g["workspace_id"])
            require_under_limit_hook(accounts, ws, "projects" if tool == "create_project" else "records")
        accounts.record_usage(g["workspace_id"], channel="mcp", operation=tool, actor=f"{g['email']} via {g['client_name']}")

    def mcp_actor(model: str) -> str:
        g = grant()
        return f"{model or 'model'} · {g['client_name']} · {g['email']}"[:100]

    def mcp_receipt(snapshot: dict[str, Any], ctx: dict[str, Any], query: str | None) -> str:
        g, recs = grant(), snapshot["records"]
        ids = [item["id"] for section in ctx.values() if isinstance(section, list)
               for item in section if isinstance(item, dict) and item.get("id") in recs]
        refs = [{"id": i, "version": recs[i]["version"], "status": recs[i]["status"], "kind": recs[i]["kind"],
                 "confirmed": bool(recs[i].get("confirmed_by"))} for i in dict.fromkeys(ids)]
        text = json.dumps({k: v for k, v in ctx.items() if k != "receipt_id"}, sort_keys=True, default=str)
        return accounts.save_receipt(
            g["workspace_id"], project_id=snapshot["project"]["id"], project_name=snapshot["project"]["name"],
            channel="mcp", briefing=text, record_refs=refs, token_estimate=(len(text) + 3) // 4,
            actor=f"{g['email']} via {g['client_name']}", query=query)

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[parsed.netloc, f"{parsed.hostname}:*"],
        allowed_origins=[origin],
    )
    mcp: FastMCP = create_server(
        store_for_request=mcp_store, gate=mcp_gate, actor=mcp_actor, on_context=mcp_receipt,
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
        transport_security=security,
    )
    mcp._session_manager = PerRequestSessions(mcp._mcp_server, security)  # SDK 1.x attribute; see the class

    # ------------------------------------- memory API hooks for /api and /v1

    def store_for_request():
        return accounts.memory_for(current().workspace_id)

    def authorize(action: str) -> None:
        p = current()
        p.require(NEEDS[action], {"delete_project": "delete a project", "delete_record": "delete a record"}
                  .get(action, "change records"))
        if action in ("create_project", "create_record"):
            require_under_limit_hook(accounts, p.workspace, "projects" if action == "create_project" else "records")

    def on_briefing(out: dict[str, Any], query: str | None = None) -> str:
        p = current()
        return accounts.save_receipt(
            p.workspace_id, project_id=out["project"]["id"], project_name=out["project"]["name"], channel=p.channel,
            briefing=out["text"], record_refs=out["record_refs"], token_estimate=out["token_estimate"],
            actor=p.actor, query=query)

    def hosted_context() -> dict[str, Any]:
        p = current()
        return {"workspace": {"id": p.workspace_id, "name": p.workspace["name"], "slug": p.workspace["slug"]},
                "role": p.role, "actor": p.actor, "channel": p.channel,
                "key": {"name": p.key["name"], "scopes": p.key["scopes"]} if p.key else None}

    hooks = dict(store_for_request=store_for_request, authorize=authorize, actor=lambda: current().actor,
                 human=lambda: current().human, on_briefing=on_briefing,
                 hosted={"mcp_url": f"{public_url}/mcp", "context": hosted_context})
    memory_app = create_memory_api(prefix="/api", **hooks)
    key_api = create_memory_api(prefix="/v1", **hooks)

    async def key_receipt(request: Request) -> Response:
        return JSONResponse(accounts.get_receipt(current().workspace_id, request.path_params["rid"]))

    key_api.router.routes.insert(0, Route("/v1/receipts/{rid}", key_receipt))

    async def app_shell(request: Request) -> Response:
        page = (pages.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(page.replace("{{BRAND_NAME}}", pages.e(BRAND["name"])), headers={"cache-control": "no-cache"})

    async def api_error(request: Request, exc: Exception) -> Response:
        status = (404 if isinstance(exc, NotFound) else 403 if isinstance(exc, PermissionDenied)
                  else 402 if isinstance(exc, LimitReached) else 400)
        return JSONResponse({"error": str(exc)}, status_code=status)

    app_api = Starlette(
        routes=[Route("/app", app_shell),
                *account_routes(accounts, settings, gateway, mailer, client_ip, load_demo),
                *memory_app.router.routes],
        exception_handlers={NotFound: api_error, PermissionDenied: api_error, LimitReached: api_error,
                            ValidationError: api_error})

    # ------------------------------------------------------------ public pages

    site_values = {"BRAND_NAME": pages.e(BRAND["name"]), "PUBLIC_URL": pages.e(public_url),
                   "MCP_URL": pages.e(f"{public_url}/mcp"), "API_URL": pages.e(f"{public_url}/v1"),
                   "GATEWAY_URL": pages.e(public_url), "VERSION": __version__, "MARK": pages.MARK,
                   "CLAUDE_CODE_CMD": pages.e(f"claude mcp add --transport http {BRAND['mcp_server_name']} {public_url}/mcp"),
                   "SIGNUP_HREF": "/signup" if settings.signup != "closed" else "/login"}

    def session_user(request: Request) -> dict[str, Any] | None:
        return accounts.session_user(request.cookies.get(SESSION_COOKIE))

    def same_site(request: Request) -> bool:
        return request.headers.get("origin") in (None, origin)

    def safe_next(value: str | None) -> str:
        """Only paths on this site that a sign-in may lead to."""
        if value and (value == "/app" or value.startswith(("/app#", "/app/", "/invite/", "/consent?request="))):
            return value
        return "/app"

    def start_session(user: dict[str, Any], to: str) -> Response:
        response = RedirectResponse(to, status_code=303)
        response.set_cookie(SESSION_COOKIE, accounts.create_session(user["id"]), max_age=SESSION_TTL,
                            httponly=True, secure=https, samesite="lax", path="/")
        return response

    def site_page(name: str):
        async def page(request: Request) -> Response:
            slug = request.path_params.get("slug")
            html_ = pages.render_site(f"{name}/{slug}" if slug else name, site_values)
            if html_ is None:
                missing = pages.render_site("404", site_values) or "Not found"
                return HTMLResponse(missing, status_code=404)
            return HTMLResponse(html_, headers={"cache-control": "no-cache"})
        return page

    for path, name in (("/", "index"), ("/pricing", "pricing"), ("/security", "security"),
                       ("/changelog", "changelog"), ("/docs", "docs/index"), ("/docs/{slug}", "docs")):
        mcp.custom_route(path, methods=["GET"])(site_page(name))

    @mcp.custom_route("/static/{path:path}", methods=["GET"])
    async def static(request: Request) -> Response:
        rel = request.path_params["path"]
        target = (pages.STATIC_DIR / rel).resolve()
        if (not rel.startswith(PUBLIC_STATIC) or pages.STATIC_DIR not in target.parents or not target.is_file()
                or target.name.startswith("_")):
            return Response("Not found", status_code=404)
        cache = "public, max-age=31536000, immutable" if rel.startswith("fonts/") else "no-cache"
        return FileResponse(target, headers={"cache-control": cache})

    @mcp.custom_route("/healthz", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True, "version": __version__})

    @mcp.custom_route("/readyz", methods=["GET"])
    async def ready(request: Request) -> Response:
        """Can this instance reach its database and are its migrations applied?"""
        try:
            from . import migrate
            state = await run_in_threadpool(migrate.status, accounts)
        except Exception as e:
            return JSONResponse({"ok": False, "error": type(e).__name__}, status_code=503)
        ok = not state["pending"]
        return JSONResponse({"ok": ok, "migrations": len(state["applied"]), "pending": state["pending"],
                             "secrets": accounts.keyring is not None}, status_code=200 if ok else 503)

    @mcp.custom_route("/beta", methods=["POST"])
    async def beta(request: Request) -> Response:
        if not same_site(request):
            return JSONResponse({"error": "Cross-site request refused."}, status_code=403)
        is_json = request.headers.get("content-type", "").startswith("application/json")
        try:
            data = await request.json() if is_json else dict(await request.form())
        except ValueError:
            return JSONResponse({"error": "Send the form again."}, status_code=400)
        ip = client_ip(request)
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

    # ------------------------------------------------------ sign in and sign up

    def register(form: Any, ip: str) -> dict[str, Any]:
        """Create an account from a sign-up form. Raises ValidationError with a message for the person."""
        token = str(form.get("invitation", ""))
        inv = accounts.find_invitation(token) if token else None
        if token and not inv:
            raise ValidationError("This invitation has expired or was already used. Ask for a new one.")
        if not inv:
            if settings.signup == "closed":
                raise ValidationError("New accounts on this server are created from a workspace invitation.")
            if settings.signup == "invite" and not hmac.compare_digest(
                    str(form.get("code", form.get("invite", ""))).encode(), (settings.signup_code or "").encode()):
                limiter.fail(f"ip:{ip}")
                raise ValidationError("That invite code isn't valid.")
        if signup_limiter.blocked(f"signup:{ip}"):
            raise ValidationError("Too many accounts were created from this network. Try again in an hour.")
        email = inv["email"] if inv else str(form.get("email", ""))
        user = accounts.create_user(email, str(form.get("password", "")), str(form.get("name", "")))
        signup_limiter.fail(f"signup:{ip}")
        accounts.audit("account.created", actor=user["email"], user_id=user["id"], ip=ip,
                       detail={"invited": bool(inv)})
        if inv:
            ws = accounts.accept_invitation(token, user)
            accounts.audit("member.joined", actor=user["email"], workspace_id=ws["id"], user_id=user["id"], ip=ip)
        else:
            name = str(form.get("workspace", "")).strip() or \
                f"{(user['name'] or user['email'].split('@')[0]).split()[0]}'s workspace"
            ws = accounts.create_workspace(name[:80], user["id"])
            link = f"{public_url}/verify/{accounts.create_email_token(user['id'], 'verify')}"
            mailer.send(user["email"], f"Confirm your email for {BRAND['name']}",
                        f"Welcome to {BRAND['name']}.\n\nConfirm this address:\n{link}\n\nThe link works once and "
                        "expires in 3 days.")
        return user

    @mcp.custom_route("/login", methods=["GET", "POST"])
    async def login(request: Request) -> Response:
        signup_open = settings.signup != "closed"
        if request.method == "GET":
            next_ = safe_next(request.query_params.get("next"))
            if session_user(request):
                return RedirectResponse(next_, status_code=303)
            if request.query_params.get("mode") == "signup":
                return RedirectResponse("/signup", status_code=303)
            return HTMLResponse(pages.login_page(next_=next_, signup_open=signup_open,
                                                 notice="Password changed. Sign in with the new one."
                                                 if request.query_params.get("reset") else None))
        if not same_site(request):
            return HTMLResponse("Cross-site request refused.", status_code=403)
        form = await request.form()
        if form.get("mode") == "signup":  # older links posted sign-ups here
            return await signup_post(request, form)
        email, next_ = str(form.get("email", "")).strip(), safe_next(str(form.get("next", "")))
        ip = client_ip(request)
        keys = (f"ip:{ip}", f"email:{email.lower()}")

        def again(message: str, status: int = 400) -> Response:
            return HTMLResponse(pages.login_page(error=message, email=email, next_=next_, signup_open=signup_open),
                                status_code=status)

        if limiter.blocked(*keys):
            return again("Too many attempts. Wait 15 minutes and try again.", 429)
        user = await run_in_threadpool(accounts.authenticate, email, str(form.get("password", "")))
        if not user:
            limiter.fail(*keys)
            return again("Email or password is incorrect.")
        accounts.ensure_personal_workspace(user)
        return start_session(user, next_)

    async def signup_post(request: Request, form: Any) -> Response:
        next_ = safe_next(str(form.get("next", "")))
        token = str(form.get("invitation", ""))
        ip = client_ip(request)
        if limiter.blocked(f"ip:{ip}"):
            return HTMLResponse(pages.message_page("Create an account", "Too many attempts",
                                                   "Wait 15 minutes and try again."), status_code=429)
        try:
            user = await run_in_threadpool(register, form, ip)
        except ValidationError as err:
            inv = accounts.find_invitation(token) if token else None
            return HTMLResponse(pages.signup_page(
                error=str(err), email=str(form.get("email", "")), name=str(form.get("name", "")),
                workspace=str(form.get("workspace", "")), next_=next_, needs_code=settings.signup == "invite",
                invitation=inv, invitation_token=token if inv else "", closed=settings.signup == "closed"),
                status_code=400)
        return start_session(user, next_)

    @mcp.custom_route("/signup", methods=["GET", "POST"])
    async def signup(request: Request) -> Response:
        if request.method == "POST":
            if not same_site(request):
                return HTMLResponse("Cross-site request refused.", status_code=403)
            return await signup_post(request, await request.form())
        next_ = safe_next(request.query_params.get("next"))
        if session_user(request):
            return RedirectResponse(next_, status_code=303)
        token = request.query_params.get("invitation", "")
        inv = accounts.find_invitation(token) if token else None
        return HTMLResponse(pages.signup_page(next_=next_, needs_code=settings.signup == "invite", invitation=inv,
                                              invitation_token=token if inv else "",
                                              closed=settings.signup == "closed"))

    @mcp.custom_route("/logout", methods=["POST"])
    async def logout(request: Request) -> Response:
        if not same_site(request):
            return HTMLResponse("Cross-site request refused.", status_code=403)
        accounts.end_session(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @mcp.custom_route("/forgot", methods=["GET", "POST"])
    async def forgot(request: Request) -> Response:
        if request.method == "GET":
            return HTMLResponse(pages.forgot_page())
        if not same_site(request):
            return HTMLResponse("Cross-site request refused.", status_code=403)
        ip = client_ip(request)
        if limiter.blocked(f"forgot:{ip}"):
            return HTMLResponse(pages.forgot_page(error="Too many requests. Wait 15 minutes and try again."), status_code=429)
        limiter.fail(f"forgot:{ip}")
        user = accounts.find_user(str((await request.form()).get("email", "")))
        link = None
        if user:  # the page says the same thing either way, so it does not reveal who has an account
            link = f"{public_url}/reset/{accounts.create_email_token(user['id'], 'reset')}"
            mailer.send(user["email"], f"Reset your {BRAND['name']} password",
                        f"Someone asked to reset the password for this address.\n\nChoose a new password:\n{link}\n\n"
                        "The link works once and expires in an hour. If this was not you, ignore this message.")
            accounts.audit("account.reset_requested", actor=user["email"], user_id=user["id"], ip=ip)
        show = link if settings.is_local and not mailer.configured else None
        return HTMLResponse(pages.forgot_page(sent=True, link=show, mail=mailer.configured))

    @mcp.custom_route("/reset/{token}", methods=["GET", "POST"])
    async def reset(request: Request) -> Response:
        token = request.path_params["token"]
        expired = pages.message_page("Link expired", "This link has expired",
                                     "Password links work once and last an hour. Ask for a new one.",
                                     ("/forgot", "Send a new link"))
        if not accounts.peek_email_token(token, "reset"):
            return HTMLResponse(expired, status_code=400)
        if request.method == "GET":
            return HTMLResponse(pages.reset_page(token))
        if not same_site(request):
            return HTMLResponse("Cross-site request refused.", status_code=403)
        password = str((await request.form()).get("password", ""))
        try:
            from .accounts import check_new_password
            check_new_password(password)
        except ValidationError as err:
            return HTMLResponse(pages.reset_page(token, error=str(err)), status_code=400)
        user_id = accounts.take_email_token(token, "reset")
        if not user_id:
            return HTMLResponse(expired, status_code=400)
        await run_in_threadpool(accounts.set_password, user_id, password)
        accounts.mark_verified(user_id)  # they received mail at this address
        accounts.audit("account.password_reset", actor=accounts.get_user(user_id)["email"], user_id=user_id,
                       ip=client_ip(request))
        return RedirectResponse("/login?reset=1", status_code=303)

    @mcp.custom_route("/verify/{token}", methods=["GET"])
    async def verify(request: Request) -> Response:
        user_id = accounts.take_email_token(request.path_params["token"], "verify")
        if not user_id:
            return HTMLResponse(pages.message_page("Link expired", "This link has expired",
                                                   "It may already have been used.", ("/app", "Open the app")),
                                status_code=400)
        accounts.mark_verified(user_id)
        return HTMLResponse(pages.message_page("Email confirmed", "Email confirmed",
                                               "Thank you. Your address is confirmed.", ("/app", "Open the app")))

    @mcp.custom_route("/invite/{token}", methods=["GET", "POST"])
    async def invite(request: Request) -> Response:
        token = request.path_params["token"]
        inv = accounts.find_invitation(token)
        if not inv:
            return HTMLResponse(pages.message_page(
                "Invitation", "This invitation is no longer valid",
                "It expired, was withdrawn or was already used. Ask the person who invited you for a new one.",
                ("/login", "Sign in")), status_code=400)
        user = session_user(request)
        if request.method == "GET":
            return HTMLResponse(pages.invite_page(token, inv, user))
        if not same_site(request):
            return HTMLResponse("Cross-site request refused.", status_code=403)
        if not user:
            return RedirectResponse(f"/login?next=/invite/{token}", status_code=303)
        try:
            ws = accounts.accept_invitation(token, user)
        except (ValidationError, PermissionDenied) as err:
            return HTMLResponse(pages.invite_page(token, inv, user, error=str(err)), status_code=400)
        accounts.audit("member.joined", actor=user["email"], workspace_id=ws["id"], user_id=user["id"],
                       ip=client_ip(request))
        return RedirectResponse("/app", status_code=303)

    # -------------------------------------------------------- connecting an app

    @mcp.custom_route("/consent", methods=["GET", "POST"])
    async def consent(request: Request) -> Response:
        signup_open = settings.signup != "closed"
        needs_code = settings.signup == "invite"
        expired = pages.message_page("Link expired", "This sign-in link has expired",
                                     "Go back to your AI app and connect again. Sign-in links last 15 minutes and work once.")
        user = session_user(request)

        def page(req: dict[str, Any], mode: str, error: str | None = None, email: str = "", status: int = 200) -> Response:
            return HTMLResponse(pages.consent_page(
                req, accounts.get_client(req["client_id"]), user=user,
                workspaces=accounts.workspaces_for(user["id"]) if user else [], mode=mode, needs_code=needs_code,
                signup_open=signup_open, error=error, email=email), status_code=status)

        if request.method == "GET":
            req = accounts.get_request(request.query_params.get("request", ""))
            if not req:
                return HTMLResponse(expired, status_code=400)
            return page(req, "signup" if request.query_params.get("mode") == "signup" else "signin")

        if not same_site(request):
            return HTMLResponse("Cross-site request refused.", status_code=403)
        form = await request.form()
        rid = str(form.get("request", ""))
        mode = "signup" if form.get("mode") == "signup" else "signin"
        req = accounts.get_request(rid)
        if not req:
            return HTMLResponse(expired, status_code=400)
        client = accounts.get_client(req["client_id"])
        params = req["params"]

        if form.get("action") == "deny":
            if accounts.take_request(rid):
                return RedirectResponse(construct_redirect_uri(params["redirect_uri"], error="access_denied",
                                                               state=params["state"]), status_code=302)
            return HTMLResponse(expired, status_code=400)

        ip = client_ip(request)
        if not user:
            email = str(form.get("email", "")).strip()
            keys = (f"ip:{ip}", f"email:{email.lower()}")
            if limiter.blocked(*keys):
                return page(req, mode, "Too many attempts. Wait 15 minutes and try again.", email, 429)
            if mode == "signup":
                try:
                    user = await run_in_threadpool(register, form, ip)
                except ValidationError as err:
                    return page(req, mode, str(err), email, 400)
            else:
                user = await run_in_threadpool(accounts.authenticate, email, str(form.get("password", "")))
                if not user:
                    limiter.fail(*keys)
                    return page(req, mode, "Email or password is incorrect.", email, 400)

        spaces = accounts.workspaces_for(user["id"]) or [accounts.ensure_personal_workspace(user)]
        wanted = str(form.get("workspace", ""))
        ws = next((w for w in spaces if w["id"] == wanted), None) if wanted else spaces[0]
        if ws is None:
            return page(req, mode, "Choose one of your workspaces.", status=400)
        access = "read" if form.get("access") == "read" else "write"
        if not accounts.take_request(rid):
            return HTMLResponse(expired, status_code=400)
        code = accounts.issue_code(user["id"], req["client_id"], (client or {}).get("client_name"), params,
                                   workspace_id=ws["id"], access=access)
        accounts.audit("connection.approved", actor=user["email"], workspace_id=ws["id"], user_id=user["id"],
                       target=(client or {}).get("client_name") or req["client_id"], detail={"access": access}, ip=ip)
        response = RedirectResponse(construct_redirect_uri(params["redirect_uri"], code=code, state=params["state"]),
                                    status_code=302)
        return response  # signing in here approves one app; it does not start a browser session

    site = WellKnown(mcp.streamable_http_app(), public_url)
    app = SecurityHeaders(Dispatcher(site, app_api, key_api, gateway, accounts, settings), https=https)
    return app, accounts


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description=f"Hosted {BRAND['name']} service")
    ap.add_argument("--public-url", default=os.environ.get("NEXUS_PUBLIC_URL"),
                    help="Public https:// address users connect to ($NEXUS_PUBLIC_URL)")
    ap.add_argument("--data-dir", default=None,
                    help="Folder for SQLite files ($NEXUS_DATA_DIR). Ignored when NEXUS_DATABASE_URL is set.")
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = ap.parse_args(argv)
    try:
        settings = Settings.from_env(public_url=args.public_url, location=args.data_dir)
    except ValueError as e:
        ap.error(str(e))
    app, _ = create_hosted_app(settings.location, settings.public_url, settings=settings)
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, log_level="info")


if __name__ == "__main__":
    main()
