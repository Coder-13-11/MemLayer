"""Reproducible measurements of what NEXUS does. No AI model and no network are involved.

    python benchmarks/run.py            run everything, print a report, write benchmarks/results.json
    python benchmarks/run.py --check    re-run the deterministic parts and fail if they differ from results.json

What is measured:

  1. plan_check   Precision and recall of the plan check on labelled steps (plan_check_cases.json), with two
                  baselines: flagging nothing, and flagging any step that shares a word with a replaced record.
  2. conflicts    Which planted conflicts the review finds in a seeded synthetic project, by how they were planted.
                  Conflicts that exist only in prose are planted on purpose: the review cannot find them.
  3. briefing     Size of the briefing a model is handed, against the structured context and the full export.
  4. retrieval    Keyword search on a seeded 2,000-record project: how often the planted target is first / in the
                  top five, against a no-ranking baseline.
  5. speed        Time for the operations a request waits on, at 200 and 2,000 records. Timings are from this
                  machine and are reported but never compared by --check.

Everything except timings is deterministic: the synthetic projects come from a fixed seed.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from nexus.briefing import build_briefing, estimate_tokens  # noqa: E402
from nexus.check import check_statements, tokens  # noqa: E402
from nexus.context import build_project_context  # noqa: E402
from nexus.demo import PROJECT, load_demo  # noqa: E402
from nexus.gaps import analyze_gaps  # noqa: E402
from nexus.store import MemoryStore  # noqa: E402

BLOCKING = ("conflict", "resembles_replaced")
SEED = 20261116


def demo_store() -> MemoryStore:
    store = MemoryStore(":memory:")
    load_demo(store)
    return store


# ------------------------------------------------------------------ 1. plan check

def plan_check() -> dict:
    cases = json.loads((ROOT / "plan_check_cases.json").read_text())["cases"]
    store = demo_store()
    snap = store.project_snapshot(PROJECT)
    replaced_words = set()
    for r in snap["records"].values():
        if r["lifecycle"] in ("superseded", "retracted"):
            replaced_words |= set(tokens(r["title"]))

    def run(predict) -> dict:
        tp = fp = fn = tn = 0
        wrong = []
        for c in cases:
            flagged = predict(c)
            should = c["expect"] != "clear"
            if flagged and should:
                tp += 1
            elif flagged and not should:
                fp += 1
                wrong.append({"kind": "false alarm", "text": c["text"], "note": c.get("note", "")})
            elif not flagged and should:
                fn += 1
                wrong.append({"kind": "missed", "text": c["text"], "note": c.get("note", "")})
            else:
                tn += 1
        return {"flagged_correctly": tp, "false_alarms": fp, "missed": fn, "correctly_clear": tn,
                "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                "recall": round(tp / (tp + fn), 3) if tp + fn else None, "wrong": wrong}

    def nexus_check(c: dict) -> bool:
        stmt = {k: c[k] for k in ("text", "subject", "value") if k in c}
        return check_statements(snap, [stmt])["results"][0]["verdict"] in BLOCKING

    return {
        "cases": len(cases),
        "should_block": sum(c["expect"] != "clear" for c in cases),
        "nexus_check": run(nexus_check),
        "baseline_flag_nothing": run(lambda c: False),
        "baseline_any_shared_word": run(lambda c: bool(set(tokens(c["text"])) & replaced_words)),
    }


# ------------------------------------------------------- synthetic project builder

TOPICS = ["cache", "queue", "billing", "search", "auth", "upload", "export", "report", "invoice", "session", "webhook",
          "retry", "schema", "index", "tenant", "audit", "token", "locale", "feature", "deploy", "backup", "metric",
          "alert", "quota", "worker", "render", "thumbnail", "digest", "mailer", "ledger"]
VERBS = ["uses", "stores", "limits", "validates", "batches", "encrypts", "caches", "retries", "logs", "rejects"]
OBJECTS = ["requests", "rows", "files", "jobs", "events", "payloads", "records", "keys", "pages", "messages"]
KINDS = ["finding", "decision", "assumption", "note", "finding", "decision"]


def synthetic(n: int, rng: random.Random) -> tuple[MemoryStore, list[dict]]:
    """A project of ``n`` records. Every tenth record carries a unique marker word and is a retrieval target."""
    store = MemoryStore(":memory:")
    store.create_project("Synthetic", objective="Seeded benchmark project")
    targets = []
    for i in range(n):
        topic, verb, obj = rng.choice(TOPICS), rng.choice(VERBS), rng.choice(OBJECTS)
        title = f"The {topic} service {verb} {obj} in group {i % 37}"
        content = f"Observed in run {i}. The {rng.choice(TOPICS)} path {rng.choice(VERBS)} {rng.choice(OBJECTS)}."
        if i % 10 == 0:
            marker = f"zq{i:05d}x"
            content += f" Tracked under reference {marker}."
        rec = store.save_record("Synthetic", kind=rng.choice(KINDS), title=title, content=content,
                                origin_model=rng.choice(["claude", "chatgpt", "gemini", "human"]),
                                importance=rng.choice(["low", "normal", "normal", "high"]))
        if i % 10 == 0:
            targets.append({"id": rec["id"], "marker": marker, "topic": topic, "verb": verb, "obj": obj,
                            "group": i % 37})
    return store, targets


# -------------------------------------------------------------------- 2. conflicts

def conflicts() -> dict:
    rng = random.Random(SEED)
    store, _ = synthetic(120, rng)
    planted = {"explicit_link": [], "same_key_different_value": [], "prose_only": []}
    for i in range(10):
        a = store.save_record("Synthetic", kind="finding", title=f"Batch size for importer {i} is 500", origin_model="claude")
        b = store.save_record("Synthetic", kind="finding", title=f"Batch size for importer {i} is 50", origin_model="chatgpt",
                              relations=[{"type": "contradicts", "target_id": a["id"]}])
        planted["explicit_link"].append({a["id"], b["id"]})
    for i in range(10):
        a = store.save_record("Synthetic", kind="finding", title=f"Timeout for client {i}", origin_model="claude",
                              subject=f"client{i}.timeout_s", value="30")
        b = store.save_record("Synthetic", kind="finding", title=f"Client {i} waits a minute", origin_model="gemini",
                              subject=f"client{i}.timeout_s", value="60")
        planted["same_key_different_value"].append({a["id"], b["id"]})
    for i in range(10):
        a = store.save_record("Synthetic", kind="decision", title=f"Region {i} data must stay in the EU", origin_model="human")
        b = store.save_record("Synthetic", kind="decision", title=f"Replicate region {i} data to the US for failover",
                              origin_model="cursor")
        planted["prose_only"].append({a["id"], b["id"]})
    report = analyze_gaps(store.project_snapshot("Synthetic"))
    found = [set(g["record_ids"]) for g in report["gaps"]
             if g["type"] in ("contradiction", "possible_contradiction", "challenged_claim")]
    out = {"planted_per_class": 10, "reported_conflicts": len(found)}
    for name, pairs in planted.items():
        out[name] = {"found": sum(any(p <= f for f in found) for p in pairs), "of": len(pairs)}
    all_pairs = [p for pairs in planted.values() for p in pairs]
    out["false_reports"] = sum(not any(p <= f for p in all_pairs) for f in found)
    return out


# --------------------------------------------------------------------- 3. briefing

def briefing() -> dict:
    store = demo_store()
    snap = store.project_snapshot(PROJECT)
    context = json.dumps(build_project_context(snap), default=str)
    export = json.dumps(store.export(PROJECT), default=str)
    sizes = {"full_export_tokens": estimate_tokens(export), "structured_context_tokens": estimate_tokens(context)}
    for budget in (600, 1200, 2400):
        b = build_briefing(snap, budget_tokens=budget)
        sizes[f"briefing_{budget}"] = {"tokens": b["token_estimate"], "records": len(b["record_refs"]),
                                       "left_out": sum(b["omitted"].values())}
    strict = build_briefing(snap, policy="confirmed")
    sizes["briefing_confirmed_only"] = {"tokens": strict["token_estimate"], "records": len(strict["record_refs"])}
    sizes["records_in_project"] = len(snap["records"])
    sizes["tokens_estimated_as"] = "characters divided by four"
    return sizes


# -------------------------------------------------------------------- 4. retrieval

def retrieval() -> dict:
    rng = random.Random(SEED)
    store, targets = synthetic(2000, rng)
    sample = targets[::4]

    def measure(make_query) -> dict:
        first = top5 = 0
        for t in sample:
            hits = [h["id"] for h in store.search(make_query(t), project="Synthetic", limit=5)["results"]]
            first += bool(hits) and hits[0] == t["id"]
            top5 += t["id"] in hits
        return {"first": round(first / len(sample), 3), "top_five": round(top5 / len(sample), 3)}

    recent = [h["id"] for h in store.search("", project="Synthetic", limit=5)["results"]]
    return {
        "records": 2000, "queries": len(sample),
        "exact_reference": measure(lambda t: t["marker"]),
        "descriptive_words": measure(lambda t: f"{t['topic']} {t['verb']} {t['obj']} group {t['group']}"),
        "baseline_most_recent_five": {"top_five": round(sum(t["id"] in recent for t in sample) / len(sample), 3)},
        "note": "Descriptive queries are ambiguous by construction: many records share the same topic, verb and "
                "object, so the planted target is one of several equally good matches.",
    }


# ------------------------------------------------------------------------ 5. speed

def speed() -> dict:
    out = {}
    for n in (200, 2000):
        store, _ = synthetic(n, random.Random(SEED))

        def timed(fn, repeat: int = 15) -> dict:
            runs = []
            for _ in range(repeat):
                t = time.perf_counter()
                fn()
                runs.append((time.perf_counter() - t) * 1000)
            runs.sort()
            return {"median_ms": round(statistics.median(runs), 2), "p95_ms": round(runs[int(len(runs) * .95) - 1], 2)}

        snap = store.project_snapshot("Synthetic")
        out[f"{n}_records"] = {
            "snapshot": timed(lambda: store.project_snapshot("Synthetic")),
            "briefing": timed(lambda: build_briefing(snap)),
            "review": timed(lambda: analyze_gaps(snap)),
            "plan_check_3_steps": timed(lambda: check_statements(snap, ["The cache service uses rows", "Encrypt the "
                                                                        "billing payloads", "Rename the README"])),
            "search": timed(lambda: store.search("billing encrypts payloads", project="Synthetic")),
        }
    return out


def deterministic() -> dict:
    return {"plan_check": plan_check(), "conflicts": conflicts(), "briefing": briefing(), "retrieval": retrieval()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare deterministic results with results.json")
    args = ap.parse_args()
    results = deterministic()
    target = ROOT / "results.json"
    if args.check:
        stored = json.loads(target.read_text())
        same = all(stored.get(k) == v for k, v in results.items())
        print("benchmarks reproduce" if same else "benchmarks differ from results.json; run benchmarks/run.py and review")
        return 0 if same else 1
    results["speed"] = speed()
    results["environment"] = {"python": sys.version.split()[0], "platform": sys.platform, "seed": SEED,
                              "note": "Timings are from one machine and one run; treat them as orders of magnitude."}
    target.write_text(json.dumps(results, indent=1) + "\n")
    print(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
