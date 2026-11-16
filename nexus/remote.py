"""CLI commands that talk to a NEXUS server, and operator commands for running one.

    nexus brief PROJECT            print the project's briefing; run it at the start of a coding-agent session
    nexus check PROJECT "step"...  check a plan against the record; exits 2 if a step conflicts with a decision
                                   or repeats something the team replaced, so it can gate a CI step
    nexus admin ...                migrate, outbox, set-plan, rotate-keys, generate-key, reset-link, workspaces

``brief`` and ``check`` use the server at $NEXUS_URL with the key in $NEXUS_API_KEY. With neither set they
read the local database instead (``--db``), so the same commands work for the single-user local mode.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

from .briefing import DEFAULT_BUDGET, build_briefing
from .check import check_statements
from .models import NotFound, ValidationError

LABELS = {"conflict": "conflicts with a recorded decision", "resembles_replaced": "resembles a replaced record",
          "review": "related records to read", "clear": "clear"}
BLOCKING = ("conflict", "resembles_replaced")


def _fail(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def _remote() -> tuple[str, str] | None:
    url, key = os.environ.get("NEXUS_URL"), os.environ.get("NEXUS_API_KEY")
    if not url and not key:
        return None
    if not (url and key):
        _fail("Set both NEXUS_URL and NEXUS_API_KEY to use a NEXUS server, or neither to use the local database.")
    return url.rstrip("/"), key


def _call(method: str, path: str, **kw: Any) -> dict[str, Any]:
    url, key = _remote()  # type: ignore[misc]
    try:
        r = httpx.request(method, f"{url}/v1{path}", headers={"Authorization": f"Bearer {key}"}, timeout=30, **kw)
    except httpx.HTTPError as e:
        _fail(f"Could not reach {url} ({type(e).__name__}).")
    if r.status_code == 401:
        _fail("The server did not accept NEXUS_API_KEY. Check the API key, or create a new one under Settings.")
    if r.status_code >= 400:
        try:
            _fail(r.json().get("error") or r.text[:200])
        except ValueError:
            _fail(f"The server answered {r.status_code}.")
    return r.json()


def _project_path(project: str) -> str:
    from urllib.parse import quote
    return f"/projects/{quote(project, safe='')}"


def brief(args: argparse.Namespace) -> None:
    if _remote():
        out = _call("GET", f"{_project_path(args.project)}/briefing",
                    params={"budget": args.budget, "policy": args.policy, **({"q": args.query} if args.query else {})})
    else:
        from .store import MemoryStore
        store = MemoryStore(args.db)
        try:
            focus = None
            if args.query:
                hits = store.search(args.query, project=args.project, include_inactive=True, include_sources=False)
                focus = [h["id"] for h in hits["results"]]
            out = build_briefing(store.project_snapshot(args.project), budget_tokens=args.budget, policy=args.policy,
                                 focus_ids=focus)
        except (ValidationError, NotFound) as e:
            _fail(str(e))
        finally:
            store.close()
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(out["text"])


def check(args: argparse.Namespace) -> None:
    statements = list(args.statements)
    if args.file:
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        statements += [line.strip(" -*\t") for line in text.splitlines() if line.strip(" -*\t")]
    if not statements:
        _fail("Give at least one statement, or --file with one per line.")
    if _remote():
        out = _call("POST", f"{_project_path(args.project)}/check", json={"statements": statements})
    else:
        from .store import MemoryStore
        store = MemoryStore(args.db)
        try:
            out = check_statements(store.project_snapshot(args.project), statements)
        except (ValidationError, NotFound) as e:
            _fail(str(e))
        finally:
            store.close()
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        n = len(out["results"])
        print(f"Checked {n} statement{'s' if n != 1 else ''} against \"{out['project']['name']}\": {LABELS[out['verdict']]}\n")
        for r in out["results"]:
            print(f"{'x' if r['verdict'] in BLOCKING else '-'} {r['statement']}")
            for c in r["conflicts"]:
                print(f"    conflicts with a recorded decision: [{c['id']}] {c['title']}")
                print(f"      recorded {c['subject']} = {c['recorded_value']!r}; this says {c['stated_value']!r}")
            for x in r["resembles_replaced"]:
                print(f"    resembles a replaced record: [{x['id']}] {x['title']} ({x['status']})")
                if x["replaced_by"]:
                    print(f"      now: [{x['replaced_by']['id']}] {x['replaced_by']['title']}")
            for x in r["related"]:
                print(f"    related: [{x['id']}] {x['title']} ({'confirmed' if x['confirmed'] else x['status']})")
            if r["verdict"] == "clear":
                print("    clear (nothing found; this is not confirmation that it is right)")
        print(f"\nMethod: {out['method']}")
    if out["verdict"] in BLOCKING and not args.warn_only:
        sys.exit(2)


def add_parsers(sub: Any, default_db: str) -> None:
    p = sub.add_parser("brief", help="print a project's briefing (what a model is handed)")
    p.add_argument("project")
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="size limit in tokens")
    p.add_argument("--policy", choices=["all", "confirmed"], default="all",
                   help="'confirmed' leaves out records nobody has confirmed or evidenced")
    p.add_argument("--query", help="put records matching these words first")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("check", help="check planned steps against the record; exit 2 on a conflict")
    p.add_argument("project")
    p.add_argument("statements", nargs="*")
    p.add_argument("--file", help="read statements from a file, one per line ('-' for standard input)")
    p.add_argument("--warn-only", action="store_true", help="always exit 0")
    p.add_argument("--json", action="store_true")


def admin(argv: list[str]) -> int:
    """Operator commands. They open the control database directly, so run them where the server's data is."""
    from .accounts import AccountStore
    from .config import _env
    from . import crypto, migrate

    ap = argparse.ArgumentParser(prog="nexus admin", description=admin.__doc__)
    ap.add_argument("--data", default=None, help="data folder or Postgres URL (default: NEXUS_DATABASE_URL, "
                                                 "DATABASE_URL, NEXUS_DATA_DIR, then ~/.nexus/hosted)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply pending migrations and give older accounts their workspace")
    sub.add_parser("status", help="show applied and pending migrations")
    sub.add_parser("workspaces", help="list workspaces with their plan and size")
    sub.add_parser("outbox", help="list messages that have not been sent (no mail server, or delivery failed)")
    sub.add_parser("generate-key", help="print a new NEXUS_SECRET_KEY")
    sub.add_parser("rotate-keys", help="re-seal stored provider keys with the current NEXUS_SECRET_KEY")
    p = sub.add_parser("set-plan", help="change a workspace's plan or individual limits")
    p.add_argument("workspace", help="workspace id or slug")
    p.add_argument("plan")
    p.add_argument("--limit", action="append", default=[], metavar="NAME=NUMBER")
    p = sub.add_parser("reset-link", help="print a one-hour password link for an account (when mail is not set up)")
    p.add_argument("email")
    p.add_argument("--public-url", default=_env("NEXUS_PUBLIC_URL") or "http://localhost:8000")
    args = ap.parse_args(argv)
    if args.cmd == "generate-key":
        print(crypto.generate_key())
        return 0
    location = args.data or _env("NEXUS_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "NEXUS_DATA_DIR") or "~/.nexus/hosted"
    acc = AccountStore(location)
    try:
        if args.cmd == "migrate":
            users = [dict(r) for r in acc.conn.execute("SELECT id, email, name FROM users")]
            made = [acc.ensure_personal_workspace(u)["name"] for u in users if not acc.workspaces_for(u["id"])]
            print(json.dumps({"applied_now": acc.migrations_applied, "workspaces_created": made,
                              **migrate.status(acc)}, indent=2))
        elif args.cmd == "status":
            print(json.dumps(migrate.status(acc), indent=2))
        elif args.cmd == "workspaces":
            for r in acc.conn.execute("SELECT id FROM workspaces ORDER BY created_at"):
                ws = acc.get_workspace(r["id"])
                projects = acc.memory_for(ws["id"]).list_projects()
                print(f"{ws['id']}  {ws['slug']:<28} {ws['plan']:<5} members={len(acc.members(ws['id']))} "
                      f"projects={len(projects)} records={sum(p['record_count'] for p in projects)}")
        elif args.cmd == "outbox":
            for msg in acc.outbox():
                print(f"--- {msg['created_at'][:16]} to {msg['to_email']}: {msg['subject']}"
                      + (f"  [failed: {msg['error']}]" if msg["error"] else "") + f"\n{msg['body']}\n")
        elif args.cmd == "rotate-keys":
            print(f"Re-sealed {acc.rotate_credentials()} provider keys with key {acc.keyring.primary_id}."
                  if acc.keyring else "No NEXUS_SECRET_KEY is configured.")
        elif args.cmd == "set-plan":
            limits = {k: int(v) for k, _, v in (x.partition("=") for x in args.limit)}
            ws = acc.set_plan(acc.get_workspace(args.workspace)["id"], args.plan, limits)
            print(json.dumps({"workspace": ws["name"], "plan": ws["plan"], "limits": ws["limits"]}, indent=2))
        elif args.cmd == "reset-link":
            user = acc.find_user(args.email)
            if not user:
                _fail("No account has that address.")
            print(f"{args.public_url.rstrip('/')}/reset/{acc.create_email_token(user['id'], 'reset')}")
    except (ValidationError, NotFound, ValueError) as e:
        _fail(str(e))
    finally:
        acc.close()
    return 0
