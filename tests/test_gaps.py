from memlayer.gaps import analyze_gaps

from .conftest import PROJECT


def gaps_of(store, gap_type=None):
    report = analyze_gaps(store.project_snapshot(PROJECT))
    if gap_type:
        return [g for g in report["gaps"] if g["type"] == gap_type]
    return report


def test_every_gap_cites_existing_records(store, imported):
    report = gaps_of(store)
    recs = store.project_snapshot(PROJECT)["records"]
    assert report["gaps"]
    for g in report["gaps"]:
        assert g["record_ids"] and all(r in recs for r in g["record_ids"])
        assert g["confidence"] in {"certain", "stated", "possible"}


def test_detects_deliberate_contradiction(store, imported):  # acceptance 8a
    found = gaps_of(store, "possible_contradiction")
    assert len(found) == 1
    assert set(found[0]["record_ids"]) == {imported["F2"], imported["F6"]}
    assert found[0]["confidence"] == "possible"  # heuristic, not asserted as fact


def test_detects_explicit_contradiction(store, imported):
    store.save_record(PROJECT, kind="finding", title="Wetland spectra shift strongly between seasons",
                      origin_model="claude", relations=[{"type": "contradicts", "target_id": imported["A2"]}])
    found = gaps_of(store, "contradiction")
    assert len(found) == 1 and imported["A2"] in found[0]["record_ids"]
    assert found[0]["confidence"] == "stated"


def test_detects_missing_evidence(store, imported):  # acceptance 8b
    ids = {g["record_ids"][0] for g in gaps_of(store, "missing_evidence")}
    assert imported["F4"] in ids          # GPU feasibility claim, no evidence
    assert imported["F1"] not in ids      # has evidence
    assert imported["D1"] not in ids      # decision justified by its dependencies


def test_superseded_vs_unresolved_contradiction(store, imported):  # acceptance 9
    # Resolve the revisit conflict by superseding the wrong claim ...
    store.save_record(
        PROJECT, kind="finding", title="Each satellite repeats every 10 days; the 2-satellite constellation gives 5 days",
        origin_model="claude", subject="sentinel2.revisit_days", value="5",
        evidence=[{"url": "https://sentiwiki.copernicus.eu/web/s2-mission"}],
        relations=[{"type": "supersedes", "target_id": imported["F6"]}],
    )
    # ... and add a separate conflict that nobody has resolved.
    store.save_record(PROJECT, kind="finding", title="NWI coastal polygons predate 2010", origin_model="claude",
                      relations=[{"type": "contradicts", "target_id": imported["A1"]}])
    report = gaps_of(store)
    conflict_gaps = [g for g in report["gaps"] if g["type"] in ("contradiction", "possible_contradiction")]
    assert len(conflict_gaps) == 1 and imported["A1"] in conflict_gaps[0]["record_ids"]
    resolved_ids = [set(r["record_ids"]) for r in report["resolved_conflicts"]]
    assert any(imported["F6"] in ids for ids in resolved_ids)
    assert all(imported["F6"] not in g["record_ids"] for g in report["gaps"])


def test_explicit_contradiction_resolved_by_supersession(store, imported):
    newer = store.save_record(PROJECT, kind="decision", title="Use dry-season composites", origin_model="claude",
                              relations=[{"type": "contradicts", "target_id": imported["D3"]},
                                         {"type": "supersedes", "target_id": imported["D3"]}])
    report = gaps_of(store)
    assert not [g for g in report["gaps"] if g["type"] == "contradiction"]
    assert {imported["D3"], newer["id"]} in [set(r["record_ids"]) for r in report["resolved_conflicts"]]


def test_unresolved_assumption_and_dependencies(store, imported):
    a1 = [g for g in gaps_of(store, "unresolved_assumption") if g["record_ids"][0] == imported["A1"]]
    assert a1 and a1[0]["severity"] == "high" and imported["D2"] in a1[0]["dependents"]
    deps = gaps_of(store, "incomplete_dependency")
    assert any(set(g["record_ids"]) == {imported["D4"], imported["T1"]} for g in deps)


def test_dependency_on_superseded_record(store, imported):
    newer = store.save_record(PROJECT, kind="finding", title="Cloud cover removes ~60% of summer scenes",
                              origin_model="claude", relations=[{"type": "supersedes", "target_id": imported["F5"]}])
    deps = gaps_of(store, "incomplete_dependency")
    hit = [g for g in deps if imported["D3"] in g["record_ids"]]
    assert hit and newer["id"] in hit[0]["record_ids"] and hit[0]["severity"] == "high"


def test_open_questions_and_candidate_answers(store, imported):
    assert {g["record_ids"][0] for g in gaps_of(store, "open_question")} == {imported["Q1"], imported["Q2"]}
    store.save_record(PROJECT, kind="finding", title="Published studies report 85-92% overall accuracy",
                      origin_model="claude", relations=[{"type": "answers", "target_id": imported["Q1"]}])
    assert {g["record_ids"][0] for g in gaps_of(store, "open_question")} == {imported["Q2"]}
    assert gaps_of(store, "candidate_answer_unreviewed")[0]["record_ids"][0] == imported["Q1"]


def test_consistent_data_produces_no_conflicts(store):
    store.create_project(PROJECT)
    for title, val in (("Revisit is 5 days", "5"), ("Combined revisit 5 days", "5 ")):
        store.save_record(PROJECT, kind="finding", title=title, origin_model="human", subject="revisit", value=val,
                          evidence=[{"url": "https://example.org"}])
    report = gaps_of(store)
    assert report["gaps"] == [] and report["resolved_conflicts"] == []
