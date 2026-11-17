"""The example project: a small, invented engineering project that shows what NEXUS is for.

Everything here is synthetic. The team, the numbers and the links (example.com) do not exist. It is loaded on
request into a workspace so a new team can see a populated ledger, and it is what the benchmarks and the
screenshots use, so anyone can reproduce them.

The story: a team is rebuilding its checkout with three AI coding tools. Some decisions were replaced, two
tools disagree about a measurement, an assumption turned out to be wrong, and one decision still rests on an
unanswered question.
"""

from __future__ import annotations

from typing import Any

from .models import ValidationError

PROJECT = "Checkout rebuild (example)"
OBJECTIVE = ("Replace the legacy checkout with a service the team can change safely: one payment path, "
             "prices that never drift by a cent, and a test suite that finishes in under five minutes.")
REVIEWER = "maya@example.com"

# key, kind, title, content, origin, extras
RECORDS: list[tuple[str, str, str, str, str, dict[str, Any]]] = [
    ("jest", "decision", "Use Jest for unit tests",
     "Chosen at kickoff because the legacy app already used it.", "chatgpt",
     {"subject": "test.framework", "value": "jest", "importance": "high"}),
    ("f_ci", "finding", "Jest's ESM transform adds about 40 seconds to every CI run",
     "Measured over 30 runs on the main branch after the move to native ES modules.", "claude-code",
     {"evidence": [{"url": "https://example.com/acme/checkout/pull/412", "citation": "PR 412: CI timing table",
                    "checked_at": "2026-06-03"}], "verification": "evidence_supported"}),
    ("vitest", "decision", "Use Vitest for unit tests",
     "Replaces Jest. Native ESM support removes the transform step; the existing tests ran unchanged except "
     "for three mocks.", "claude-code",
     {"subject": "test.framework", "value": "vitest", "importance": "high",
      "relations": [("supersedes", "jest", "Jest was too slow under ESM"), ("depends_on", "f_ci", None)],
      "confirm": True}),
    ("money", "decision", "Store money as integer minor units, never floats",
     "All amounts are integers in the currency's minor unit (cents). Formatting happens only at the edge.",
     "human", {"importance": "high", "subject": "money.representation", "value": "integer minor units",
               "evidence": [{"url": "https://example.com/acme/handbook/money", "citation": "Engineering handbook: money"}],
               "verification": "evidence_supported", "confirm": True}),
    ("port", "decision", "All payment calls go through the PaymentsPort adapter",
     "Feature code never imports the payment provider's SDK. This keeps the provider swappable and makes "
     "payment paths testable with a fake.", "claude-code", {"importance": "high", "confirm": True}),
    ("sdk", "decision", "Call the Stripe SDK directly from checkout handlers",
     "Proposed for speed during the first spike.", "cursor",
     {"relations": [], "superseded_by": "port", "supersede_reason": "Replaced after the spike: direct SDK calls "
      "could not be tested without network access."}),
    ("a_hooks", "assumption", "Payment webhooks arrive within 30 seconds",
     "The order confirmation page polls for 30 seconds and then shows a failure.", "chatgpt",
     {"importance": "high"}),
    ("f_hooks", "finding", "During the 14 August incident, webhook delivery took up to 4 minutes",
     "Provider status page and our own logs agree: p99 delivery delay was 3m52s for about an hour.", "human",
     {"evidence": [{"url": "https://example.com/acme/incidents/2026-08-14", "citation": "Incident review, 14 Aug 2026",
                    "checked_at": "2026-08-16"}], "verification": "evidence_supported",
      "relations": [("challenges", "a_hooks", "Observed delays are eight times the assumed limit")], "confirm": True}),
    ("idem", "decision", "Send an idempotency key with every payment-creating request",
     "The key is the order id plus the attempt number, so a retried request can never charge twice.",
     "claude-code", {"importance": "high", "relations": [("depends_on", "a_hooks", None)]}),
    ("lat_a", "finding", "Cart service p95 latency is 180 ms",
     "From the load test on the staging cluster.", "cursor",
     {"subject": "cart.p95_latency_ms", "value": "180"}),
    ("lat_b", "finding", "Cart service p95 latency is 420 ms",
     "From production traces over the last seven days.", "gemini",
     {"subject": "cart.p95_latency_ms", "value": "420"}),
    ("a_guest", "assumption", "Guest checkout is under 20% of orders",
     "Taken from a planning document; nobody has checked the analytics.", "chatgpt", {}),
    ("guest", "decision", "Build account checkout first and guest checkout second",
     "", "chatgpt", {"relations": [("depends_on", "a_guest", None)]}),
    ("q_refund", "question", "Do we need partial refunds at launch?",
     "Support asked for it; finance has not answered.", "human", {"importance": "high"}),
    ("ledger", "decision", "Model refunds as negative ledger entries, not as edits to the order",
     "", "claude-code", {"relations": [("depends_on", "q_refund", None)]}),
    ("q_tax", "question", "Which service owns tax calculation?", "", "cursor", {}),
    ("tax", "finding", "Tax is calculated by the pricing service; checkout only displays it",
     "Confirmed with the pricing team's API contract.", "human",
     {"relations": [("answers", "q_tax", None)],
      "evidence": [{"url": "https://example.com/acme/pricing/api#tax", "citation": "Pricing API contract, section 4"}],
      "verification": "evidence_supported"}),
    ("t_flags", "task", "Remove the legacy checkout feature flag after two clean weeks", "", "human", {}),
]


def load_demo(store: Any) -> dict[str, Any]:
    """Create the example project in a workspace's memory. Refuses if it is already there."""
    if any(p["name"] == PROJECT for p in store.list_projects()):
        raise ValidationError("The example project is already in this workspace.")
    project = store.create_project(PROJECT, "An invented project that shows a populated ledger. Safe to delete.",
                                   OBJECTIVE)
    ids: dict[str, str] = {}
    later: list[tuple[str, str, str]] = []
    for key, kind, title, content, origin, extra in RECORDS:
        relations = [{"type": t, "target_id": ids[target], "note": note}
                     for t, target, note in extra.get("relations", []) if target in ids]
        rec = store.save_record(
            project["id"], kind=kind, title=title, content=content, origin_model=origin,
            created_by=REVIEWER if origin == "human" else f"{origin} (example)",
            verification=extra.get("verification", "unverified"), importance=extra.get("importance", "normal"),
            subject=extra.get("subject"), value=extra.get("value"), evidence=extra.get("evidence", []),
            relations=relations,
            source_text=f"{title}\n\n{content}" if content else None)
        ids[key] = rec["id"]
        if extra.get("confirm"):
            store.update_record(rec["id"], reason="Reviewed in the weekly ledger review", changed_by=REVIEWER,
                                confirm=REVIEWER)
        if extra.get("superseded_by"):
            later.append((key, extra["superseded_by"], extra["supersede_reason"]))
    for old, new, reason in later:
        store.update_record(ids[old], reason=reason, changed_by=REVIEWER, superseded_by=ids[new])
    return {"project": project, "records": len(ids), "ids": ids}
