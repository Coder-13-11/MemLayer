# NEXUS

One record of what a project has decided, for every person and AI tool that works on it.

Teams that run several AI coding tools find that each tool keeps its own notes. One remembers a decision that was
reversed, another reads a stale rules file, a third was never told. NEXUS keeps a single record of decisions,
assumptions, findings and open questions, each with its author, its evidence and its history, and hands the
same copy to every tool. It can check a plan against the record before an agent acts, and it writes a receipt
of exactly what each model was told.

> This is release candidate 0.2.0. It grew out of MemLayer 0.1 (a personal memory); see
> [docs/handoff.md](docs/handoff.md) for what is verified, what is not, and what comes next.

## What is in the box

| | |
|---|---|
| **The record** | Entries with kind, status, author (person or model), evidence, typed links and full version history. Replaced decisions are kept, not overwritten. |
| **Confirmation** | Only a signed-in person can vouch for an entry. Rewording it clears the confirmation. AI tools cannot confirm or delete. |
| **Review** | A rule-based list of what needs a person: conflicts, claims with no evidence, decisions resting on open questions. Every item cites its entries. |
| **Plan check** | Compares intended steps with the record: conflicts with a decision, or repeats something the team replaced. Deterministic, no model. |
| **Briefing and receipts** | A small, labelled text block for models, and a receipt of which entries (and versions) each model was given. |
| **Three ways in** | MCP with OAuth for apps with connectors; a pass-through gateway for tools that only take a base URL; a CLI for hooks and CI. |
| **Teams** | Workspaces, four roles, single-use invitations, API keys, encrypted provider keys, usage limits, an audit log. |

## Run it locally in a minute

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
nexus hosted --public-url http://localhost:8000 --data-dir ./.devdata
```

Open <http://localhost:8000>, create an account, and choose **Load the example project**. The **Playground**
sends a message through the gateway with a built-in mock model, so nothing else is needed to see a briefing and
a receipt.

Single-user, no account: `nexus web` serves a local dashboard on one SQLite file, and `nexus serve` is a
stdio MCP server for it.

## Run it for a team

```bash
cp .env.example .env      # set NEXUS_PUBLIC_URL and NEXUS_SECRET_KEY
docker compose up -d      # the service and Postgres 17
```

Put a TLS-terminating proxy in front of port 8000. Details, settings, backups and upgrades:
[docs/deployment.md](docs/deployment.md).

## Connect a tool

```bash
# an app with connectors: paste this address
https://your-nexus.example.com/mcp

# Claude Code
claude mcp add --transport http nexus https://your-nexus.example.com/mcp

# a tool that only takes a base URL (your provider key passes through untouched)
export ANTHROPIC_BASE_URL=https://your-nexus.example.com
export ANTHROPIC_CUSTOM_HEADERS="x-nexus-key: nxk_...
x-nexus-project: Checkout rebuild"

# a terminal, a session hook or CI
export NEXUS_URL=https://your-nexus.example.com NEXUS_API_KEY=nxk_...
nexus brief "Checkout rebuild"
nexus check "Checkout rebuild" "Add Jest tests for the cart"     # exits 2 on a conflict
```

The served documentation (`/docs` on a running instance) covers each of these.

## Tests and benchmarks

```bash
pytest -q                           # SQLite
NEXUS_TEST_PG_URL=postgresql://user@host/db NEXUS_SECRET_KEY=$(nexus admin generate-key) pytest -q   # and Postgres
python benchmarks/run.py            # plan-check precision and recall, conflict detection, sizes, speed
```

Results and how to read them: [docs/verification.md](docs/verification.md).

## Layout

```
nexus/
  store.py  db.py  pg.py  schema*.sql   one memory store per workspace (SQLite file or Postgres schema)
  gaps.py  context.py  check.py  briefing.py   pure functions: review, context, plan check, briefing
  accounts.py  tenancy.py  keys.py  metering.py  crypto.py   the control plane (accounts, workspaces, keys, usage)
  migrate.py  migrations/               numbered, portable control-plane migrations
  hosted.py  principal.py  pages.py     routing, who-is-asking, server-rendered pages
  web.py  account_api.py                the JSON APIs (memory; account and workspace)
  server.py  oauth.py                   the MCP server and its OAuth provider
  gateway.py                            the model pass-through
  cli.py  remote.py                     the nexus command
  static/                               the app (no build step) and the public site
docs/                                   research, architecture, deployment, security, verification, roadmap, handoff
benchmarks/  tests/  scripts/
```

## Documents

- [docs/research.md](docs/research.md): the market, the literature, and what they changed in the product
- [docs/architecture.md](docs/architecture.md): how it is built and why, with the decisions recorded
- [docs/deployment.md](docs/deployment.md): running, configuring, backing up, upgrading
- [docs/security.md](docs/security.md): threat model, what is defended, known weaknesses
- [docs/verification.md](docs/verification.md): what was tested, how, and the results
- [docs/roadmap.md](docs/roadmap.md): what comes next, in order
- [docs/handoff.md](docs/handoff.md): the state of the release, plainly
- [DESIGN.md](DESIGN.md) and [PRODUCT.md](PRODUCT.md): the visual system and product brief
