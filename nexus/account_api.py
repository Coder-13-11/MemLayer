"""The signed-in app's API for everything that is not project memory: the account, workspaces, members,
invitations, API keys, provider keys, connected apps, usage, receipts and the audit log.

Every handler runs with a ``Principal`` already resolved by the session middleware in ``hosted.py``. Role
checks are explicit at the top of each handler; anything that changes access is written to the audit log.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import __version__
from .brand import BRAND
from .config import Settings
from .gateway import Gateway, GatewayError
from .keys import KEY_ROLES, KEY_SCOPES, PROVIDERS
from .models import NotFound, ValidationError
from .principal import current
from .tenancy import INVITABLE_ROLES, ROLES, require_under_limit_hook


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = json.loads(await request.body() or b"{}")
    except ValueError:
        raise ValidationError("Request body must be JSON") from None
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


def _int(request: Request, name: str, default: int) -> int:
    try:
        return int(request.query_params.get(name, default))
    except ValueError:
        raise ValidationError(f"{name} must be a whole number") from None


def account_routes(accounts: Any, settings: Settings, gateway: Gateway, mailer: Any,
                   client_ip: Callable[[Request], str], load_demo: Callable[[Any], dict[str, Any]]) -> list[Route]:
    def audit(request: Request, action: str, target: str | None = None, **detail: Any) -> None:
        p = current()
        accounts.audit(action, actor=p.actor, workspace_id=p.workspace_id, user_id=p.user["id"], target=target,
                       detail=detail, ip=client_ip(request))

    def handler(fn):
        """Handlers are plain functions: the body is read here, and the work runs off the event loop."""
        async def wrapped(request: Request) -> Response:
            body = await _body(request) if request.method in ("POST", "PATCH", "PUT", "DELETE") else {}
            return await run_in_threadpool(fn, request, body)
        return wrapped

    # ------------------------------------------------------------------ account

    def me(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        if request.method == "PATCH":
            accounts.update_profile(p.user["id"], str(b.get("name", "")))
        if request.method == "DELETE":
            if not accounts.verify_password(p.user["id"], str(b.get("password", ""))):
                raise ValidationError("That password is not correct.")
            out = accounts.delete_user(p.user["id"])
            accounts.audit("account.deleted", actor=p.actor, ip=client_ip(request))
            return _json(out)
        user = accounts.get_user(p.user["id"])
        return _json({
            "user": {k: user[k] for k in ("id", "email", "name", "created_at", "email_verified_at")},
            "workspaces": accounts.workspaces_for(p.user["id"]),
            "workspace": {"id": p.workspace["id"], "name": p.workspace["name"], "slug": p.workspace["slug"],
                          "role": p.role, "plan": p.workspace["plan"], "limits": p.workspace["limits"]},
            "service": {"brand": BRAND, "version": __version__, "public_url": settings.public_url,
                        "mcp_url": f"{settings.public_url}/mcp", "api_url": f"{settings.public_url}/v1",
                        "mail": bool(settings.smtp_url), "secrets": accounts.keyring is not None,
                        "mock_provider": settings.mock_provider, "roles": ROLES, "invitable_roles": INVITABLE_ROLES,
                        "key_roles": KEY_ROLES, "key_scopes": KEY_SCOPES, "providers": PROVIDERS},
        })

    def password(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        if not accounts.verify_password(p.user["id"], str(b.get("current", ""))):
            raise ValidationError("Your current password is not correct.")
        accounts.set_password(p.user["id"], str(b.get("new", "")), keep_session=request.cookies.get("nx_session"))
        accounts.audit("account.password_changed", actor=p.actor, user_id=p.user["id"], ip=client_ip(request))
        return _json({"ok": True, "note": "Other browsers and connected apps were signed out."})

    def my_connections(request: Request, b: dict[str, Any]) -> Response:
        return _json({"connections": accounts.grants_for(current().user["id"])})

    # --------------------------------------------------------------- workspaces

    def workspaces(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        owned = sum(1 for w in accounts.workspaces_for(p.user["id"]) if w["role"] == "owner")
        if owned >= 10:
            raise ValidationError("You already own 10 workspaces. Delete one before creating another.")
        ws = accounts.create_workspace(str(b.get("name", "")), p.user["id"])
        accounts.audit("workspace.created", actor=p.actor, workspace_id=ws["id"], user_id=p.user["id"],
                       target=ws["name"], ip=client_ip(request))
        return _json({"id": ws["id"], "name": ws["name"], "slug": ws["slug"], "role": "owner"}, 201)

    def workspace(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        if request.method == "PATCH":
            p.require("admin", "rename the workspace")
            ws = accounts.rename_workspace(p.workspace_id, str(b.get("name", "")))
            audit(request, "workspace.renamed", ws["name"])
        elif request.method == "DELETE":
            p.require("owner", "delete the workspace")
            if str(b.get("confirm", "")).strip() != p.workspace["name"]:
                raise ValidationError("Type the workspace's name exactly to confirm deleting it.")
            out = accounts.delete_workspace(p.workspace_id)
            accounts.audit("workspace.deleted", actor=p.actor, user_id=p.user["id"], target=p.workspace["name"],
                           detail={"workspace_id": p.workspace_id}, ip=client_ip(request))
            return _json(out)
        ws = accounts.get_workspace(p.workspace_id)
        store = accounts.memory_for(p.workspace_id)
        projects = store.list_projects()
        return _json({
            "workspace": {k: ws[k] for k in ("id", "name", "slug", "plan", "limits", "created_at")}, "role": p.role,
            "counts": {"members": len(accounts.members(ws["id"])), "projects": len(projects),
                       "records": sum(x["record_count"] for x in projects),
                       "keys": len(accounts.api_keys(ws["id"])), "providers": len(accounts.credentials(ws["id"])),
                       "connections": len(accounts.workspace_grants(ws["id"]))},
        })

    def members(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        return _json({"members": accounts.members(p.workspace_id), "you": p.user["id"],
                      "invitations": accounts.invitations(p.workspace_id) if p.can("admin") else []})

    def member(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        uid = request.path_params["uid"]
        if request.method == "PATCH":
            p.require("admin", "change roles")
            accounts.set_role(p.workspace_id, uid, str(b.get("role", "")), by_role=p.role)
            audit(request, "member.role_changed", uid, role=b.get("role"))
        else:
            if uid != p.user["id"]:
                p.require("admin", "remove members")
            accounts.remove_member(p.workspace_id, uid, by_role=p.role)
            audit(request, "member.left" if uid == p.user["id"] else "member.removed", uid)
        return _json({"members": accounts.members(p.workspace_id)})

    def invitations(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        p.require("admin", "invite people")
        if request.method == "POST":
            inv, token = accounts.create_invitation(p.workspace_id, str(b.get("email", "")),
                                                    str(b.get("role", "editor")), p.user["id"])
            link = f"{settings.public_url}/invite/{token}"
            sent = mailer.send(
                inv["email"], f"{p.user['email']} invited you to {inv['workspace']} on {BRAND['name']}",
                f"{p.user['email']} invited you to join the workspace “{inv['workspace']}” on {BRAND['name']} "
                f"as {inv['role']}.\n\nAccept the invitation:\n{link}\n\nThe link works once and expires in 7 days. "
                "If you were not expecting this, you can ignore it.")
            audit(request, "invitation.created", inv["email"], role=inv["role"])
            return _json({"invitation": inv, "link": link, "emailed": sent}, 201)
        return _json({"invitations": accounts.invitations(p.workspace_id)})

    def invitation(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        p.require("admin", "withdraw invitations")
        if not accounts.revoke_invitation(p.workspace_id, request.path_params["iid"]):
            raise NotFound("No such invitation.")
        audit(request, "invitation.revoked", request.path_params["iid"])
        return _json({"invitations": accounts.invitations(p.workspace_id)})

    # ------------------------------------------------------- keys and providers

    def keys(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        p.require("admin", "manage API keys")
        if request.method == "POST":
            days = b.get("expires_in_days")
            row, secret = accounts.create_api_key(
                p.workspace_id, str(b.get("name", "")), role=str(b.get("role", "editor")),
                scopes=b.get("scopes") if isinstance(b.get("scopes"), list) else None,
                default_project=str(b.get("default_project") or "") or None, created_by=p.user["id"],
                expires_in_days=int(days) if str(days or "").isdigit() else None)
            audit(request, "key.created", row["name"], role=row["role"], scopes=row["scopes"])
            return _json({"key": row, "secret": secret}, 201)
        return _json({"keys": accounts.api_keys(p.workspace_id)})

    def key(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        p.require("admin", "revoke API keys")
        if not accounts.revoke_api_key(p.workspace_id, request.path_params["kid"]):
            raise NotFound("No such key.")
        audit(request, "key.revoked", request.path_params["kid"])
        return _json({"keys": accounts.api_keys(p.workspace_id)})

    def credentials(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        p.require("admin", "manage provider keys")
        if request.method == "POST":
            row = accounts.set_credential(p.workspace_id, str(b.get("provider", "")), str(b.get("secret", "")),
                                          label=str(b.get("label") or "default"),
                                          base_url=str(b.get("base_url") or "") or None, created_by=p.user["id"])
            audit(request, "provider_key.stored", f"{row['provider']}/{row['label']}")
            return _json({"credential": row}, 201)
        return _json({"credentials": accounts.credentials(p.workspace_id), "available": accounts.keyring is not None})

    def credential(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        p.require("admin", "remove provider keys")
        if not accounts.delete_credential(p.workspace_id, request.path_params["cid"]):
            raise NotFound("No such provider key.")
        audit(request, "provider_key.removed", request.path_params["cid"])
        return _json({"credentials": accounts.credentials(p.workspace_id)})

    def connections(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        mine = [g for g in accounts.grants_for(p.user["id"]) if g["workspace_id"] == p.workspace_id]
        return _json({"connections": accounts.workspace_grants(p.workspace_id) if p.can("admin") else
                      [{**g, "email": p.user["email"], "user_id": p.user["id"]} for g in mine],
                      "all_members": p.can("admin")})

    def connection(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        gid = request.path_params["gid"]
        if not accounts.revoke_workspace_grant(p.workspace_id, gid, None if p.can("admin") else p.user["id"]):
            raise NotFound("No such connection.")
        audit(request, "connection.revoked", gid)
        return _json({"ok": True})

    # ------------------------------------------------- usage, receipts, audit

    def usage(request: Request, b: dict[str, Any]) -> Response:
        return _json(accounts.usage_summary(current().workspace_id, _int(request, "days", 30)))

    def receipts(request: Request, b: dict[str, Any]) -> Response:
        return _json({"receipts": accounts.receipts(current().workspace_id, _int(request, "limit", 50),
                                                    request.query_params.get("project") or None)})

    def receipt(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        r = accounts.get_receipt(p.workspace_id, request.path_params["rid"])
        # What those records look like now, so a reader can see what has changed since the model saw them.
        store = accounts.memory_for(p.workspace_id)
        try:
            now = store.project_snapshot(r["project_id"])["records"]
        except NotFound:
            now = {}
        for ref in r["record_refs"]:
            rec = now.get(ref["id"])
            ref["title"] = rec["title"] if rec else None
            ref["current_version"] = rec["version"] if rec else None
            ref["current_status"] = rec["status"] if rec else "deleted"
            ref["changed_since"] = rec is None or rec["version"] != ref["version"]
        return _json(r)

    def audit_log(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        p.require("admin", "read the audit log")
        return _json({"audit": accounts.audit_log(p.workspace_id, _int(request, "limit", 100))})

    def export(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        p.require("admin", "export the workspace")
        data = accounts.memory_for(p.workspace_id).export()
        data["workspace"] = {"id": p.workspace_id, "name": p.workspace["name"]}
        data["members"] = [{k: m[k] for k in ("email", "name", "role", "joined_at")} for m in accounts.members(p.workspace_id)]
        audit(request, "workspace.exported")
        return JSONResponse(data, headers={
            "content-disposition": f'attachment; filename="nexus-{p.workspace["slug"]}.json"'})

    def demo(request: Request, b: dict[str, Any]) -> Response:
        p = current()
        p.require("editor", "load the example project")
        require_under_limit_hook(accounts, p.workspace, "projects")
        return _json(load_demo(accounts.memory_for(p.workspace_id)), 201)

    async def playground(request: Request) -> Response:
        """Send one message through the gateway from the app, so a person can see what a model is handed."""
        p = current()
        p.require("editor", "use the playground")
        b = await _body(request)
        wire = b.get("wire") or "chat"
        if wire not in ("chat", "messages", "responses"):
            raise ValidationError("wire must be chat, messages or responses")
        model, text = str(b.get("model") or "mock-1"), str(b.get("message") or "").strip()
        if not text:
            raise ValidationError("Write a message to send.")
        body: dict[str, Any] = {"model": model}
        if wire == "responses":
            body["input"] = text
        else:
            body["messages"] = [{"role": "user", "content": text}]
            if wire == "messages":
                body["max_tokens"] = 1024
        headers = {k: str(v) for k, v in {
            "x-nexus-project": b.get("project"), "x-nexus-context": b.get("context") or "all",
            "x-nexus-provider": b.get("provider"), "x-nexus-refresh": "1"}.items() if v}
        key = {"id": f"app:{p.user['id']}", "name": "playground", "workspace_id": p.workspace_id,
               "default_project": None, "scopes": ["gateway"], "role": p.role}
        try:
            call = await run_in_threadpool(lambda: gateway.prepare(key, False, wire, body, headers, actor=p.actor))
            status, data, ours = await gateway.complete(call)
        except GatewayError as e:
            return _json({"error": e.message}, e.status)
        sent = call.body
        return _json({"status": status, "response": data, "provider": call.provider,
                      "receipt_id": call.receipt_id, "context_records": call.context_records,
                      "context_tokens": call.context_tokens,
                      "request_sent": {k: sent[k] for k in ("messages", "system", "instructions", "input") if k in sent}},
                     200 if status < 400 else 502)

    W = "/api/workspace"
    return [
        Route("/api/me", handler(me), methods=["GET", "PATCH", "DELETE"]),
        Route("/api/me/password", handler(password), methods=["POST"]),
        Route("/api/me/connections", handler(my_connections)),
        Route("/api/workspaces", handler(workspaces), methods=["POST"]),
        Route(W, handler(workspace), methods=["GET", "PATCH", "DELETE"]),
        Route(f"{W}/members", handler(members)),
        Route(f"{W}/members/{{uid}}", handler(member), methods=["PATCH", "DELETE"]),
        Route(f"{W}/invitations", handler(invitations), methods=["GET", "POST"]),
        Route(f"{W}/invitations/{{iid}}", handler(invitation), methods=["DELETE"]),
        Route(f"{W}/keys", handler(keys), methods=["GET", "POST"]),
        Route(f"{W}/keys/{{kid}}", handler(key), methods=["DELETE"]),
        Route(f"{W}/providers", handler(credentials), methods=["GET", "POST"]),
        Route(f"{W}/providers/{{cid}}", handler(credential), methods=["DELETE"]),
        Route(f"{W}/connections", handler(connections)),
        Route(f"{W}/connections/{{gid}}", handler(connection), methods=["DELETE"]),
        Route(f"{W}/usage", handler(usage)),
        Route(f"{W}/receipts", handler(receipts)),
        Route(f"{W}/receipts/{{rid}}", handler(receipt)),
        Route(f"{W}/audit", handler(audit_log)),
        Route(f"{W}/export", handler(export)),
        Route(f"{W}/demo", handler(demo), methods=["POST"]),
        Route(f"{W}/playground", playground, methods=["POST"]),
    ]
