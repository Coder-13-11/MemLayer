import json

import pytest

from memlayer.context import build_project_context
from memlayer.gaps import analyze_gaps
from memlayer.importer import import_text
from memlayer.models import NotFound, ValidationError
from memlayer.store import MemoryStore

from .conftest import PROJECT, SAMPLES, dump


def test_create_project_and_save_research(store):  # acceptance 1
    p = store.create_project(PROJECT, objective="Map wetland loss")
    rec = store.save_record(PROJECT, kind="finding", title="Sentinel-2 is free", origin_model="chatgpt")
    assert rec["project"]["id"] == p["id"]
    assert rec["status"] == "unverified"
    with pytest.raises(ValidationError):
        store.create_project(PROJECT.upper())  # names are unique, case-insensitive


def test_data_persists_after_reopen(db_path):  # acceptance 2
    s = MemoryStore(db_path)
    s.create_project(PROJECT)
    rid = s.save_record(PROJECT, kind="decision", title="Use 10 m bands", origin_model="claude")["id"]
    s.close()
    s2 = MemoryStore(db_path)
    assert s2.get_record(rid)["title"] == "Use 10 m bands"
    s2.close()


def test_import_keeps_original_source_and_extracts_records(store, imported):
    f1 = store.get_record(imported["F1"], include_source_content=True)
    assert f1["source"]["kind"] == "conversation"
    assert "Sentinel-2 provides free multispectral imagery" in f1["source"]["content"]
    assert f1["source_excerpt"].startswith("- [F1]")
    assert f1["status"] == "evidence_supported"
    assert f1["evidence"][0]["url"].startswith("https://sentiwiki")
    assert store.get_record(imported["D2"])["relations_out"][0]["record_id"] == imported["A1"]
    assert store.get_project(PROJECT)["objective"].startswith("Map year-over-year coastal wetland loss")


def test_reimport_is_idempotent(store, imported):
    before = len(store.project_snapshot(PROJECT)["records"])
    again = import_text(store, (SAMPLES / "chatgpt_feasibility_session.md").read_text())
    assert again["source"]["created"] is False and again["records_created"] == []
    assert len(store.project_snapshot(PROJECT)["records"]) == before


def test_new_findings_do_not_overwrite_unrelated(store, imported):  # acceptance 5
    before = {rid: (r["title"], r["content"], r["version"]) for rid, r in store.project_snapshot(PROJECT)["records"].items()}
    store.save_record(PROJECT, kind="finding", title="Tidal stage data exists from NOAA gauges", origin_model="claude")
    after = store.project_snapshot(PROJECT)["records"]
    for rid, val in before.items():
        assert (after[rid]["title"], after[rid]["content"], after[rid]["version"]) == val


def test_records_retain_source_and_status(store):  # acceptance 6
    store.create_project(PROJECT)
    rec = store.save_record(
        PROJECT, kind="finding", title="NWI updated before 2010 in coastal parishes", origin_model="claude",
        conversation_id="claude-session-7", source_text="Claude said: NWI coastal polygons predate 2010.",
    )
    detail = store.get_record(rec["id"])
    assert detail["origin_model"] == "claude"
    assert detail["conversation_id"] == "claude-session-7"
    assert detail["source"]["kind"] == "llm_output"
    assert detail["status"] == "unverified"


def test_llm_claims_cannot_self_verify_without_evidence(store):
    store.create_project(PROJECT)
    with pytest.raises(ValidationError):
        store.save_record(PROJECT, kind="finding", title="x", verification="evidence_supported", origin_model="claude")
    rid = store.save_record(PROJECT, kind="finding", title="x", origin_model="claude")["id"]
    with pytest.raises(ValidationError):
        store.update_record(rid, reason="trust me", verification="evidence_supported")
    assert store.get_record(rid)["status"] == "unverified"  # the failed update rolled back
    ok = store.update_record(rid, reason="checked", verification="evidence_supported",
                             add_evidence=[{"url": "https://example.org/a", "checked_at": "2026-11-02"}])
    assert ok["status"] == "evidence_supported" and ok["evidence"][0]["checked_at"] == "2026-11-02"


def test_supersede_keeps_history(store, imported):  # acceptance 7
    old = imported["D3"]
    new = store.save_record(
        PROJECT, kind="decision", title="Use dry-season (Oct-Mar) median composites instead of annual ones",
        origin_model="claude", relations=[{"type": "supersedes", "target_id": old, "note": "Annual medians mix tidal and seasonal states"}],
    )
    old_rec = store.get_record(old)
    assert old_rec["status"] == "superseded" and old_rec["superseded_by"] == new["id"]
    assert old_rec["title"].startswith("Build one annual median composite")  # content not erased
    assert old_rec["history"][-1]["snapshot"]["lifecycle"] == "active"
    assert old_rec["history"][-1]["change_reason"] == "Annual medians mix tidal and seasonal states"
    assert old not in [r["id"] for r in store.search("annual median composite", project=PROJECT)["results"]]
    assert any(r["id"] == old for r in store.search("annual median composite", project=PROJECT, include_inactive=True)["results"])


def test_context_prioritizes_newer_record(store, imported):  # acceptance 7, retrieval side
    old = imported["D3"]
    new = store.save_record(PROJECT, kind="decision", title="Use dry-season median composites",
                            origin_model="claude", importance="high",
                            relations=[{"type": "supersedes", "target_id": old}])["id"]
    ctx = build_project_context(store.project_snapshot(PROJECT))
    decision_ids = [d["id"] for d in ctx["decisions"]]
    assert new in decision_ids and old not in decision_ids
    hist = {h["id"]: h for h in ctx["history"]}
    assert hist[old]["superseded_by"]["id"] == new


def test_update_records_history_and_versions(store, imported):
    rid = imported["F4"]
    store.update_record(rid, reason="Clarify VRAM", changed_by="claude", content="Needs ~9 GB at batch 16.")
    rec = store.get_record(rid)
    assert rec["version"] == 2 and rec["content"] == "Needs ~9 GB at batch 16."
    assert rec["history"][0]["snapshot"]["content"].startswith("256x256 tiles")
    assert rec["history"][0]["changed_by"] == "claude"


def test_read_operations_never_modify(store, imported):
    before = dump(store)
    store.get_record(imported["F1"], include_source_content=True)
    store.search("sentinel revisit", project=PROJECT, include_inactive=True)
    store.list_projects()
    snap = store.project_snapshot(PROJECT)
    build_project_context(snap)
    analyze_gaps(snap)
    assert dump(store) == before


def test_reads_run_query_only(store, imported):
    with store._read() as c:
        with pytest.raises(Exception, match="readonly|read-only"):
            c.execute("UPDATE records SET title = 'x'")


def test_search_ranks_relevant_records(store, imported):
    hits = store.search("revisit days", project=PROJECT)["results"]
    assert {h["id"] for h in hits[:2]} == {imported["F2"], imported["F6"]}
    assert store.search("Sentinel-2 \"OR\" (drop table)", project=PROJECT)["results"]  # no FTS syntax errors


def test_relations_validated(store, imported):
    with pytest.raises(ValidationError):
        store.update_record(imported["F1"], reason="x", add_relations=[{"type": "loves", "target_id": imported["F2"]}])
    with pytest.raises(NotFound):
        store.update_record(imported["F1"], reason="x", add_relations=[{"type": "supports", "target_id": "rec_000000000000"}])
    other = store.create_project("Other")
    foreign = store.save_record(other["id"], kind="note", title="n", origin_model="human")["id"]
    with pytest.raises(ValidationError):
        store.update_record(imported["F1"], reason="x", add_relations=[{"type": "supports", "target_id": foreign}])


def test_delete_and_export(store, imported, tmp_path):
    exported = store.export(PROJECT)
    proj = exported["projects"][0]
    assert len(proj["records"]) == len(imported) and proj["sources"] and proj["evidence"]
    json.dumps(exported, default=str)
    store.delete_record(imported["Q1"])
    with pytest.raises(NotFound):
        store.get_record(imported["Q1"])
    store.delete_project(PROJECT)
    assert store.list_projects() == []
    if store.dialect == "sqlite":
        assert store.conn.execute("SELECT count(*) FROM records_fts").fetchone()[0] == 0


def test_possible_duplicate_flagged(store, imported):
    rec = store.save_record(PROJECT, kind="finding", title="Sentinel-2 revisits the same location every 5 days!",
                            origin_model="claude")
    assert rec["possible_duplicates"][0]["id"] == imported["F2"]


def test_import_keeps_items_under_unfamiliar_headings(store):
    """Models name sections however they like; no item may be dropped because of it."""
    text = """## Objective
Track college applications.

## Applications and statuses
- [A1] Applied to UCF early action
  Submitted on time.
- [A2] Waiting on the Georgia Tech portal
  kind: task

## Decisions
- [D1] Lead with the robotics project in the personal statement
- [D2] Keep every essay under the word limit. """ + "This sentence is long. " * 20 + """
"""
    res = import_text(store, text, project="College apps", origin_model="chatgpt")
    by_key = {r["key"]: r for r in res["records_created"]}
    assert set(by_key) == {"_objective", "A1", "A2", "D1", "D2"}
    d2 = store.get_record(by_key["D2"]["id"])
    assert d2["title"] == "Keep every essay under the word limit." and "This sentence is long." in d2["content"]
    assert by_key["A1"]["kind"] == "note" and by_key["A2"]["kind"] == "task" and by_key["D1"]["kind"] == "decision"
    assert "applications-and-statuses" in store.get_record(by_key["A1"]["id"])["tags"]
    assert any("Applications and statuses" in w and "1 item" in w for w in res["warnings"])
