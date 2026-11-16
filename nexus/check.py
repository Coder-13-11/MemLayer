"""Preflight: check what an agent or person is about to do against what the project has already settled.

Deterministic and deliberately narrow. Research on contradiction detection is clear that even LLM judges
reach high precision only with low recall, and that a noisy checker gets muted. So this reports three things
and keeps them apart:

  conflicts   the statement names a subject and value, and an active record gives the same subject a
              different value. Structural, so it is reported as a conflict.
  replaced    the statement closely resembles a record that was superseded or retracted: the team already
              moved away from this. Wording-based, reported as "resembles", with the replacement.
  related     active decisions, assumptions and findings that share the statement's vocabulary. Not judged:
              the caller (a model or a person) reads them and decides.

It never says a plan is correct. "clear" means only that nothing above was found.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable, Mapping

from .models import ValidationError, normalize_key

RESEMBLES = 0.6     # similarity at which a statement "resembles" a replaced record
RELATED = 0.3       # similarity at which an active record is listed as related
MAX_STATEMENTS = 25
_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with we our "
    "should use using used can could would not no do does all any into than then so if but about after before "
    "when which who what how why there their they them these those i you he she may must also only more most".split())
LIMITATIONS = [
    "Conflicts are found only when the statement gives a subject and value that an active record also uses.",
    "'Resembles a replaced record' compares wording. A statement that rejects the old approach can resemble it "
    "too, and a paraphrase with different words will be missed.",
    "Related records are listed, not judged. No language model is involved in this check.",
]


def _stem(word: str) -> str:
    for suffix in ("ingly", "edly", "ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9][a-z0-9_.+#-]*", (text or "").lower()) if t not in _STOP]


class _Index:
    """Inverse document frequency over a project's records, so shared rare words count for more than common ones."""

    def __init__(self, texts: Iterable[str]):
        docs = [set(tokens(t)) for t in texts]
        self.n = max(len(docs), 1)
        self.df = Counter(t for d in docs for t in d)

    def weight(self, token: str) -> float:
        return math.log(1 + self.n / (1 + self.df.get(token, 0))) + 0.1

    def similarity(self, a: str, b: str) -> float:
        """Weighted share of the shorter text's vocabulary that the other also uses. Needs two shared terms."""
        ta, tb = set(tokens(a)), set(tokens(b))
        shared = ta & tb
        if len(shared) < 2:
            return 0.0
        wa, wb = sum(map(self.weight, ta)), sum(map(self.weight, tb))
        return sum(map(self.weight, shared)) / min(wa, wb)


def _brief(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "kind": r["kind"], "title": r["title"], "status": r["status"],
            "origin_model": r["origin_model"], "confirmed": bool(r.get("confirmed_by"))}


def check_statements(snapshot: dict[str, Any], statements: Iterable[Any], *, max_related: int = 5) -> dict[str, Any]:
    """``statements`` are strings, or objects ``{"text", "subject"?, "value"?}``."""
    items: list[dict[str, Any]] = []
    for s in statements:
        s = {"text": s} if isinstance(s, str) else dict(s) if isinstance(s, Mapping) else None
        if not s or not isinstance(s.get("text"), str) or not s["text"].strip():
            raise ValidationError("each statement needs non-empty text")
        if len(s["text"]) > 2000:
            raise ValidationError("a statement is at most 2000 characters; check one claim or step at a time")
        items.append(s)
    if not items:
        raise ValidationError("give at least one statement to check")
    if len(items) > MAX_STATEMENTS:
        raise ValidationError(f"check at most {MAX_STATEMENTS} statements at a time")

    recs: dict[str, dict[str, Any]] = snapshot["records"]
    index = _Index(f"{r['title']} {r['content']}" for r in recs.values())
    active = [r for r in recs.values() if r["lifecycle"] == "active"]
    gone = [r for r in recs.values() if r["lifecycle"] in ("superseded", "retracted")]

    def score(text: str, r: dict[str, Any]) -> float:
        return max(index.similarity(text, r["title"]), 0.8 * index.similarity(text, f"{r['title']} {r['content']}"))

    results = []
    for s in items:
        text = s["text"].strip()
        conflicts, replaced, related = [], [], []
        subject, value = normalize_key(s.get("subject")), normalize_key(s.get("value"))
        if subject and value:
            for r in active:
                if normalize_key(r["subject"]) == subject and r["value"] and normalize_key(r["value"]) != value:
                    conflicts.append({**_brief(r), "subject": r["subject"], "recorded_value": r["value"],
                                      "stated_value": s["value"]})
        for r in gone:
            sim = score(text, r)
            if sim >= RESEMBLES:
                newer = recs.get(r.get("superseded_by") or "")
                replaced.append({**_brief(r), "similarity": round(sim, 2),
                                 "replaced_by": _brief(newer) if newer else None})
        for r in active:
            if r["kind"] in ("decision", "assumption", "hypothesis", "finding", "question"):
                sim = score(text, r)
                if sim >= RELATED:
                    related.append({**_brief(r), "similarity": round(sim, 2)})
        replaced.sort(key=lambda x: -x["similarity"])
        related.sort(key=lambda x: (-x["similarity"], x["id"]))
        verdict = "conflict" if conflicts else "resembles_replaced" if replaced else "review" if related else "clear"
        results.append({"statement": text, "verdict": verdict, "conflicts": conflicts, "resembles_replaced": replaced,
                        "related": related[:max_related]})

    order = ("conflict", "resembles_replaced", "review", "clear")
    return {
        "project": {"id": snapshot["project"]["id"], "name": snapshot["project"]["name"]},
        "verdict": min((r["verdict"] for r in results), key=order.index),
        "results": results,
        "meaning": {"conflict": "an active record gives this subject a different value",
                    "resembles_replaced": "close in wording to a record the team superseded or withdrew",
                    "review": "related active records exist; read them before proceeding",
                    "clear": "nothing was found; this is not confirmation that the statement is right"},
        "method": "deterministic: subject/value comparison and weighted word overlap; no language model",
        "limitations": LIMITATIONS,
    }
