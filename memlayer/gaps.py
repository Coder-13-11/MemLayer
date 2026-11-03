"""Deterministic, source-backed knowledge-gap analysis.

Input is ``MemoryStore.project_snapshot``; output is a list of gaps, each citing
the record ids it is based on. Nothing here writes to the store.

Every gap carries a ``confidence``:
  certain  - a structural fact about the stored data (e.g. zero evidence rows)
  stated   - someone explicitly recorded the relation (e.g. "X contradicts Y")
  possible - a heuristic match that needs human review (same subject, different value)

The analyzer only reports conflicts it can ground in stored structure. Two
claims that disagree in meaning but share no subject key or explicit relation
are not detected (see LIMITATIONS).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

from .models import normalize_key

CLAIM_KINDS = ("finding", "decision", "hypothesis")
DEPENDENCY_TYPES = ("depends_on", "requires")
CONFLICT_TYPES = ("contradicts", "challenges")
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

METHOD = "deterministic rules v1 (explicit relations, subject/value comparison, evidence and dependency checks)"
LIMITATIONS = [
    "Contradictions are found only through explicit 'contradicts'/'challenges' relations or records that share a "
    "subject key with different values; conflicting prose without either is not detected.",
    "'possible' subject/value conflicts may reflect different contexts (units, dates, sensor modes) and need review.",
    "Evidence is counted, not judged: the analyzer does not check that a cited source actually supports the claim.",
]


def _brief(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "kind": r["kind"], "title": r["title"], "status": r["status"],
            "origin_model": r["origin_model"]}


def analyze_gaps(snapshot: dict[str, Any]) -> dict[str, Any]:
    recs: dict[str, dict[str, Any]] = snapshot["records"]
    rels: list[dict[str, Any]] = snapshot["relations"]
    active = {rid: r for rid, r in recs.items() if r["lifecycle"] == "active"}

    rels_in: dict[str, list[dict]] = defaultdict(list)
    rels_out: dict[str, list[dict]] = defaultdict(list)
    for rel in rels:
        rels_in[rel["to_id"]].append(rel)
        rels_out[rel["from_id"]].append(rel)

    def dependents(rid: str) -> list[str]:
        return [x["from_id"] for x in rels_in[rid] if x["type"] in DEPENDENCY_TYPES and x["from_id"] in active]

    def challengers(rid: str) -> list[str]:
        return [x["from_id"] for x in rels_in[rid] if x["type"] in CONFLICT_TYPES and x["from_id"] in active]

    gaps: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    explained_pairs: set[frozenset[str]] = set()

    def add(gap_type: str, severity: str, confidence: str, summary: str, ids: list[str], action: str,
            **details: Any) -> None:
        ids = list(dict.fromkeys(ids))
        gaps.append({
            "type": gap_type, "severity": severity, "confidence": confidence, "summary": summary,
            "record_ids": ids, "records": [_brief(recs[i]) for i in ids], "suggested_action": action, **details,
        })

    def resolution_of(a: dict, b: dict) -> str | None:
        for x, y in ((a, b), (b, a)):
            if x["lifecycle"] == "superseded":
                newer = x["superseded_by"]
                return f"{x['id']} was superseded" + (f" by {newer}" if newer else "")
            if x["lifecycle"] in ("retracted", "resolved"):
                return f"{x['id']} is {x['lifecycle']}"
        return None

    # 1. Explicit contradictions and challenges.
    for rel in rels:
        if rel["type"] not in CONFLICT_TYPES:
            continue
        a, b = recs[rel["from_id"]], recs[rel["to_id"]]
        pair = frozenset((a["id"], b["id"]))
        res = resolution_of(a, b)
        if res:
            resolved.append({"relation": rel["type"], "record_ids": [a["id"], b["id"]],
                             "records": [_brief(a), _brief(b)], "resolution": res})
            explained_pairs.add(pair)
            continue
        explained_pairs.add(pair)
        if rel["type"] == "contradicts":
            add("contradiction", "high", "stated",
                f"'{a['title']}' contradicts '{b['title']}', and neither has been superseded or retracted.",
                [a["id"], b["id"]],
                "Decide which claim holds; supersede or retract the other, or record evidence for each.",
                note=rel["note"])
        else:
            sev = "high" if b["importance"] == 3 or dependents(b["id"]) else "medium"
            add("challenged_claim", sev, "stated",
                f"'{b['title']}' is challenged by '{a['title']}' and the challenge is unresolved.",
                [b["id"], a["id"]],
                "Examine the challenge; update the challenged record's status or supersede it.",
                dependents=dependents(b["id"]), note=rel["note"])

    # 2. Same subject, different values (heuristic).
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for r in recs.values():
        if r["subject"] and r["value"]:
            by_subject[normalize_key(r["subject"])].append(r)
    for subject, items in by_subject.items():
        live = [r for r in items if r["lifecycle"] == "active"]
        conflicting = [
            (a, b) for a, b in combinations(live, 2)
            if normalize_key(a["value"]) != normalize_key(b["value"])
            and frozenset((a["id"], b["id"])) not in explained_pairs
        ]
        if conflicting:
            ids = list(dict.fromkeys(i for a, b in conflicting for i in (a["id"], b["id"])))
            values = {i: recs[i]["value"] for i in ids}
            sev = "high" if any(recs[i]["importance"] == 3 for i in ids) else "medium"
            add("possible_contradiction", sev, "possible",
                f"Active records disagree on '{items[0]['subject']}': "
                + "; ".join(f"{v!r} ({i})" for i, v in values.items()) + ".",
                ids,
                "Check whether these values describe the same thing. If one is wrong, supersede it; if both hold "
                "in different contexts, give them distinct subject keys.",
                subject=items[0]["subject"], values=values)
        # Value disagreements that a supersession already settled.
        for old in items:
            new = recs.get(old["superseded_by"] or "")
            if (old["lifecycle"] == "superseded" and new and normalize_key(new["subject"]) == subject
                    and normalize_key(new["value"]) != normalize_key(old["value"])
                    and frozenset((old["id"], new["id"])) not in explained_pairs):
                resolved.append({"relation": "same_subject_different_value", "record_ids": [old["id"], new["id"]],
                                 "records": [_brief(old), _brief(new)],
                                 "resolution": f"{old['id']} ({old['value']!r}) was superseded by {new['id']} "
                                               f"({new['value']!r})"})

    # 3. Important claims with no evidence.
    for r in active.values():
        if r["kind"] not in CLAIM_KINDS or r["evidence_count"]:
            continue
        supporters = [x["from_id"] for x in rels_in[r["id"]] if x["type"] == "supports" and x["from_id"] in active]
        if supporters:
            continue
        if r["kind"] == "decision" and any(x["type"] in DEPENDENCY_TYPES for x in rels_out[r["id"]]):
            continue  # a decision's rationale is its dependencies; rule 5 checks those
        deps = dependents(r["id"])
        sev = "high" if r["importance"] == 3 or deps else ("low" if r["importance"] == 1 else "medium")
        add("missing_evidence", sev, "certain",
            f"{r['kind'].capitalize()} '{r['title']}' (from {r['origin_model'] or 'unknown origin'}) has no evidence "
            f"or supporting record" + (f"; {len(deps)} record(s) depend on it." if deps else "."),
            [r["id"], *deps],
            "Attach a source (update_memory add_evidence) or link a supporting record; until then treat it as "
            "an unverified claim.",
            dependents=deps)

    # 4. Assumptions nobody has examined.
    for r in active.values():
        if r["kind"] != "assumption" or r["verification"] == "evidence_supported":
            continue
        deps, against = dependents(r["id"]), challengers(r["id"])
        if r["evidence_count"] and not against and r["verification"] == "unverified":
            continue
        sev = "high" if deps or against or r["importance"] == 3 else "medium"
        why = []
        if deps:
            why.append(f"{len(deps)} record(s) depend on it")
        if against:
            why.append(f"challenged by {', '.join(against)}")
        add("unresolved_assumption", sev, "certain",
            f"Assumption '{r['title']}' is {r['status']} with no supporting evidence"
            + (f" ({'; '.join(why)})." if why else "."),
            [r["id"], *deps, *against],
            "Test the assumption or find evidence; then mark it evidence_supported, or supersede/retract it.",
            dependents=deps, challenged_by=against)

    # 5. Things that rest on unfinished or outdated records.
    for rel in rels:
        if rel["type"] not in DEPENDENCY_TYPES or rel["from_id"] not in active:
            continue
        a, dep = recs[rel["from_id"]], recs[rel["to_id"]]
        problem, sev, action = None, "medium", ""
        if dep["lifecycle"] == "superseded":
            newer = dep["superseded_by"]
            problem = f"depends on superseded record '{dep['title']}'" + (f" (replaced by {newer})" if newer else "")
            sev, action = "high", "Re-check the dependent record against the newer record and re-link it."
        elif dep["lifecycle"] == "retracted":
            problem, sev = f"depends on retracted record '{dep['title']}'", "high"
            action = "Find a replacement basis or retract the dependent record."
        elif dep["kind"] == "question" and dep["lifecycle"] == "active":
            problem, action = f"depends on unanswered question '{dep['title']}'", "Answer the question first."
        elif dep["kind"] == "task" and dep["lifecycle"] == "active":
            problem, action = f"depends on unfinished task '{dep['title']}'", "Complete the task or mark it resolved."
        elif dep["verification"] in ("challenged", "contradicted") or challengers(dep["id"]):
            problem, sev = f"depends on challenged record '{dep['title']}'", "high"
            action = "Resolve the challenge on the dependency before relying on this record."
        if problem:
            ids = [a["id"], dep["id"]] + ([dep["superseded_by"]] if dep["superseded_by"] in recs else [])
            add("incomplete_dependency", sev, "certain", f"{a['kind'].capitalize()} '{a['title']}' {problem}.",
                ids, action)

    # 6. Open research questions.
    for r in active.values():
        if r["kind"] != "question":
            continue
        answers = [x["from_id"] for x in rels_in[r["id"]] if x["type"] == "answers" and x["from_id"] in active]
        deps = dependents(r["id"])
        if answers:
            add("candidate_answer_unreviewed", "low", "certain",
                f"Question '{r['title']}' has candidate answer(s) but is still open.",
                [r["id"], *answers], "Review the answer(s) and mark the question resolved if satisfied.")
        else:
            sev = "high" if deps or r["importance"] == 3 else ("low" if r["importance"] == 1 else "medium")
            add("open_question", sev, "certain", f"Research question '{r['title']}' has no recorded answer.",
                [r["id"], *deps], "Investigate and save the answer with an 'answers' relation.", dependents=deps)

    gaps.sort(key=lambda g: (_SEVERITY_ORDER[g["severity"]], g["type"]))
    for n, g in enumerate(gaps, 1):
        g["gap_id"] = f"G{n}"
    counts: dict[str, int] = defaultdict(int)
    for g in gaps:
        counts[g["type"]] += 1
    return {
        "project": {"id": snapshot["project"]["id"], "name": snapshot["project"]["name"]},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": METHOD,
        "counts": dict(counts),
        "gaps": gaps,
        "resolved_conflicts": resolved,
        "limitations": LIMITATIONS,
    }
