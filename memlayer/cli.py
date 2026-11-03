"""Human-facing CLI: inspect, edit, approve, delete and export memory.

    memlayer projects
    memlayer import samples/chatgpt_feasibility_session.md
    memlayer context "Environmental AI Research"
    memlayer search "sentinel revisit" --project "Environmental AI Research"
    memlayer show rec_...
    memlayer set-status rec_... evidence_supported --reason "Checked ESA docs" --evidence-url https://...
    memlayer supersede rec_OLD --by rec_NEW --reason "..."
    memlayer gaps "Environmental AI Research"
    memlayer delete rec_... --yes
    memlayer export backup.json
    memlayer serve
    memlayer web --port 8765
    memlayer hosted --public-url https://memory.example.com --data-dir /data
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import models as m
from .context import build_project_context
from .db import default_db_path
from .gaps import analyze_gaps
from .importer import import_text
from .store import MemoryStore


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _print_gaps(report: dict[str, Any]) -> None:
    print(f"Gap analysis for {report['project']['name']}  ({report['method']})")
    for g in report["gaps"]:
        print(f"\n[{g['gap_id']}] {g['severity'].upper():6} {g['type']}  (confidence: {g['confidence']})")
        print(f"  {g['summary']}")
        print(f"  records: {', '.join(g['record_ids'])}")
        print(f"  action:  {g['suggested_action']}")
    if report["resolved_conflicts"]:
        print("\nResolved conflicts (not reported as gaps):")
        for r in report["resolved_conflicts"]:
            print(f"  - {r['resolution']}  [{', '.join(r['record_ids'])}]")
    print("\nLimitations:")
    for line in report["limitations"]:
        print(f"  - {line}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="memlayer", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(default_db_path()), help="SQLite file (default: $MEMLAYER_DB or ~/.memlayer/memory.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("projects", help="list projects")
    p = sub.add_parser("create-project", help="create a project")
    p.add_argument("name")
    p.add_argument("--description", default="")
    p.add_argument("--objective", default="")

    p = sub.add_parser("import", help="import a text/markdown file (stored verbatim; structured items extracted)")
    p.add_argument("file", type=Path)
    p.add_argument("--project")
    p.add_argument("--origin-model")
    p.add_argument("--conversation-id")
    p.add_argument("--title")

    p = sub.add_parser("add", help="save one record")
    p.add_argument("project")
    p.add_argument("kind", choices=m.RECORD_KINDS)
    p.add_argument("title")
    p.add_argument("--content", default="")
    p.add_argument("--origin-model", default="human")
    p.add_argument("--importance", choices=list(m.IMPORTANCE), default="normal")
    p.add_argument("--subject")
    p.add_argument("--value")
    p.add_argument("--evidence-url", action="append", default=[])
    p.add_argument("--relation", action="append", default=[], metavar="TYPE:REC_ID")

    p = sub.add_parser("search", help="keyword search")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--project")
    p.add_argument("--all", action="store_true", help="include superseded/resolved/retracted")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("context", help="project briefing (what an MCP client sees)")
    p.add_argument("project")
    p.add_argument("--query")

    p = sub.add_parser("show", help="full record with evidence, relations and history")
    p.add_argument("record_id")
    p.add_argument("--source", action="store_true", help="include full source text")

    p = sub.add_parser("set-status", help="approve/reject: change verification or lifecycle")
    p.add_argument("record_id")
    p.add_argument("status", choices=[*m.VERIFICATION, "active", "resolved", "retracted"])
    p.add_argument("--reason", required=True)
    p.add_argument("--evidence-url", action="append", default=[])
    p.add_argument("--citation")

    p = sub.add_parser("edit", help="correct a record's text")
    p.add_argument("record_id")
    p.add_argument("--title")
    p.add_argument("--content")
    p.add_argument("--reason", required=True)

    p = sub.add_parser("supersede", help="mark a record as replaced by a newer one")
    p.add_argument("record_id")
    p.add_argument("--by", required=True, dest="newer")
    p.add_argument("--reason", required=True)

    p = sub.add_parser("gaps", help="run the gap analyzer")
    p.add_argument("project")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("delete", help="permanently delete a record (or --project to delete a whole project)")
    p.add_argument("target")
    p.add_argument("--project", action="store_true", help="target is a project name/id")
    p.add_argument("--yes", action="store_true", help="confirm permanent deletion")

    p = sub.add_parser("export", help="export everything (or one project) as JSON")
    p.add_argument("out", type=Path)
    p.add_argument("--project")

    sub.add_parser("serve", help="run the MCP server on stdio")
    p = sub.add_parser("web", help="run the local web dashboard")
    p.add_argument("--port", type=int, default=8765)
    sub.add_parser("hosted", help="run the hosted service (MCP over HTTPS with sign-in); see memlayer hosted -h")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hosted"]:  # its own options are parsed by hosted.main
        from .hosted import main as hosted
        hosted(argv[1:])
        return 0
    args = build_parser().parse_args(argv)
    if args.cmd == "serve":
        from .server import main as serve
        serve(["--db", args.db])
        return 0

    if args.cmd == "web":
        from .web import main as web
        web(["--db", args.db, "--port", str(args.port)])
        return 0

    store = MemoryStore(args.db)
    try:
        if args.cmd == "projects":
            _print(store.list_projects())
        elif args.cmd == "create-project":
            _print(store.create_project(args.name, args.description, args.objective))
        elif args.cmd == "import":
            _print(import_text(store, args.file.read_text(encoding="utf-8"), project=args.project,
                               origin_model=args.origin_model, conversation_id=args.conversation_id,
                               title=args.title or args.file.name, created_by="cli:import"))
        elif args.cmd == "add":
            rels = []
            for spec in args.relation:
                rtype, _, rid = spec.partition(":")
                rels.append({"type": rtype, "target_id": rid})
            ev = [{"url": u} for u in args.evidence_url]
            _print(store.save_record(
                args.project, kind=args.kind, title=args.title, content=args.content,
                verification="evidence_supported" if ev else "unverified", importance=args.importance,
                subject=args.subject, value=args.value, origin_model=args.origin_model, created_by="cli",
                evidence=ev, relations=rels,
            ))
        elif args.cmd == "search":
            _print(store.search(args.query, project=args.project, include_inactive=args.all, limit=args.limit))
        elif args.cmd == "context":
            matched = None
            if args.query:
                matched = [h["id"] for h in store.search(args.query, project=args.project, include_inactive=True,
                                                          include_sources=False, limit=50)["results"]]
            _print(build_project_context(store.project_snapshot(args.project), matched_ids=matched))
        elif args.cmd == "show":
            _print(store.get_record(args.record_id, include_source_content=args.source))
        elif args.cmd == "set-status":
            kw: dict[str, Any] = {}
            if args.status in m.VERIFICATION:
                kw["verification"] = args.status
            else:
                kw["lifecycle"] = args.status
            ev = [{"url": u, "citation": args.citation} for u in args.evidence_url]
            if args.citation and not ev:
                ev = [{"citation": args.citation}]
            _print(store.update_record(args.record_id, reason=args.reason, changed_by="cli", add_evidence=ev, **kw))
        elif args.cmd == "edit":
            _print(store.update_record(args.record_id, reason=args.reason, changed_by="cli",
                                       title=args.title, content=args.content))
        elif args.cmd == "supersede":
            _print(store.update_record(args.record_id, reason=args.reason, changed_by="cli", superseded_by=args.newer))
        elif args.cmd == "gaps":
            report = analyze_gaps(store.project_snapshot(args.project))
            _print(report) if args.json else _print_gaps(report)
        elif args.cmd == "delete":
            if not args.yes:
                print("Refusing to delete without --yes (deletion is permanent; consider 'export' first).",
                      file=sys.stderr)
                return 2
            _print(store.delete_project(args.target) if args.project else store.delete_record(args.target))
        elif args.cmd == "export":
            args.out.write_text(json.dumps(store.export(args.project), indent=2, default=str), encoding="utf-8")
            print(f"Exported to {args.out}")
    except (m.ValidationError, m.NotFound) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
