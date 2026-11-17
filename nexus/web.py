"""The memory API (projects, records, review, briefing, check), and the local dashboard that serves it.

The hosted service mounts these same handlers under /api and /v1 with hooks for workspace, role and actor.
Handlers are plain functions run in worker threads, so database work never blocks the event loop.

    nexus web                 # http://127.0.0.1:8765

It opens the same SQLite file as the MCP server, so anything an AI client saves
through MCP shows up here, and the /api/events stream tells the open page when
that happens. It binds to 127.0.0.1 only and refuses requests whose Host header
is not local (DNS-rebinding guard). Writes must carry the X-Nexus header,
which a cross-site form or fetch cannot add without a CORS preflight this
server never approves.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
from collections import Counter
from importlib import resources
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__
from . import models as m
from .brand import BRAND
from .briefing import DEFAULT_BUDGET, build_briefing
from .check import check_statements
from .context import build_project_context
from .db import default_db_path
from .gaps import analyze_gaps
from .importer import import_text
from .store import MemoryStore

ACTOR = "dashboard"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
WRITE_HEADER = "x-nexus"


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


class LocalOnly:
    """Pure ASGI middleware (keeps the event stream unbuffered)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            host = headers.get("host", "").rsplit(":", 1)[0]
            error = None
            if host not in LOCAL_HOSTS:
                error = "This dashboard only answers on localhost."
            elif scope["method"] in ("POST", "PATCH", "PUT", "DELETE") and headers.get(WRITE_HEADER) != "1":
                error = "Missing X-Nexus header."
            if error:
                await _json({"error": error}, 403)(scope, receive, send)
                return
            if scope["path"].startswith("/static/"):
                # Revalidate every load so an upgraded dashboard never runs stale modules.
                async def send_no_cache(message):
                    if message["type"] == "http.response.start":
                        message.setdefault("headers", []).append((b"cache-control", b"no-cache"))
                    await send(message)
                await self.app(scope, receive, send_no_cache)
                return
        await self.app(scope, receive, send)


async def _not_found(request: Request, exc: Exception) -> JSONResponse:
    return _json({"error": str(exc)}, 404)


async def _invalid(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, (TypeError, AttributeError)):  # a field of the wrong type, not a server fault
        return _json({"error": "A field in the request has the wrong type."}, 400)
    return _json({"error": str(exc)}, 400)


def _body(request: Request) -> dict[str, Any]:
    try:
        data = json.loads(request.state.raw or b"{}")
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise m.ValidationError("Request body must be JSON")
    if not isinstance(data, dict):
        raise m.ValidationError("Request body must be a JSON object")
    return data


def _gap_brief(g: dict[str, Any]) -> dict[str, Any]:
    return {k: g[k] for k in ("gap_id", "type", "severity", "confidence", "summary", "record_ids", "records",
                                    "suggested_action")}


def create_app(db_path: str | None = None, store: MemoryStore | None = None, poll_seconds: float = 1.0, *,
               store_for_request: Callable[[], MemoryStore] | None = None,
               hosted: dict[str, Any] | None = None, prefix: str = "/api",
               authorize: Callable[[str], None] | None = None, actor: Callable[[], str] | None = None,
               human: Callable[[], str | None] | None = None,
               on_briefing: Callable[..., str | None] | None = None) -> Starlette:
    """The memory API. Local use: one database, localhost only, a live event stream.

    The hosted service mounts this twice, for signed-in browsers (``/api``) and for API keys (``/v1``), and
    passes hooks that tie each request to its workspace and role:

      ``store_for_request``  the current workspace's memory
      ``authorize(action)``  raises unless the caller may ``write``, ``create_project``, ``create_record``,
                             ``delete_record`` or ``delete_project``
      ``actor()``            who to record in history
      ``human()``            a signed-in person's name, or None for keys and apps (only people confirm records)
      ``on_briefing(...)``   stores a context receipt and returns its id

    Hosted pages poll /api/revision instead of holding a stream open (serverless hosts cut those off).
    """
    authorize = authorize or (lambda action: None)
    actor = actor or (lambda: ACTOR)
    human = human or (lambda: "local user")
    if store_for_request is not None:
        class _Current:  # resolves to the signed-in account's memory on every call
            def __getattr__(self, name: str) -> Any:
                return getattr(store_for_request(), name)
        store = _Current()  # type: ignore[assignment]
    else:
        store = store or MemoryStore(db_path)
    static_dir = resources.files("nexus").joinpath("static")

    def project_summary(p: dict[str, Any]) -> dict[str, Any]:
        snap = store.project_snapshot(p["id"])
        statuses = Counter(r["status"] for r in snap["records"].values())
        gaps = analyze_gaps(snap)
        return {
            **p,
            "status_counts": dict(statuses),
            "models": sorted({r["origin_model"] for r in snap["records"].values() if r["origin_model"]}),
            "gap_counts": gaps["counts"],
            "high_gaps": sum(1 for g in gaps["gaps"] if g["severity"] == "high"),
        }

    # ------------------------------------------------------------- handlers

    async def index(request: Request) -> HTMLResponse:
        page = static_dir.joinpath("index.html").read_text(encoding="utf-8")
        return HTMLResponse(page.replace("{{BRAND_NAME}}", html.escape(BRAND["name"])))

    def meta(request: Request) -> JSONResponse:
        mcp = {
            "server_name": BRAND["mcp_server_name"],
            "tools": ["get_project_context", "search_memory", "get_memory_details", "save_memory",
                      "update_memory", "analyze_gaps", "check_plan", "import_source", "create_project",
                      "list_projects", "get_review_instructions"],
            "prompts": ["review_project"],
        }
        if hosted:
            mcp["url"] = hosted["mcp_url"]
            where = {"live": "poll", **hosted["context"]()}
        else:
            mcp["stdio_command"] = f"{sys.executable} -m nexus.server --db {store.path}"
            where = {"db_path": store.path, "live": "stream"}
        return _json({
            "brand": BRAND, "version": __version__, **where, "mcp": mcp,
            "vocabulary": {
                "kinds": m.RECORD_KINDS, "statuses": m.STATUSES, "verification": m.VERIFICATION,
                "lifecycle": m.LIFECYCLE, "relation_types": m.RELATION_TYPES, "importance": list(m.IMPORTANCE),
            },
        })

    def projects(request: Request) -> JSONResponse:
        if request.method == "POST":
            authorize("create_project")
            b = _body(request)
            return _json(store.create_project(b.get("name", ""), b.get("description", ""), b.get("objective", "")), 201)
        return _json({"projects": [project_summary(p) for p in store.list_projects()]})

    def project(request: Request) -> JSONResponse:
        ref = request.path_params["pid"]
        if request.method == "DELETE":
            authorize("delete_project")
            return _json(store.delete_project(ref))
        if request.method == "PATCH":
            authorize("write")
            b = _body(request)
            return _json(store.update_project(ref, description=b.get("description"), objective=b.get("objective")))
        snap = store.project_snapshot(ref)
        ctx = build_project_context(snap, max_per_section=8)
        gaps = analyze_gaps(snap)
        recs = snap["records"].values()
        return _json({
            "project": snap["project"],
            "status_counts": dict(Counter(r["status"] for r in recs)),
            "kind_counts": dict(Counter(r["kind"] for r in recs if r["lifecycle"] == "active")),
            "model_counts": dict(Counter(r["origin_model"] or "unknown" for r in recs)),
            "record_total": len(snap["records"]),
            "relation_total": len(snap["relations"]),
            "source_total": len(snap["sources"]),
            "confirmed_total": sum(1 for r in recs if r["lifecycle"] == "active" and r.get("confirmed_by")),
            "decisions": [{**d, "confirmed": bool(snap["records"][d["id"]].get("confirmed_by"))}
                          for d in ctx.get("decisions", [])],
            "open_questions": ctx.get("open_questions", []),
            "gaps": [_gap_brief(g) for g in gaps["gaps"]],
            "gap_counts": gaps["counts"],
            "activity": store.activity(ref, limit=12),
        })

    def records(request: Request) -> JSONResponse:
        ref = request.path_params["pid"]
        if request.method == "POST":
            authorize("create_record")
            b = _body(request)
            rec = store.save_record(
                ref, kind=b.get("kind", "note"), title=b.get("title", ""), content=b.get("content", ""),
                verification=b.get("verification", "unverified"), importance=b.get("importance", "normal"),
                subject=b.get("subject") or None, value=b.get("value") or None, tags=b.get("tags") or [],
                # "human" is reserved for a signed-in person; a program that names no model is recorded as itself
                origin_model=b.get("origin_model") or ("human" if human() else actor()), created_by=actor(),
                source_text=b.get("source_text") or None, evidence=b.get("evidence") or [],
                relations=b.get("relations") or [],
            )
            return _json(rec, 201)
        snap = store.project_snapshot(ref)
        q = request.query_params
        matched = None
        if q.get("q"):
            hits = store.search(q["q"], project=ref, include_inactive=True, include_sources=False, limit=100)
            matched = {h["id"]: i for i, h in enumerate(hits["results"])}
        links = Counter()
        for rel in snap["relations"]:
            links[rel["from_id"]] += 1
            links[rel["to_id"]] += 1
        rows = []
        for r in snap["records"].values():
            if matched is not None and r["id"] not in matched:
                continue
            if q.get("kind") and r["kind"] != q["kind"]:
                continue
            if q.get("status") and r["status"] != q["status"]:
                continue
            if q.get("model") and (r["origin_model"] or "unknown") != q["model"]:
                continue
            if q.get("inactive") != "1" and not q.get("status") and r["lifecycle"] != "active":
                continue
            rows.append({
                k: r[k] for k in ("id", "kind", "title", "status", "importance", "importance_label", "origin_model",
                                  "evidence_count", "updated_at", "created_at", "superseded_by", "subject", "value")
            } | {"links": links[r["id"]], "rank": matched.get(r["id"]) if matched else None})
        return _json({"records": rows})

    def record(request: Request) -> JSONResponse:
        rid = request.path_params["rid"]
        if request.method == "DELETE":
            authorize("delete_record")
            return _json(store.delete_record(rid))
        if request.method == "PATCH":
            authorize("write")
            b = _body(request)
            allowed = {"title", "content", "verification", "lifecycle", "importance", "subject", "value", "tags",
                       "superseded_by", "add_evidence", "add_relations"}
            kw = {k: v for k, v in b.items() if k in allowed}
            if "confirm" in b:  # the trust tier: only a signed-in person can vouch for a record
                person = human()
                if person is None:
                    raise m.ValidationError("Only a signed-in person can confirm a record; keys and apps cannot.")
                kw["confirm"] = person if b["confirm"] else False
            return _json(store.update_record(rid, reason=b.get("reason", "") or
                                             ("Confirmed" if b.get("confirm") else ""), changed_by=actor(), **kw))
        return _json(store.get_record(rid, include_source_content=request.query_params.get("source") == "1"))

    def relation(request: Request) -> JSONResponse:
        authorize("write")
        return _json(store.delete_relation(request.path_params["relid"]))

    def graph(request: Request) -> JSONResponse:
        snap = store.project_snapshot(request.path_params["pid"])
        nodes = [
            {k: r[k] for k in ("id", "kind", "title", "status", "importance", "origin_model", "evidence_count",
                               "lifecycle")}
            for r in snap["records"].values()
        ]
        edges = [{"id": e["id"], "source": e["from_id"], "target": e["to_id"], "type": e["type"], "note": e["note"]}
                 for e in snap["relations"]]
        return _json({"nodes": nodes, "edges": edges})

    def gaps(request: Request) -> JSONResponse:
        return _json(analyze_gaps(store.project_snapshot(request.path_params["pid"])))

    def sources(request: Request) -> JSONResponse:
        snap = store.project_snapshot(request.path_params["pid"])
        per_source = Counter(r["source_id"] for r in snap["records"].values() if r["source_id"])
        return _json({"sources": [{**s, "record_count": per_source[s["id"]]} for s in reversed(snap["sources"])]})

    def source(request: Request) -> JSONResponse:
        src = store.get_source(request.path_params["sid"])
        snap = store.project_snapshot(src["project_id"])
        src["records"] = [
            {k: r[k] for k in ("id", "kind", "title", "status")}
            for r in snap["records"].values() if r["source_id"] == src["id"]
        ]
        return _json(src)

    def import_source(request: Request) -> JSONResponse:
        authorize("create_record")
        b = _body(request)
        return _json(import_text(
            store, b.get("text", ""), project=request.path_params["pid"], origin_model=b.get("origin_model") or None,
            title=b.get("title") or None, created_by=actor(),
        ), 201)

    def check(request: Request) -> JSONResponse:
        """Preflight: compare statements with what the project has settled. Reads only."""
        b = _body(request)
        statements = b.get("statements") if "statements" in b else [b.get("statement")]
        if not isinstance(statements, list):
            raise m.ValidationError("statements must be a list")
        return _json(check_statements(store.project_snapshot(request.path_params["pid"]), statements))

    def briefing(request: Request) -> JSONResponse:
        """The text block a model is handed for this project, and the records that went into it."""
        q = request.query_params
        ref = request.path_params["pid"]
        snap = store.project_snapshot(ref)
        focus = None
        if q.get("q"):
            hits = store.search(q["q"], project=ref, include_inactive=True, include_sources=False, limit=30)
            focus = [h["id"] for h in hits["results"]]
        try:
            budget = int(q.get("budget", DEFAULT_BUDGET))
        except ValueError:
            raise m.ValidationError("budget must be a number of tokens")
        policy = q.get("policy", "all")
        if policy not in ("all", "confirmed"):
            raise m.ValidationError("policy must be all or confirmed")
        out = build_briefing(snap, budget_tokens=budget, policy=policy, focus_ids=focus)
        if on_briefing and q.get("receipt") != "0":
            out["receipt_id"] = on_briefing(out, query=q.get("q"))
        return _json(out)

    def export(request: Request) -> JSONResponse:
        ref = request.path_params.get("pid")
        return _json(store.export(ref), 200)

    def activity(request: Request) -> JSONResponse:
        q = request.query_params
        return _json({"activity": store.activity(q.get("project") or None, limit=int(q["limit"]) if q.get("limit", "").isdigit() else 60)})

    def search(request: Request) -> JSONResponse:
        q = request.query_params.get("q", "")
        res = store.search(q, include_inactive=True, limit=20)
        needle = q.strip().lower()
        res["projects"] = [p for p in store.list_projects() if needle and needle in p["name"].lower()]
        return _json(res)

    async def events(request: Request) -> StreamingResponse:
        """Server-sent events: one message per database change, from any client (MCP, CLI, this page)."""

        async def stream():
            last = await asyncio.to_thread(store.revision)
            last_seen = (await asyncio.to_thread(store.activity, None, 1) or [{"at": None}])[0]["at"]
            yield f"event: hello\ndata: {json.dumps({'revision': last})}\n\n"
            idle = 0.0
            while not await request.is_disconnected():
                await asyncio.sleep(poll_seconds)
                rev = await asyncio.to_thread(store.revision)
                if rev != last:
                    last = rev
                    items = await asyncio.to_thread(store.activity, None, 20, last_seen)
                    if items:
                        last_seen = items[0]["at"]
                    yield f"event: change\ndata: {json.dumps({'revision': rev, 'activity': items})}\n\n"
                    idle = 0.0
                else:
                    idle += poll_seconds
                    if idle >= 15:
                        yield ": keep-alive\n\n"
                        idle = 0.0

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def revision(request: Request) -> JSONResponse:
        """For pages that poll: the current fingerprint, and what happened since ``since``."""
        since = request.query_params.get("since") or None
        return _json({"revision": store.revision(), "activity": store.activity(None, 20, since) if since else [],
                      "latest": (store.activity(None, 1) or [{"at": None}])[0]["at"]})

    def not_found(request: Request) -> Response:
        return _json({"error": "Not found"}, 404)

    def context(request: Request) -> JSONResponse:
        ref = request.path_params["pid"]
        matched = None
        if request.query_params.get("q"):
            hits = store.search(request.query_params["q"], project=ref, include_inactive=True,
                                include_sources=False, limit=50)
            matched = [h["id"] for h in hits["results"]]
        return _json(build_project_context(store.project_snapshot(ref), matched_ids=matched))

    def T(handler: Callable[[Request], Response]):
        """Read the body on the event loop, then run the handler (and its database work) in a worker thread."""
        async def endpoint(request: Request) -> Response:
            request.state.raw = await request.body() if request.method in ("POST", "PATCH", "PUT", "DELETE") else b""
            return await run_in_threadpool(handler, request)
        return endpoint

    P = prefix
    routes = [
        *([] if hosted else [Route("/", index)]),
        Route(f"{P}/meta", T(meta)),
        Route(f"{P}/projects", T(projects), methods=["GET", "POST"]),
        Route(f"{P}/projects/{{pid}}", T(project), methods=["GET", "PATCH", "DELETE"]),
        Route(f"{P}/projects/{{pid}}/records", T(records), methods=["GET", "POST"]),
        Route(f"{P}/projects/{{pid}}/graph", T(graph)),
        Route(f"{P}/projects/{{pid}}/gaps", T(gaps)),
        Route(f"{P}/projects/{{pid}}/sources", T(sources)),
        Route(f"{P}/projects/{{pid}}/context", T(context)),
        Route(f"{P}/projects/{{pid}}/briefing", T(briefing)),
        Route(f"{P}/projects/{{pid}}/check", T(check), methods=["POST"]),
        Route(f"{P}/projects/{{pid}}/export", T(export)),
        Route(f"{P}/projects/{{pid}}/import", T(import_source), methods=["POST"]),
        Route(f"{P}/records/{{rid}}", T(record), methods=["GET", "PATCH", "DELETE"]),
        Route(f"{P}/relations/{{relid}}", T(relation), methods=["DELETE"]),
        Route(f"{P}/sources/{{sid}}", T(source)),
        Route(f"{P}/activity", T(activity)),
        Route(f"{P}/search", T(search)),
        Route(f"{P}/revision", T(revision)),
        *([] if hosted else [Route(f"{P}/events", events)]),
        Route(f"{P}/{{rest:path}}", T(not_found), methods=["GET", "POST", "PATCH", "PUT", "DELETE"]),
        *([] if hosted else [Mount("/static", app=StaticFiles(directory=str(static_dir)), name="static")]),
    ]
    from .tenancy import LimitReached, PermissionDenied

    async def _forbidden(request: Request, exc: Exception) -> JSONResponse:
        return _json({"error": str(exc)}, 403)

    async def _limit(request: Request, exc: Exception) -> JSONResponse:
        return _json({"error": str(exc), "code": "limit_reached"}, 402)

    app = Starlette(routes=routes, middleware=[] if hosted else [Middleware(LocalOnly)],
                    exception_handlers={m.NotFound: _not_found, LimitReached: _limit, m.ValidationError: _invalid,
                                        PermissionDenied: _forbidden, TypeError: _invalid, AttributeError: _invalid})
    if not hosted:
        app.state.store = store
    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description=f"{BRAND['name']} dashboard")
    ap.add_argument("--db", default=str(default_db_path()))
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    print(f"{BRAND['name']} dashboard on http://127.0.0.1:{args.port}  (database: {args.db})")
    uvicorn.run(create_app(args.db), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
