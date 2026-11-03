"""End-to-end demo of the MVP workflow (spec section 6) over real MCP stdio.

Each "client" below is a scripted MCP ClientSession that launches its own
server process on the same SQLite file, the same way Claude Desktop or Claude
Code would. It is not ChatGPT or Claude itself; it proves the protocol path.

    python scripts/demo.py            # uses ./demo.db (deleted first)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
DB = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "demo.db"
PROJECT = "Environmental AI Research"


def server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", "memlayer.server", "--db", str(DB)],
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


async def call(s: ClientSession, tool: str, **args):
    res = await s.call_tool(tool, args)
    if res.isError:
        raise RuntimeError(f"{tool}: {res.content[0].text}")
    return res.structuredContent


class Client:
    """One MCP connection = one AI application session."""

    def __init__(self, label: str):
        self.label = label

    async def __aenter__(self) -> ClientSession:
        self._stdio = stdio_client(server_params())
        r, w = await self._stdio.__aenter__()
        self._session = ClientSession(r, w)
        s = await self._session.__aenter__()
        await s.initialize()
        print(f"\n--- [{self.label}] connected to a fresh memlayer server process ---")
        return s

    async def __aexit__(self, *exc):
        await self._session.__aexit__(*exc)
        await self._stdio.__aexit__(*exc)


def step(n: int, text: str) -> None:
    print(f"\n=== Step {n}: {text} ===")


def show_items(title: str, items: list[dict]) -> None:
    print(f"  {title}:")
    for it in items:
        print(f"    {it['id']}  [{it['status']}, {it['origin_model']}, evidence={it['evidence_count']}]  {it['title']}")


async def main() -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{DB}{suffix}").unlink(missing_ok=True)

    async with Client("ChatGPT-side import") as s:
        step(1, "create the project")
        p = await call(s, "create_project", name=PROJECT, description="Synthetic demo project")
        print(f"  created {p['id']} {p['name']!r}")

        step(2, "import a (synthetic) ChatGPT research session, stored verbatim")
        text = (ROOT / "samples" / "chatgpt_feasibility_session.md").read_text()
        res = await call(s, "import_source", project=PROJECT, text=text)
        print(f"  source {res['source']['id']} stored; {len(res['records_created'])} records extracted")
        step(3, "structured records created from it")
        for r in res["records_created"]:
            print(f"    [{r['key']:>10}] {r['id']}  {r['kind']:<10} {r['title'][:70]}")
        res = await call(s, "import_source", project=PROJECT,
                         text=(ROOT / "samples" / "followup_notes.md").read_text())
        print(f"  + follow-up notes imported: {[r['key'] for r in res['records_created']]}")

    async with Client("Claude") as s:
        step(4, "a second client retrieves the project context")
        ctx = await call(s, "get_project_context", project=PROJECT)
        print(f"  objective: {ctx['project']['objective']}")
        show_items("decisions", ctx["decisions"])
        show_items("findings", ctx["findings"])
        show_items("open questions", ctx["open_questions"])
        print(f"  contributing models: {ctx['contributing_models']}")
        a1 = next(a for a in ctx["assumptions"] if a["title"].startswith("NWI polygons"))

        step(5, "Claude adds a new finding that challenges an earlier assumption")
        new = await call(s, "save_memory", project=PROJECT, kind="finding", origin_model="claude",
                         conversation_id="demo-claude-session",
                         title="NWI polygons for much of coastal Louisiana predate 2010",
                         content="If so, 2019 labels built from NWI would miss a decade of land loss.",
                         source_text="Claude: The NWI coverage for coastal parishes was largely mapped before 2010, "
                                     "so using it as 2019 ground truth is risky until update dates are checked.",
                         relations=[{"type": "challenges", "target_id": a1["id"],
                                     "note": "Age of NWI mapping undermines A1"}])
        print(f"  saved {new['id']} [{new['status']}] {new['title']}")
        print(f"  provenance: origin={new['origin_model']}, source={new['source']['id']} ({new['source']['kind']})")

    async with Client("ChatGPT (later session)") as s:
        hits = await call(s, "search_memory", project=PROJECT, query="NWI predate 2010")
        top = hits["results"][0]
        print(f"  search 'NWI predate 2010' -> {top['id']} by {top['origin_model']}: {top['title']}")

        step(6, "run analyze_gaps")
        gaps = await call(s, "analyze_gaps", project=PROJECT)
        for g in gaps["gaps"]:
            print(f"  [{g['gap_id']}] {g['severity']:<6} {g['type']:<24} ({g['confidence']}) {g['summary'][:110]}")
            print(f"        records: {', '.join(g['record_ids'])}")
        conflict = next(g for g in gaps["gaps"] if g["type"] == "possible_contradiction")
        f2, f6 = conflict["record_ids"]

        step(7, "reconcile: supersede the outdated revisit claim")
        fixed = await call(s, "save_memory", project=PROJECT, kind="finding", origin_model="claude",
                           title="Per-satellite repeat is 10 days; the two-satellite constellation revisit is 5 days",
                           subject="sentinel2.revisit_days", value="5", verification="evidence_supported",
                           evidence=[{"url": "https://sentiwiki.copernicus.eu/web/s2-mission",
                                      "citation": "Copernicus SentiWiki, Sentinel-2 mission", "checked_at": "2026-11-02"}],
                           relations=[{"type": "supersedes", "target_id": f6,
                                       "note": "10 days is the single-satellite repeat, not the revisit"}])
        old = await call(s, "get_memory_details", record_id=f6)
        print(f"  {f6} is now {old['status']} (superseded_by {old['superseded_by']}); "
              f"history keeps {len(old['history'])} prior version(s); original text still: {old['title']!r}")

    async with Client("Claude (next session)") as s:
        ctx = await call(s, "get_project_context", project=PROJECT, query="Sentinel-2 revisit")
        show_items("findings matching 'Sentinel-2 revisit'", ctx["findings"])
        print("  history:", json.dumps(ctx["history"], indent=None)[:300])
        gaps = await call(s, "analyze_gaps", project=PROJECT)
        remaining = [g["type"] for g in gaps["gaps"] if g["type"] in ("contradiction", "possible_contradiction")]
        print(f"  contradiction gaps remaining: {remaining}")
        for r in gaps["resolved_conflicts"]:
            print(f"  resolved (not a gap): {r['resolution']}")
        challenged = [g for g in gaps["gaps"] if g["type"] == "challenged_claim"]
        print(f"  still unresolved: {[g['summary'][:90] for g in challenged]}")

    print(f"\nDone. Database: {DB}  (inspect with: python -m memlayer.cli --db {DB.name} context \"{PROJECT}\")")


if __name__ == "__main__":
    asyncio.run(main())
