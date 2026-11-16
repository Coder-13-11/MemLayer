# Architecture

NEXUS is one Python process that serves five things from one codebase: the public site, the signed-in app,
a REST API, a model gateway, and an MCP endpoint. This document says how it is put together and records the
decisions behind it.

## The shape

```
                         ┌──────────────── one ASGI process (Starlette) ───────────────┐
 browser ── cookie ────► │ /app, /api/*    session  ─┐                                  │
 program ── nxk_ key ──► │ /v1/*           API key  ─┼─► Principal ─► memory API ─┐     │
 AI app ─── OAuth ─────► │ /mcp            grant    ─┘   (who, which workspace,   │     │
 coding tool ─ nxk_ ───► │ /v1/chat/..., /v1/messages    role, read-only?)        │     │
                         │        gateway ─► briefing + receipt ─► provider       │     │
                         └────────────────────────────────────────────────────────┼─────┘
                                                                                  ▼
            control plane (one database)                      one memory store per workspace
            users, sessions, workspaces, memberships,         projects, sources, records, evidence,
            invitations, API keys, sealed provider keys,      relations, record history
            OAuth clients and grants, usage, receipts,        (a SQLite file, or a Postgres schema)
            audit log
```

### Request families

`hosted.py`'s `Dispatcher` routes by path and establishes who is asking before any handler runs. Each family
has exactly one way in:

| Path | Credential | Becomes |
|---|---|---|
| `/app`, `/api/*` | session cookie, plus `X-Nexus` header and matching `Origin` on writes | a person in one of their workspaces |
| `/v1/*` (memory) | `Authorization: Bearer nxk_…` with the `memory` scope | a workspace, as viewer or editor |
| `/v1/chat/completions`, `/v1/responses`, `/v1/messages` | an `nxk_` key with the `gateway` scope | a metered pass-through |
| `/mcp` | OAuth access token bound to this resource | the workspace the person connected the app to |
| everything else | none | public pages, sign-in, OAuth endpoints |

All of them resolve to a `Principal` (`principal.py`): channel, actor, workspace, role, read-only flag. Handlers
reach memory only through `accounts.memory_for(principal.workspace_id)`. There is no code path from a request
to a memory store that does not pass through a membership, a key or a grant.

### Layers

- **Pure functions** (`gaps.py`, `context.py`, `check.py`, `briefing.py`) take a project snapshot (plain dicts)
  and return plain dicts. They touch no database and call no model, so they are deterministic and easy to test.
- **Store** (`store.py`) is the only module that writes SQL for memory. Reads run read-only; every write is one
  transaction and snapshots the previous version of anything it changes.
- **Control plane** (`accounts.py` with `tenancy.py`, `keys.py`, `metering.py` mixed in) owns identity, access,
  secrets and metering.
- **Interfaces** (`web.py`, `account_api.py`, `server.py`, `gateway.py`, `remote.py`) translate between a wire
  format and those layers. `web.py`'s handlers are mounted twice, under `/api` and `/v1`, with hooks that
  supply the store, the permission check and the actor, so the app and the REST API cannot drift apart.

## Decisions

Each records what was chosen, what was rejected and why. They are numbered so later documents can refer to them.

### D1. Keep Python, Starlette and a no-build frontend

*Chosen:* the stack MemLayer 0.1 already used. One process, one language, ES modules served as files, no bundler.
*Rejected:* a separate Node frontend (Next.js) with a Python API.
*Why:* a small team ships and operates one deployable. The official MCP SDK is Starlette-based, so the MCP
endpoint, OAuth server and HTTP API share one app. The frontend is about 3,500 lines of plain modules; a
framework would add a build pipeline before it added capability. *Revisit when* the app needs offline state,
or more than two people work on the frontend at once.

### D2. One memory store per workspace

*Chosen:* each workspace's record lives in its own SQLite file or Postgres schema, opened by workspace id.
*Rejected:* shared tables with a `workspace_id` column and row-level filtering.
*Why:* isolation is physical. A handler cannot forget a `WHERE workspace_id = ?` because there is none to
write. Export and deletion are one file or one schema. The cost is one schema per workspace on Postgres, which
is fine into the low tens of thousands and is the first thing to revisit at larger scale (see the roadmap).

### D3. Numbered migrations in the SQL both databases share

*Chosen:* control-plane migrations are `.sql` files in a portable subset, applied in order and recorded.
*Why:* one set of files, tested on both. Migration 0001 uses `IF NOT EXISTS`, so a database created by 0.1
upgrades in place. Memory stores migrate themselves on open because they are created on demand.

### D4. The record is typed and versioned; nothing is overwritten

Inherited from 0.1 and kept. Entries have a kind, a verification status and a lifecycle. Replacing an entry
links old to new and keeps both. Research supports this over extract-and-overwrite memory: keep verbatim
sources, preserve conflicts as visible objects, and never silently resolve them (docs/research.md §3).

### D5. Confirmation is a separate trust tier that only people can set

*Chosen:* `confirmed_by` / `confirmed_at` on a record, settable only through a signed-in browser session, and
cleared by any change to the entry's wording.
*Why:* shared memory multiplies the damage of a poisoned or mistaken write. Evidence can be attached by a model;
a person's vouching cannot be. This is the same mechanism as provenance, not an add-on, and it lets a briefing
be restricted to entries that are confirmed or evidenced.

### D6. The check is deterministic, narrow and labelled

*Chosen:* subject/value comparison and weighted word overlap, with a rule that a step only "resembles a replaced
record" if it uses a term the old entry had and its replacement dropped.
*Rejected:* calling a language model to judge contradictions.
*Why:* published results put LLM contradiction detection at high precision only with low recall, and a noisy
checker gets muted. A deterministic check is fast, free, reproducible and honest about what it cannot see. The
connected AI tool is the judge: the check hands it the relevant entries. Measured results are in
docs/verification.md.

### D7. The gateway forwards native formats and does not route

*Chosen:* three wire formats, each forwarded to providers that speak it, with stored or passed-through keys.
*Rejected:* a general router with translation, fallbacks and a model catalogue.
*Why:* routing is a commodity offered free by several large vendors, a general gateway is a credential
honeypot, and model catalogues change monthly. The gateway exists for one reason: tools that cannot use MCP
still need the briefing, and reading should not depend on a model deciding to call a tool.

### D8. Briefings are pinned, small, placed last, and receipted

*Chosen:* a briefing is built once per project and reused unchanged for a window (default 15 minutes); it has a
token budget; it is added after the client's own system text; a receipt is written per briefing, not per request.
*Why:* mutating the prompt on every request breaks provider prompt caches, and long context degrades answers.
Pinning trades up to 15 minutes of staleness for cache stability; a client can ask for a rebuild.

### D9. Receipts and usage live in the control plane; conversations are not stored

*Chosen:* the gateway keeps counts, model names and record ids. Request and response text is never written.
*Why:* the audit question is "what was the model told about the project", which the receipt answers without
holding anyone's prompts.

### D10. Provider keys are envelope-encrypted, with a pass-through alternative

*Chosen:* a per-credential data key (AES-256-GCM) sealed by a master key from the environment, both bound to
the workspace and row; or pass-through, where the client's provider key is forwarded and never stored.
*Why:* the best defence for a stored secret is not storing it. Where storing is wanted, the seal is the one
place to swap in a cloud key service.

### D11. Stay on MCP SDK 1.x for this release

*Chosen:* the 1.x line that the existing OAuth flow is tested against.
*Why:* the 2026-07 specification revision and SDK 2.x are a breaking rewrite. Migrating is required work, but
it is a contained task better done with real clients to test against than folded into this release. It is
first on the roadmap.

### D12. Synchronous storage code, run off the event loop where it matters

The stores are synchronous and guarded by a lock. Account and workspace handlers and all gateway preparation run
in a thread pool. The memory API handlers call the store directly from `async` functions, which blocks the
event loop for the duration of a query (milliseconds on SQLite). On Postgres each process shares one
connection. This is the main scaling limit and is addressed in the roadmap.

## Data model

Control plane (`nexus/migrations/`): `users`, `web_sessions`, `workspaces`, `memberships`, `invitations`,
`api_keys`, `provider_credentials`, `oauth_clients`, `auth_requests`, `auth_codes`, `grants`, `tokens`,
`usage_events`, `receipts`, `audit_log`, `email_tokens`, `outbox`, `beta_requests`, `schema_migrations`.

Memory store (`nexus/schema.sql`, `schema_pg.sql`): `projects`, `sources`, `records`, `evidence`, `relations`,
`record_history`, with full-text indexes.

## Frontend

`nexus/static/js/` is ES modules with a tiny DOM helper (`dom.js`) that inserts stored text as text, never as
HTML. `main.js` is the shell: hash routing, sidebar, live refresh by polling a revision fingerprint. Views are
one file each under `views/`. The public site (`static/site/`) is HTML fragments in a shared layout with one
small script; it works without JavaScript.

The visual system is documented in [DESIGN.md](../DESIGN.md).
