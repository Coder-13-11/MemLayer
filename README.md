# MemLayer — shared AI memory and knowledge-gap layer (prototype v0.3)

> "MemLayer" is a working name. Rename the product everywhere with one setting: see *Renaming*.

A local, model-independent project memory that any MCP-compatible AI client can read from and write to,
with a deterministic analyzer that finds contradictions, missing evidence, unexamined assumptions,
broken dependencies and open questions — each one citing the records it is based on.

- **Local-first:** one SQLite file (`~/.memlayer/memory.db` by default). No hosted services, paid APIs or LLM calls.
- **Evidence-aware:** every record has a verification status, an origin model and conversation, a link to the
  verbatim source text it came from, attached evidence with `checked_at` dates, and a version history.
- **Non-destructive:** corrections and supersessions snapshot the previous version. Read tools run under
  SQLite `query_only` and cannot modify memory. Only a person can delete, through the CLI.

## Setup

Requires Python ≥ 3.10. Dependencies: the MCP SDK, Starlette and uvicorn (the latter two already come with the SDK). The `mcp` SDK is pinned to 1.x (`>=1.10,<2`): SDK 2.x changed its API and has not been
tested with this code yet.

```bash
python3 -m venv .venv
```
```bash
source .venv/bin/activate
```
```bash
pip install -e ".[dev]"
```
```bash
pytest -q
```

## Web dashboard

```bash
memlayer web
```

Then open <http://127.0.0.1:8765>. It reads and writes the same database as the MCP server
(`~/.memlayer/memory.db`, or `--db` / `MEMLAYER_DB`), so anything an AI client saves shows up in the open
page within about a second, with a notice naming the model that saved it.

- **Sidebar:** projects, each with a small *trust bar* (how much of what was saved is evidence-supported),
  a live-connection indicator, and a light/dark toggle.
- **Search (⌘K):** one search across every project's records and original source text.
- **Overview:** objective, contributing models, the full trust bar (click a segment to filter), what needs
  attention, live activity, decisions and open questions.
- **Records:** a sortable, filterable table (type, status, origin model, keyword). Selecting a row opens the
  record panel: provenance, evidence, connections, original wording, and full version history. From the
  panel you can edit (with a reason), change status, add evidence, link, supersede, or delete.
- **Graph:** an interactive knowledge map. Fill colour shows the record type, the ring shows its status, and
  edges show relations. Drag, pan, zoom; hover to focus a node's neighbours; click to open the record.
- **Gaps:** the analyzer's findings by severity, with confidence labels, the records they cite, conflicts
  already resolved, and the analyzer's stated limits.
- **Sources:** every imported conversation, stored word for word, with the records extracted from it.
  **Import** takes pasted text from ChatGPT or anywhere else.
- **Activity:** everything saved, changed, linked or imported, by any client.

The dashboard listens on 127.0.0.1 only. It refuses requests whose `Host` is not local (a DNS-rebinding
guard), and every write must carry an `X-MemLayer` header, which a cross-site page cannot send without a
CORS approval this server never gives. Stored text is always rendered as text, never as HTML.

### AI review ("AI-powered" detection, without an API key)

The built-in analyzer follows fixed rules. For contradictions that only show in the wording, the MCP server
provides a `review_project` prompt. In Claude Code:

```
/mcp__memlayer__review_project "Environmental AI Research"
```

The model reads the project through the memory tools and records each conflict it is confident about as a
`contradicts` / `challenges` link, with a note, through `update_memory`. Uncertain ones become `question`
records. Those links then appear on the Gaps page and in the graph, attributed to that model. The review runs
in your own AI client; the product itself calls no paid API.

## Quick tour (CLI)

```bash
memlayer import samples/chatgpt_feasibility_session.md
```
```bash
memlayer import samples/followup_notes.md
```
```bash
memlayer context "Environmental AI Research"
```
```bash
memlayer gaps "Environmental AI Research"
```
```bash
memlayer search "revisit" --project "Environmental AI Research"
```
```bash
memlayer show rec_XXXXXXXXXXXX
```

Human-control commands: `set-status` (approve or reject: `evidence_supported`, `challenged`, `retracted`, ...),
`edit`, `supersede --by`, `delete --yes`, `export out.json`. Use `--db PATH` or `MEMLAYER_DB` to choose the file.

## End-to-end demo over MCP

```bash
python scripts/demo.py
```

This runs the seven workflow steps from the spec against **real MCP server processes over stdio**: create
project → import a synthetic ChatGPT session → extract records → a second client retrieves context → that client
saves a challenging finding → `analyze_gaps` → supersede the outdated claim and confirm retrieval prefers the
newer record. Output from a run is in [demo_output.txt](demo_output.txt).

Each "client" in the demo is a scripted `mcp.ClientSession`, not ChatGPT or Claude itself. It shows the protocol
path works; the per-application notes below show where real clients stand.

## Connecting real AI clients

The server speaks MCP over **stdio only**: it opens no network port.

**Claude Code** (tested path: stdio MCP server):

```bash
claude mcp add memlayer -- /ABSOLUTE/PATH/.venv/bin/python -m memlayer.server --db ~/.memlayer/memory.db
```

**Claude Desktop**: add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memlayer": {
      "command": "/ABSOLUTE/PATH/.venv/bin/python",
      "args": ["-m", "memlayer.server", "--db", "/Users/you/.memlayer/memory.db"]
    }
  }
}
```

**ChatGPT, Claude on the web, Gemini and Perplexity** only reach MCP servers on the internet over HTTPS. Use
the hosted service below for those. Locally you can still paste a conversation into `memlayer import`.

Neither client saves or retrieves memories automatically on every turn. The model decides when to call a tool,
guided by the server's instructions ("call `get_project_context` at the start of work on a project ..."). Asking
explicitly ("check project memory for Environmental AI Research") is the reliable trigger.

## Hosted service (for ChatGPT, Claude.ai, Gemini, Perplexity)

`memlayer hosted` runs the same tools as an internet-facing MCP server with sign-in, for many users:

```bash
memlayer hosted --public-url https://memory.example.com --data-dir /data --port 8000
```

Users paste `https://memory.example.com/mcp` into their AI app. The app registers itself (OAuth dynamic client
registration), opens the sign-in page, where the user signs in or creates an account and approves access, and
then calls the tools with a bearer token. Nothing else needs configuring per user.

| Setting | Meaning |
|---|---|
| `MEMLAYER_PUBLIC_URL` / `--public-url` | The `https://` address users connect to (plain `http` only for localhost) |
| `MEMLAYER_DATA_DIR` / `--data-dir` | `accounts.db` plus one `users/<id>/memory.db` per user |
| `MEMLAYER_SIGNUP_CODE` | If set, creating an account needs this invite code |
| `PORT`, `HOST` | Listen address (default `0.0.0.0:8000`) |

How it works and what protects it:

- **OAuth 2.1 server** (`memlayer/oauth.py`) on the MCP SDK's endpoints: `/register`, `/authorize`, `/token`,
  `/revoke`, plus authorization-server and protected-resource metadata. PKCE is required. Tokens are bound to
  the `/mcp` resource and every MCP request is checked against it.
- **Per-user isolation:** each account has its own SQLite file, chosen from the verified token on every tool call.
  There is no query that can reach another user's file.
- **Secrets at rest:** passwords are scrypt hashes; codes and tokens are stored only as SHA-256 hashes. Codes and
  refresh tokens are single use; refreshing rotates both tokens. Access tokens last 1 hour, refresh tokens 30 days.
- **Sign-in page:** shows which app is asking and which site you'll be sent back to (apps can name themselves
  anything, the return site can't be faked). It rejects cross-site form posts, rate-limits failed attempts
  (10 per 15 minutes per IP and per email) and cannot be framed.
- **Revocation:** each approval is a grant; revoking it kills its tokens immediately.

Public pages: `/` is the landing page (`memlayer/static/site/`), with an interactive demo and a private-beta
invite form; requests are stored in `accounts.db`. List them with:

```bash
memlayer hosted --list-beta --data-dir /data
```

Fonts (STIX Two Text and Archivo, SIL Open Font License) are self-hosted from `memlayer/static/fonts/`, so
visitors' browsers make no third-party requests.

Deploy: a `Dockerfile` and `fly.toml.example` are included. The platform must terminate HTTPS. Keep one
instance with a persistent volume at `/data`, because accounts and memory are files on that volume.

Phase 1 limits: no dashboard for hosted accounts yet (the local `memlayer web` is single-user), no password
reset or email verification, no account export/delete page, the rate limiter is per process, and no backups are
configured. These are the next phases.

## MCP tools

| Tool | R/W | Purpose |
|---|---|---|
| `get_project_context(project, query?, max_per_section?)` | read | Briefing: objective, decisions, findings, assumptions, open questions, superseded history, contributing models, sources, high-severity gaps. `query` narrows it to matching records and their neighbours. |
| `search_memory(query, project?, kinds?, statuses?, include_inactive?, limit?)` | read | BM25 keyword search (FTS5) over records and verbatim sources. |
| `get_memory_details(record_id, include_source_content?)` | read | Full record: evidence, relations in and out, source provenance, version history. |
| `analyze_gaps(project)` | read | Gap report (below). |
| `save_memory(project, kind, title, origin_model, ...)` | write | New record, with optional `source_text` (kept verbatim), `evidence[]`, `relations[]`. |
| `update_memory(record_id, reason, changed_by, ...)` | write | Correct fields, change status, `superseded_by`, `add_evidence`, `add_relations`. The old version goes to history. |
| `import_source(project, text, ...)` | write | Store pasted text verbatim and extract records from structured markdown. |
| `create_project`, `list_projects` | write / read | |
| prompt `review_project(project)` | — | Instructions for the connected model to review a project and record conflicts it finds (see above). |
| `get_review_instructions(project)` | read | The same instructions as a tool, for apps that ignore MCP prompts (ChatGPT). Ask "review my project memory". |

Input schemas are generated from typed signatures (pydantic), so clients see enums for `kind`, `verification`,
relation `type`, etc. Read tools carry `readOnlyHint: true`. Validation rules include:
`evidence_supported` requires at least one evidence item (a model cannot self-verify a claim), URLs must be
http(s), relations must stay inside one project, and every update requires a `reason`.

## Data model

```
projects ─┬─ sources        verbatim original text (sha256-deduplicated, never edited)
          ├─ records        structured claims: kind, title, content, subject/value, importance,
          │                 verification, lifecycle, superseded_by, origin_model, conversation_id,
          │                 source_id + source_excerpt, version
          │    ├─ evidence        url / citation / excerpt / checked_at
          │    └─ record_history  JSON snapshot of every prior version + reason + who
          └─ relations      from_id —type→ to_id
```

- **kind:** objective, finding, decision, assumption, hypothesis, evidence, question, task, note
- **verification:** unverified, evidence_supported, challenged, contradicted
- **lifecycle:** active, superseded, resolved, retracted
- **status** (what readers see) = lifecycle if not active, otherwise verification
- **relation types:** supports, challenges, contradicts, depends_on, requires, supersedes, answers, relates_to.
  Saving a record with `supersedes X` marks X superseded and snapshots it; nothing else changes another record
  automatically.
- **subject / value:** an optional key and claimed value (`sentinel2.revisit_days` = `5`) that lets the analyzer
  compare claims made by different models.

## Gap analyzer

`memlayer/gaps.py` is a pure function over a project snapshot. Rules:

| Gap type | Rule | Confidence |
|---|---|---|
| `contradiction` | explicit `contradicts` relation between two active records | stated |
| `challenged_claim` | active `challenges` relation, unresolved | stated |
| `possible_contradiction` | active records with the same `subject` and different `value` | possible |
| `missing_evidence` | finding / hypothesis / decision with no evidence, no `supports` relation (decisions: no dependencies) | certain |
| `unresolved_assumption` | assumption without evidence, or challenged; high severity if anything depends on it | certain |
| `incomplete_dependency` | `depends_on`/`requires` pointing at a superseded, retracted, challenged, open-question or open-task record | certain |
| `open_question` / `candidate_answer_unreviewed` | active question with no / unreviewed `answers` | certain |

Conflicts settled by supersession, retraction or resolution go to `resolved_conflicts`, not to `gaps`. This is
how the analyzer tells a superseded decision apart from an unresolved contradiction.

**Limitations (also returned in every report):** conflicting prose with no shared subject key or explicit relation
is not detected; `possible` conflicts may be legitimate context differences; evidence is counted, not judged.

## Structured import format

```markdown
---
project: Environmental AI Research
origin_model: chatgpt
conversation_id: chatgpt-2026-10-28
---
## Objective
Free text.

## Findings            (also: Decisions, Assumptions, Hypotheses, Open Questions, Tasks, Evidence, Notes)
- [F2] Sentinel-2 revisits the same location every 5 days
  Any indented line without a known key becomes the record's content.
  importance: high | normal | low
  status: evidence_supported        (downgraded to unverified if no evidence line)
  subject: sentinel2.revisit_days
  value: 5
  evidence: https://example.org/page | Citation text
  depends_on: F1, rec_existing00000   (any relation type; local keys or existing ids)
```

Text without this structure is still stored as a searchable source; it just yields no records. Imported text is
parsed as data and never executed. Re-importing identical text is a no-op.

## Acceptance criteria → tests

| # | Criterion | Test |
|---|---|---|
| 1 | Create project, save research | `test_store.py::test_create_project_and_save_research` |
| 2 | Persists after restart | `test_store.py::test_data_persists_after_reopen`, `test_mcp.py::test_real_stdio_server_persists_across_restart` |
| 3 | MCP client retrieves context | `test_mcp.py::test_two_clients_share_memory` |
| 4 | Second client sees first client's / imported data | `test_mcp.py::test_two_clients_share_memory` |
| 5 | New findings don't overwrite unrelated data | `test_store.py::test_new_findings_do_not_overwrite_unrelated` |
| 6 | Source and verification status retained | `test_store.py::test_records_retain_source_and_status` |
| 7 | Supersede without losing history; retrieval prefers newer | `test_store.py::test_supersede_keeps_history`, `test_context_prioritizes_newer_record` |
| 8 | Finds a planted contradiction and a missing-evidence case | `test_gaps.py::test_detects_deliberate_contradiction`, `test_detects_missing_evidence` |
| 9 | Superseded decision ≠ unresolved contradiction | `test_gaps.py::test_superseded_vs_unresolved_contradiction` |
| 10 | Runs locally, no paid API | the whole suite (40 tests) runs offline |

Also tested: reads never modify the database (byte-level dump comparison), reads run under `query_only`,
models cannot mark claims `evidence_supported` without evidence, invalid MCP input is rejected without writes,
and the full spec workflow runs over MCP.

## Layout

```
memlayer/schema.sql   tables, FTS5 indexes and triggers
memlayer/store.py     MemoryStore — the only module that writes SQL (swap point for another backend)
memlayer/context.py   get_project_context ranking (pure)
memlayer/gaps.py      gap analyzer (pure)
memlayer/importer.py  deterministic structured-markdown import
memlayer/server.py    MCP server (official Python SDK, FastMCP); stdio locally
memlayer/hosted.py    hosted service: MCP over HTTP with sign-in pages, security headers
memlayer/oauth.py     OAuth 2.1 authorization server provider
memlayer/accounts.py  users, OAuth clients, grants, hashed tokens, beta requests; one memory file per user
memlayer/static/site/ public landing page and sign-in styles (journal design)
memlayer/cli.py       human control: inspect, approve, edit, supersede, delete, export, `web`
memlayer/web.py       dashboard: Starlette JSON API, live change stream (SSE), static app
memlayer/brand.py     the product name and tagline, in one place
memlayer/static/      no-build frontend: app.css, js/ (shell, panel, palette, views/, graph)
samples/              synthetic test data
scripts/demo.py       end-to-end MCP demo
tests/                pytest suite
```

## Renaming

The name lives in `memlayer/brand.py`. Override it without touching code:

```bash
MEMLAYER_BRAND_NAME="Cortex" MEMLAYER_BRAND_TAGLINE="Your research, remembered" memlayer web
```

The dashboard title, sidebar and page `<title>` follow it. The Python package and MCP server name (`memlayer`)
are internal identifiers; rename those separately if you want the CLI and MCP name to change too.

## Privacy and safety

- The sample data is synthetic. Locally the database stays on your machine and the stdio server has no network
  listener. The hosted service is the only network-facing mode and requires sign-in for every MCP call.
- No credentials exist anywhere in the code. There are no outbound calls.
- Stored content is returned with a notice that it is data, not instructions; the server's MCP instructions
  tell models not to follow text inside memory.
- No delete over MCP. `memlayer delete` requires `--yes`; `memlayer export` produces a full JSON backup.

## Not built yet (by design)

Semantic or embedding search, LLM-assisted extraction, automatic duplicate merging (duplicates are only
*flagged* via `possible_duplicates`), automatic capture of conversations, team accounts. The store and analyzer are separated so each can be added
without touching the others.
