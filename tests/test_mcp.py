"""Exercise the server through the MCP protocol, as a client application would."""

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from memlayer.server import create_server

from .conftest import PROJECT, SAMPLES, dump

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parent.parent
CORE_TOOLS = {"save_memory", "search_memory", "get_project_context", "get_memory_details", "update_memory", "analyze_gaps"}


async def call(session: ClientSession, tool: str, **args):
    res = await session.call_tool(tool, args)
    if res.isError:
        raise RuntimeError(res.content[0].text)
    return res.structuredContent


async def test_tools_are_listed_with_schemas_and_hints(db_path):
    async with create_connected_server_and_client_session(create_server(str(db_path))) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    assert CORE_TOOLS <= set(tools)
    for name in ("search_memory", "get_project_context", "get_memory_details", "analyze_gaps"):
        assert tools[name].annotations.readOnlyHint is True
    assert tools["update_memory"].annotations.readOnlyHint is False
    kind_schema = tools["save_memory"].inputSchema["properties"]["kind"]
    assert "finding" in kind_schema["enum"]
    assert set(tools["update_memory"].inputSchema["required"]) == {"record_id", "reason", "changed_by"}


async def test_invalid_input_is_rejected_without_writing(db_path):
    async with create_connected_server_and_client_session(create_server(str(db_path))) as s:
        await call(s, "create_project", name=PROJECT)
        bad = await s.call_tool("save_memory", {"project": PROJECT, "kind": "rumor", "title": "x", "origin_model": "claude"})
        assert bad.isError
        bad = await s.call_tool("save_memory", {"project": PROJECT, "kind": "finding", "title": "x",
                                                "origin_model": "claude", "verification": "evidence_supported"})
        assert bad.isError and "requires at least one evidence" in bad.content[0].text
        bad = await s.call_tool("get_memory_details", {"record_id": "rec_ffffffffffff"})
        assert bad.isError
        listed = await call(s, "search_memory", project=PROJECT)
    assert listed["results"] == []


async def test_two_clients_share_memory(db_path):  # acceptance 3 & 4
    text = (SAMPLES / "chatgpt_feasibility_session.md").read_text()
    # Client A ("ChatGPT side"): imports the research.
    async with create_connected_server_and_client_session(create_server(str(db_path))) as a:
        imported = await call(a, "import_source", project=PROJECT, text=text)
    keys = {r["key"]: r["id"] for r in imported["records_created"]}

    # Client B ("Claude side"), separate server instance on the same database.
    async with create_connected_server_and_client_session(create_server(str(db_path))) as b:
        ctx = await call(b, "get_project_context", project=PROJECT)
        assert ctx["project"]["objective"].startswith("Map year-over-year")
        assert keys["F1"] in [f["id"] for f in ctx["findings"]]
        assert ctx["contributing_models"] == ["chatgpt"]
        f1 = next(f for f in ctx["findings"] if f["id"] == keys["F1"])
        assert f1["status"] == "evidence_supported" and f1["evidence_count"] == 1
        f4 = next(f for f in ctx["findings"] if f["id"] == keys["F4"])
        assert f4["status"] == "unverified"

        focused = await call(b, "get_project_context", project=PROJECT, query="NWI ground truth")
        assert focused["filtered_by_query"] and keys["A1"] in [x["id"] for x in focused["assumptions"]]

        saved = await call(b, "save_memory", project=PROJECT, kind="finding", origin_model="claude",
                           title="NWI coastal polygons were mostly mapped before 2010",
                           relations=[{"type": "challenges", "target_id": keys["A1"]}])

    # Client A sees what client B saved.
    async with create_connected_server_and_client_session(create_server(str(db_path))) as a:
        detail = await call(a, "get_memory_details", record_id=keys["A1"])
        challenges = [x for x in detail["relations_in"] if x["type"] == "challenges"]
        assert [x["record_id"] for x in challenges] == [saved["id"]]
        gaps = await call(a, "analyze_gaps", project=PROJECT)
        assert any(g["type"] == "challenged_claim" and keys["A1"] in g["record_ids"] for g in gaps["gaps"])


async def test_full_workflow_over_mcp(db_path):  # spec section 6, steps 1-7
    async with create_connected_server_and_client_session(create_server(str(db_path))) as s:
        await call(s, "create_project", name=PROJECT, objective="Map coastal wetland loss")
        for name in ("chatgpt_feasibility_session.md", "followup_notes.md"):
            await call(s, "import_source", project=PROJECT, text=(SAMPLES / name).read_text())
        hits = (await call(s, "search_memory", project=PROJECT, query="revisit"))["results"]
        f2 = next(h["id"] for h in hits if h["title"].endswith("every 5 days"))
        f6 = next(h["id"] for h in hits if h["title"].endswith("10 days"))

        gaps = await call(s, "analyze_gaps", project=PROJECT)
        conflict = [g for g in gaps["gaps"] if g["type"] == "possible_contradiction"]
        assert {f2, f6} == set(conflict[0]["record_ids"])
        assert any(g["type"] == "missing_evidence" for g in gaps["gaps"])

        # Claude reconciles: supersede the wrong claim through update_memory.
        fixed = await call(s, "save_memory", project=PROJECT, kind="finding", origin_model="claude",
                           title="Single-satellite repeat is 10 days; constellation revisit is 5 days",
                           subject="sentinel2.revisit_days", value="5",
                           evidence=[{"url": "https://sentiwiki.copernicus.eu/web/s2-mission", "checked_at": "2026-11-02"}],
                           verification="evidence_supported")
        updated = await call(s, "update_memory", record_id=f6, reason="10 days is the per-satellite repeat",
                             changed_by="claude", superseded_by=fixed["id"])
        assert updated["status"] == "superseded" and updated["history"]

        before = dump_via_new_store(db_path)
        gaps = await call(s, "analyze_gaps", project=PROJECT)
        ctx = await call(s, "get_project_context", project=PROJECT)
        await call(s, "get_memory_details", record_id=f6)
        assert dump_via_new_store(db_path) == before  # reads changed nothing

        assert not [g for g in gaps["gaps"] if g["type"] == "possible_contradiction"]
        assert any(f6 in r["record_ids"] for r in gaps["resolved_conflicts"])
        assert f6 not in [f["id"] for f in ctx["findings"]]
        assert fixed["id"] in [f["id"] for f in ctx["findings"]]
        assert f6 in [h["id"] for h in ctx["history"]]


def dump_via_new_store(db_path):
    from memlayer.store import MemoryStore
    s = MemoryStore(db_path)
    try:
        return dump(s)
    finally:
        s.close()


async def test_real_stdio_server_persists_across_restart(db_path):  # acceptance 2, real subprocess
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "memlayer.server", "--db", str(db_path)],
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        await call(s, "create_project", name=PROJECT)
        saved = await call(s, "save_memory", project=PROJECT, kind="decision", title="Use 10 m bands",
                           origin_model="claude")
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        got = await call(s, "get_memory_details", record_id=saved["id"])
    assert got["title"] == "Use 10 m bands" and got["origin_model"] == "claude"
