"""Deterministic import of a pasted conversation summary.

The full text is always stored unchanged as a source. If it follows the simple
structured-markdown format below, records are extracted from it; otherwise only
the source is stored. Imported text is parsed as data and never executed.

    ---
    project: Environmental AI Research
    title: ChatGPT feasibility session
    origin_model: chatgpt
    conversation_id: chatgpt-2026-10-28
    source_kind: conversation
    ---
    ## Objective
    Free text...

    ## Findings
    - [F1] Short title
      Longer explanation (any indented line without a known key).
      importance: high
      status: evidence_supported
      subject: sentinel2.spatial_resolution_m
      value: 10
      evidence: https://example.org/page | Citation text
      supports: D1
    ## Decisions / Assumptions / Hypotheses / Open Questions / Tasks / Evidence / Notes
      ... same item format; relation keys (depends_on, supports, challenges, contradicts,
      requires, answers, relates_to, supersedes) take comma-separated local keys
      like F1 or existing record ids.
    ## Any other heading, e.g. "Applications and statuses"
    - [A1] Items under it are kept as notes tagged with the section name.
      kind: decision      (an item can always set its own type)

No item is ever dropped: every "- [KEY] Title" line becomes a record, and the result lists any section whose
type had to be guessed.
"""

from __future__ import annotations

import re
from typing import Any

from . import models as m
from .store import MemoryStore

SECTION_KINDS = {
    "objective": "objective", "objectives": "objective", "goal": "objective", "goals": "objective",
    "finding": "finding", "findings": "finding", "conclusions": "finding", "results": "finding",
    "decision": "decision", "decisions": "decision",
    "assumption": "assumption", "assumptions": "assumption",
    "hypothesis": "hypothesis", "hypotheses": "hypothesis",
    "open questions": "question", "questions": "question", "uncertainties": "question",
    "remaining uncertainties": "question",
    "tasks": "task", "next steps": "task",
    "evidence": "evidence", "notes": "note",
}
META_KEYS = {"importance", "status", "subject", "value", "evidence", "tags", "kind"} | set(m.RELATION_TYPES)
DEFAULT_KIND = "note"
_ITEM_RE = re.compile(r"^- \[(?P<key>[A-Za-z0-9_.-]+)\]\s+(?P<title>.+)$")
_META_RE = re.compile(r"^\s+(?P<k>[a-z_]+):\s*(?P<v>.*)$")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, text[end + 4:].lstrip("\n")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def parse_items(body: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (objective paragraphs, item dicts) from the markdown body.

    Items under a heading that names no known kind are kept as notes tagged with that heading, so a model's own
    section names never cause data to be dropped.
    """
    objective_lines: list[str] = []
    items: list[dict[str, Any]] = []
    kind: str | None = None
    section: str | None = None
    current: dict[str, Any] | None = None
    for line in body.splitlines():
        heading = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if heading:
            section = heading.group(1).strip()
            kind = SECTION_KINDS.get(section.lower())
            current = None
            continue
        item = _ITEM_RE.match(line)
        if item:
            current = {"key": item["key"], "kind": kind or DEFAULT_KIND, "guessed": kind is None,
                       "section": section, "title": item["title"].strip(), "content": [],
                       "meta": {}, "evidence": [], "raw": [line]}
            items.append(current)
            continue
        if kind is None and current is None:
            continue
        if current is not None and (line.startswith("  ") or not line.strip()):
            current["raw"].append(line)
            meta = _META_RE.match(line)
            if meta and meta["k"] in META_KEYS:
                if meta["k"] == "evidence":
                    url, _, cite = meta["v"].partition("|")
                    url, cite = url.strip(), cite.strip()
                    if not url.startswith("http"):
                        url, cite = "", meta["v"].strip()
                    current["evidence"].append({"url": url or None, "citation": cite or None})
                else:
                    current["meta"][meta["k"]] = meta["v"].strip()
            elif line.strip():
                current["content"].append(line.strip())
            continue
        current = None
        if kind == "objective" and line.strip():
            objective_lines.append(line.strip())
    return objective_lines, items


def import_text(
    store: MemoryStore,
    text: str,
    *,
    project: str | None = None,
    origin_model: str | None = None,
    conversation_id: str | None = None,
    title: str | None = None,
    source_kind: str | None = None,
    created_by: str = "importer",
) -> dict[str, Any]:
    meta, body = parse_frontmatter(text)
    project = project or meta.get("project")
    if not project:
        raise m.ValidationError("No project given (pass project=... or add 'project:' front matter)")
    origin_model = origin_model or meta.get("origin_model")
    conversation_id = conversation_id or meta.get("conversation_id")
    title = title or meta.get("title") or "Imported text"
    source_kind = source_kind or meta.get("source_kind") or "conversation"

    objective_lines, items = parse_items(body)
    proj, project_created = store.ensure_project(project, objective=" ".join(objective_lines))
    source, source_created = store.add_source(
        proj["id"], text, kind=source_kind, title=title, origin_model=origin_model, conversation_id=conversation_id
    )
    result: dict[str, Any] = {
        "project": {"id": proj["id"], "name": proj["name"], "created": project_created},
        "source": {"id": source["id"], "created": source_created},
        "records_created": [], "relations_created": 0, "warnings": [],
    }
    if not source_created:
        result["warnings"].append("Identical text was already imported; nothing new was extracted.")
        return result
    if not proj["objective"] and objective_lines:
        store.update_project(proj["id"], objective=" ".join(objective_lines))
    if objective_lines:
        items.insert(0, {"key": "_objective", "kind": "objective", "title": _first_sentence(objective_lines),
                         "content": objective_lines, "meta": {"importance": "high"}, "evidence": [],
                         "raw": objective_lines})

    guessed: dict[str, int] = {}
    key_to_id: dict[str, str] = {}
    for it in items:
        if it["key"] in key_to_id:
            result["warnings"].append(f"Duplicate key [{it['key']}] skipped")
            continue
        status = it["meta"].get("status", "unverified")
        if status == "evidence_supported" and not it["evidence"]:
            result["warnings"].append(f"[{it['key']}] claims evidence_supported without evidence; stored as unverified")
            status = "unverified"
        tags = [t.strip() for t in it["meta"].get("tags", "").split(",") if t.strip()]
        own_kind = it["meta"].get("kind", "").strip().lower()
        if own_kind in m.RECORD_KINDS:
            it["kind"], it["guessed"] = own_kind, False
        elif own_kind:
            result["warnings"].append(f"[{it['key']}] unknown kind '{own_kind}'; stored as {it['kind']}")
        if it.get("section") and it.get("guessed"):
            tags.append(_slug(it["section"]))
            guessed[it["section"]] = guessed.get(it["section"], 0) + 1
        it["title"], it["content"] = split_long_title(it["title"], it["content"])
        rec = store.save_record(
            proj["id"], kind=it["kind"], title=it["title"], content="\n".join(it["content"]),
            verification=status, importance=it["meta"].get("importance", "normal"),
            subject=it["meta"].get("subject"), value=it["meta"].get("value"), tags=tags,
            origin_model=origin_model, conversation_id=conversation_id, created_by=created_by,
            source_id=source["id"], source_excerpt="\n".join(it["raw"]).strip(),
            evidence=[{**e, "source_id": source["id"]} for e in it["evidence"]],
        )
        key_to_id[it["key"]] = rec["id"]
        result["records_created"].append({"key": it["key"], "id": rec["id"], "kind": it["kind"], "title": it["title"]})
    for section, n in guessed.items():
        result["warnings"].append(
            f"Section '{section}' is not a record type; its {n} item(s) were saved as notes tagged "
            f"'{_slug(section)}'. Add 'kind: finding' (or decision, assumption, question, task...) under an item "
            "to set its type.")

    for it in items:
        rid = key_to_id.get(it["key"])
        links = []
        for rel_type in m.RELATION_TYPES:
            for target in filter(None, (t.strip() for t in it["meta"].get(rel_type, "").split(","))):
                target_id = key_to_id.get(target, target)
                try:
                    m.require_id(target_id)
                except m.ValidationError:
                    result["warnings"].append(f"[{it['key']}] {rel_type} {target}: unknown key, skipped")
                    continue
                links.append({"type": rel_type, "target_id": target_id})
        if rid and links:
            store.update_record(rid, reason="Relations from import", changed_by=created_by, add_relations=links)
            result["relations_created"] += len(links)
    return result


TITLE_MAX = 300


def split_long_title(title: str, content: list[str]) -> tuple[str, list[str]]:
    """Titles are one line of at most 300 characters. A longer item keeps its first sentence as the title and
    its full wording in the content, so nothing is cut."""
    if len(title) <= TITLE_MAX:
        return title, content
    short = _first_sentence([title])
    if len(short) > TITLE_MAX - 1 or short == title:
        short = title[:TITLE_MAX - 1].rsplit(" ", 1)[0] + "…"
    return short, [title, *content]


def _first_sentence(lines: list[str]) -> str:
    text = " ".join(lines)
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first[:200]
