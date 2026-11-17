"""Regenerate nexus/static/site/demo.json: the plan-check results shown on the landing page.

The landing page's "check a plan" demo is not hand-written. This script loads the example project into an
in-memory store, runs the product's real check on five planned steps, and writes what came back. Run it again
whenever check.py or demo.py changes:

    python scripts/build_site_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from nexus.check import check_statements
from nexus.demo import PROJECT, load_demo
from nexus.store import MemoryStore

# The short form numbers printed on the landing page's example ledger, by example-record key.
SERIAL = {"jest": "0398", "sdk": "0402", "money": "0405", "port": "0409", "vitest": "0412", "a_hooks": "0419",
          "f_hooks": "0431", "lat_a": "0440", "lat_b": "0441", "q_refund": "0447", "idem": "0415", "a_guest": "0422",
          "guest": "0424", "ledger": "0450", "q_tax": "0452", "tax": "0455", "f_ci": "0410", "t_flags": "0460"}
STEPS = [
    "Add Jest tests for the cart reducer",
    "Call the Stripe SDK directly from the refund handler",
    {"text": "Store prices as floats", "subject": "money.representation", "value": "float"},
    "Poll for payment webhooks for 30 seconds",
    "Rename the README",
]
STAMP = {"conflict": ("Conflicts with a decision", "bad"), "resembles_replaced": ("Resembles a replaced record", "bad"),
         "review": ("Related records to read", "warn"), "clear": ("Nothing found", "ok")}
FINE = {"conflict": "Exit code 2 from nexus check, so a CI step can fail on it.",
        "resembles_replaced": "Exit code 2 from nexus check, so a CI step can fail on it.",
        "review": "Not judged. The agent is told to read these before it goes on.",
        "clear": "Nothing in the record matches. That is not confirmation the step is right."}


def build() -> dict:
    store = MemoryStore(":memory:")
    ids = load_demo(store)["ids"]
    serial = {rid: SERIAL[key] for key, rid in ids.items()}
    out = check_statements(store.project_snapshot(PROJECT), STEPS)

    def ref(r: dict) -> str:
        return f"{serial[r['id']]}, {r['title']}" + (" (confirmed)" if r.get("confirmed") else "")

    results = []
    for r in out["results"]:
        lines = []
        for c in r["conflicts"]:
            lines += [["Recorded", ref(c)], ["It says", f"{c['subject']} = {c['recorded_value']}"],
                      ["This says", f"{c['subject']} = {c['stated_value']}"]]
        for x in r["resembles_replaced"]:
            lines.append(["Replaced", ref(x)])
            if x["replaced_by"]:
                lines.append(["Now", ref(x["replaced_by"])])
        for x in r["related"][:2]:
            lines.append(["Related", ref(x) if x.get("confirmed") else f"{ref(x)} ({x['status'].replace('_', ' ')})"])
        results.append({"step": r["statement"], "verdict": r["verdict"], "stamp": STAMP[r["verdict"]][0],
                        "tone": STAMP[r["verdict"]][1], "lines": lines, "fine": FINE[r["verdict"]]})
    return {"generated_by": "scripts/build_site_demo.py", "method": out["method"], "results": results}


def main() -> None:
    data = build()
    target = Path(__file__).resolve().parent.parent / "nexus" / "static" / "site" / "demo.json"
    target.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    for r in data["results"]:
        print(f"{r['verdict']:<20} {r['step']}")
        for label, text in r["lines"]:
            print(f"    {label}: {text}")


if __name__ == "__main__":
    main()
