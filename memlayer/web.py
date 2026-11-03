"""Local web dashboard: JSON API + static single-page app + live change stream.

    memlayer web                 # http://127.0.0.1:8765

It opens the same SQLite file as the MCP server, so anything an AI client saves
through MCP shows up here, and the /api/events stream tells the open page when
that happens. It binds to 127.0.0.1 only and refuses requests whose Host header
is not local (DNS-rebinding guard). Writes must carry the X-MemLayer header,
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
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__
from . import models as m
from .brand import BRAND
from .context import build_project_context
from .db import default_db_path
from .gaps import analyze_gaps
from .importer import import_text
from .store import MemoryStore

ACTOR = "dashboard"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
WRITE_HEADER = "x-memlayer"


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
                error = "Missing X-MemLayer header."
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
    return _json({"error": str(exc)}, 400)


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise m.ValidationError("Request body must be JSON")
    if not isinstance(data, dict):
        raise m.ValidationError("Request body must be a JSON object")
    return data


def _gap_brief(g: dict[str, Any]) -> dict[str, Any]:
    return {k: g[k] for k in ("gap_id", "type", "severity", "confidence", "summary", "record_ids", "records",
                                    "suggested_action")}


def create_app(db_path: str | None = None, store: MemoryStore | None = None, poll_seconds: float = 1.0, *,
               store_for_request: Callable[[], MemoryStore] | None = None,
               hosted: dict[str, Any] | None = None) -> Starlette:
    """Local use: one database, localhost only, a live event stream.

    The hosted service passes ``store_for_request`` (the signed-in account's memory) and ``hosted`` (``mcp_url``
    and an ``account()`` callable). It then wraps the app in its own sign-in middleware, serves the static files
    itself, and the page polls /api/revision instead of holding a stream open (serverless hosts cut those off).
    """
    if store_for_request is not None:
        class _Current:  # resolves to the signed-in account's memory on every call
            def __getattr__(self, name: str) -> Any:
                return getattr(store_for_request(), name)
        store = _Current()  # type: ignore[assignment]
    else:
        store = store or MemoryStore(db_path)
    static_dir = resources.files("memlayer").joinpath("static")

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

    async def meta(request: Request) -> JSONResponse:
        mcp = {
            "server_name": BRAND["mcp_server_name"],
            "tools": ["get_project_context", "search_memory", "get_memory_details", "save_memory",
                      "update_memory", "analyze_gaps", "import_source", "create_project", "list_projects",
                      "get_review_instructions"],
            "prompts": ["review_project"],
        }
        if hosted:
            mcp["url"] = hosted["mcp_url"]
            where = {"account": hosted["account"](), "live": "poll"}
        else:
            mcp["stdio_command"] = f"{sys.executable} -m memlayer.server --db {store.path}"
            where = {"db_path": store.path, "live": "stream"}
        return _json({
            "brand": BRAND, "version": __version__, **where, "mcp": mcp,
            "vocabulary": {
                "kinds": m.RECORD_KINDS, "statuses": m.STATUSES, "verification": m.VERIFICATION,
                "lifecycle": m.LIFECYCLE, "relation_types": m.RELATION_TYPES, "importance": list(m.IMPORTANCE),
            },
        })

    async def projects(request: Request) -> JSONResponse:
        if request.method == "POST":
            b = await _body(request)
            return _json(store.create_project(b.get("name", ""), b.get("description", ""), b.get("objective", "")), 201)
        return _json({"projects": [project_summary(p) for p in store.list_projects()]})

    async def project(request: Request) -> JSONResponse:
        ref = request.path_params["pid"]
        if request.method == "DELETE":
            return _json(store.delete_project(ref))
        if request.method == "PATCH":
            b = await _body(request)
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
            "decisions": ctx.get("decisions", []),
            "open_questions": ctx.get("open_questions", []),
            "gaps": [_gap_brief(g) for g in gaps["gaps"]],
            "gap_counts": gaps["counts"],
            "activity": store.activity(ref, limit=12),
        })

    async def records(request: Request) -> JSONResponse:
        ref = request.path_params["pid"]
        if request.method == "POST":
            b = await _body(request)
            rec = store.save_record(
                ref, kind=b.get("kind", "note"), title=b.get("title", ""), content=b.get("content", ""),
                verification=b.get("verification", "unverified"), importance=b.get("importance", "normal"),
                subject=b.get("subject") or None, value=b.get("value") or None, tags=b.get("tags") or [],
                origin_model=b.get("origin_model") or "human", created_by=ACTOR,
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

    async def record(request: Request) -> JSONResponse:
        rid = request.path_params["rid"]
        if request.method == "DELETE":
            return _json(store.delete_record(rid))
        if request.method == "PATCH":
            b = await _body(request)
            allowed = {"title", "content", "verification", "lifecycle", "importance", "subject", "value", "tags",
                       "superseded_by", "add_evidence", "add_relations"}
            kw = {k: v for k, v in b.items() if k in allowed}
            return _json(store.update_record(rid, reason=b.get("reason", ""), changed_by=ACTOR, **kw))
        return _json(store.get_record(rid, include_source_content=request.query_params.get("source") == "1"))

    async def relation(request: Request) -> JSONResponse:
        return _json(store.delete_relation(request.path_params["relid"]))

    async def graph(request: Request) -> JSONResponse:
        snap = store.project_snapshot(request.path_params["pid"])
        nodes = [
            {k: r[k] for k in ("id", "kind", "title", "status", "importance", "origin_model", "evidence_count",
                               "lifecycle")}
            for r in snap["records"].values()
        ]
        edges = [{"id": e["id"], "source": e["from_id"], "target": e["to_id"], "type": e["type"], "note": e["note"]}
                 for e in snap["relations"]]
        return _json({"nodes": nodes, "edges": edges})

    async def gaps(request: Request) -> JSONResponse:
        return _json(analyze_gaps(store.project_snapshot(request.path_params["pid"])))

    async def sources(request: Request) -> JSONResponse:
        snap = store.project_snapshot(request.path_params["pid"])
        per_source = Counter(r["source_id"] for r in snap["records"].values() if r["source_id"])
        return _json({"sources": [{**s, "record_count": per_source[s["id"]]} for s in reversed(snap["sources"])]})

    async def source(request: Request) -> JSONResponse:
        src = store.get_source(request.path_params["sid"])
        snap = store.project_snapshot(src["project_id"])
        src["records"] = [
            {k: r[k] for k in ("id", "kind", "title", "status")}
            for r in snap["records"].values() if r["source_id"] == src["id"]
        ]
        return _json(src)

    async def import_source(request: Request) -> JSONResponse:
        b = await _body(request)
        return _json(import_text(
            store, b.get("text", ""), project=request.path_params["pid"], origin_model=b.get("origin_model") or None,
            title=b.get("title") or None, created_by=ACTOR,
        ), 201)

    async def activity(request: Request) -> JSONResponse:
        q = request.query_params
        return _json({"activity": store.activity(q.get("project") or None, limit=int(q.get("limit", 60)))})

    async def search(request: Request) -> JSONResponse:
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

    async def revision(request: Request) -> JSONResponse:
        """For pages that poll: the current fingerprint, and what happened since ``since``."""
        since = request.query_params.get("since") or None
        return _json({"revision": store.revision(), "activity": store.activity(None, 20, since) if since else [],
                      "latest": (store.activity(None, 1) or [{"at": None}])[0]["at"]})

    async def not_found(request: Request) -> Response:
        return _json({"error": "Not found"}, 404)

    routes = [
        Route("/app" if hosted else "/", index),
        Route("/api/meta", meta),
        Route("/api/projects", projects, methods=["GET", "POST"]),
        Route("/api/projects/{pid}", project, methods=["GET", "PATCH", "DELETE"]),
        Route("/api/projects/{pid}/records", records, methods=["GET", "POST"]),
        Route("/api/projects/{pid}/graph", graph),
        Route("/api/projects/{pid}/gaps", gaps),
        Route("/api/projects/{pid}/sources", sources),
        Route("/api/projects/{pid}/import", import_source, methods=["POST"]),
        Route("/api/records/{rid}", record, methods=["GET", "PATCH", "DELETE"]),
        Route("/api/relations/{relid}", relation, methods=["DELETE"]),
        Route("/api/sources/{sid}", source),
        Route("/api/activity", activity),
        Route("/api/search", search),
        Route("/api/revision", revision),
        *([] if hosted else [Route("/api/events", events)]),
        Route("/api/{rest:path}", not_found),
        *([] if hosted else [Mount("/static", app=StaticFiles(directory=str(static_dir)), name="static")]),
    ]
    app = Starlette(routes=routes, middleware=[] if hosted else [Middleware(LocalOnly)],
                    exception_handlers={m.NotFound: _not_found, m.ValidationError: _invalid})
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
