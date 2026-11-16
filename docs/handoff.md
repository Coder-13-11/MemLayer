# Handoff: NEXUS 0.2.0 release candidate

Written 2026-11-16. Branch `nexus`, not pushed, not deployed. `main` and the existing Vercel deployment are
untouched.

## In one paragraph

MemLayer (a personal AI memory) has become NEXUS: a shared record of what a project has decided, for teams that
run several AI coding tools. It is a working, tested, multi-tenant product with a public site, a signed-in app,
MCP with OAuth, a REST API, a model gateway and a CLI. It has been verified by an automated suite on SQLite and
Postgres, by hand in a browser, by reproducible benchmarks, and by two independent reviews whose findings were
fixed or listed. It has **not** been used by a real team, connected to a real AI client, sent a request to a
real provider, or deployed.

## What changed from the brief, and why

The brief asked for a model-independent intelligence layer with a unified AI gateway. Research
(docs/research.md) said two things plainly: "shared AI memory" is crowded and reads like a dozen other
products, and a general gateway is a commodity and a liability. So:

- The product is aimed at one buyer (the lead of a 5 to 30 person engineering team with two or more coding
  agents) and one job (agents stop re-litigating and silently breaking settled decisions).
- The differentiators are things no memory layer or vendor memory offers together: a trust tier only people can
  set, a deterministic check of plans against replaced decisions, and a receipt of what each model was told.
- The gateway was built narrow: it forwards three native formats, adds a pinned briefing and writes a receipt.
  It does not route, translate or resell.
- Two things were added that were not in the brief: reads that need no model cooperation (session-start
  briefing, the gateway), and a published list of what the product does not do.

## What exists

| Area | State |
|---|---|
| Record, review, history | Working; inherited from 0.1 and extended with confirmation |
| Workspaces, roles, invitations | Working, tested |
| Sign-up, sign-in, reset, account and workspace deletion, export | Working, tested |
| MCP server with OAuth, per-workspace and read-only connections | Working; tested with the official client library |
| REST API and API keys | Working, tested |
| Gateway (Chat Completions, Responses, Messages), stored and pass-through keys | Working; tested against stand-in providers |
| Briefings and receipts | Working, tested |
| Plan check (app, MCP, API, CLI) | Working, benchmarked |
| Usage, limits, audit log | Working, tested; no billing |
| Public site, documentation, sign-in pages | Built and checked in a browser |
| App: home, onboarding, project views, receipts, playground, settings | Built and checked in a browser |
| Migrations, Postgres, container, compose, CI workflow | Migrations and Postgres tested; container and CI not run |

## Numbers

- Tests: 101 pass on SQLite; 145 pass on Postgres 17.
- Plan check on 32 labelled steps: precision 0.87, recall 0.81 (naive baseline: 0.55 / 0.75).
- Conflicts found: all that were linked or keyed, none that exist only in wording (by design, and stated).
- Standard briefing for the 18-entry example: about 1,000 tokens, against 7,400 for the full export.

Detail and caveats: docs/verification.md.

## The refinement cycles that happened

1. **Plan check.** First measured at precision 0.77. Reading the false alarms led to one rule (a step must use a
   term the replaced entry had and its replacement dropped): 0.87 at the same recall, and 3.6 times faster.
2. **Dashboard.** After feedback that the dashboard should be simpler and smoother: home reduced to one next
   step and a project list, a three-step setup strip, the project overview cut to four blocks, seven tabs
   reduced to four plus a menu, loading skeletons and motion.
3. **Independent reviews.** A security review (no critical or high findings) and a code review (two blockers).
   Eighteen findings fixed with regression tests; twelve listed as known. See docs/security.md.
4. **Postgres.** An end-to-end test on Postgres found a real bug (decimal sums broke the usage page), fixed.

## What is not done, in order of risk

1. **Never run with a real AI client or a real provider.** Highest risk. The protocols are implemented and tested
   against the reference client and stand-ins, but ChatGPT, Claude, Cursor and live provider APIs each have
   quirks that only show in use.
2. **MCP is on the 1.x SDK.** The 2026-07 specification and SDK 2.x are a required migration.
3. **One database connection per process; some pages and the MCP tools still block the event loop.** Fine for a
   team, not for load.
4. **Rate limits are per process.** Looser than configured on serverless or with several instances.
5. **No outside security audit, no SSO, no two-factor sign-in, no billing.**
6. **The container was not built here** (no Docker daemon) and CI has not run on a CI service.
7. **No real users.** There is no evidence yet that teams will keep the record current, which is the question
   the whole product rests on.

## Decisions that are yours

- **The name and the look.** NEXUS and the carbon-copy design are choices made on your behalf. Both are easy to
  change: the name in `nexus/brand.py`, the look in two stylesheets.
- **Whether to deploy this over the existing MemLayer deployment.** It upgrades in place (accounts keep their
  memory and connections), but that path was tested on SQLite and a constructed Postgres layout, not on your
  live database. Back up first; consider a separate deployment for a first trial.
- **Open, invite-only or closed sign-up** for a public instance.
- **A print typeface.** Receipts and form entries use the system monospace. A self-hosted face would lift them;
  it was not downloaded without asking.
- **Pricing.** The pricing page says free, with a planned team tier and no price.

## How to pick this up

```bash
git checkout nexus
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest -q
nexus hosted --public-url http://localhost:8000 --data-dir ./.devdata   # then open http://localhost:8000
```

Read, in this order: README.md, docs/research.md, docs/architecture.md, docs/security.md, docs/roadmap.md.

The single most useful next step is not code: connect one real AI client to a running instance, use it on a
real project for a week, and see whether the record stays true.
