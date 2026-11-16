# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Python and plain JavaScript. Starlette serves the JSON APIs, the MCP endpoint, the model gateway, the public
site and a no-build ES-module app from one process. Storage is SQLite files or Postgres. No Node, no bundler.

## Users

The lead of a 5 to 30 person engineering team that runs two or more AI coding tools (Claude Code, Cursor, Codex,
ChatGPT), and the engineers on that team. They are at a desk, in daylight, between code review and standup.
They are tired of agents re-litigating settled decisions and of each tool believing something different.
A single person can also run it locally for themselves.

## Product Purpose

One record of what a project has decided, read by every person and AI tool that works on it. Success: an agent
about to repeat a mistake the team already fixed is told so before it acts, and when an agent does something
surprising, the lead can see exactly what it was told.

## Positioning

"Shared AI memory" is crowded. NEXUS is the ledger, not the memory: decisions, assumptions and findings with
their author (person or model), evidence, confirmation by a person, and history. Three things no general memory
layer or vendor memory offers together: a trust tier only people can set, a deterministic check of plans against
replaced decisions, and a receipt of what each model was handed. See docs/research.md.

## Operating Context

- Hosted mode (`nexus hosted`): accounts, workspaces with four roles, invitations, API keys, encrypted provider
  keys, usage limits, audit log. Ways in: MCP with OAuth, a REST API, a pass-through gateway, a CLI.
- Local mode (`nexus web`, `nexus serve`): one SQLite file, no accounts.
- Core objects: workspaces, projects, sources (verbatim text), records (objective, finding, decision,
  assumption, hypothesis, evidence, question, task, note), evidence, typed relations, version history,
  receipts.

## Capabilities and Constraints

- Statuses: unverified, evidence_supported, challenged, contradicted, superseded, resolved, retracted. Separately,
  a record may be confirmed by a person; rewording clears it.
- The review and the plan check are deterministic. The product calls no AI model itself.
- AI tools can read, add and correct. They cannot delete or confirm. Everything they write starts unverified.
- Stored content is untrusted data: never rendered as HTML, never followed as instructions, flattened before it
  is placed in a briefing.
- The gateway forwards native wire formats and does not translate, route or store conversations.
- The name lives in `nexus/brand.py` and can be overridden with `NEXUS_BRAND_NAME`.

## Brand Commitments

Name: NEXUS. Visual world: the carbon copy. Multipart carbonless forms and till receipts: a bright white
original, canary, pink, sky and mint copy sheets, carbon-blue ink, red serial numbers and stamps. The app keeps
the same ink and paper at working density. Documented in DESIGN.md.

## Evidence on Hand

A synthetic example project, "Checkout rebuild (example)" (`nexus/demo.py`), used by the app's first run, the
landing page, the tests and the benchmarks. No real users, customers, testimonials or outcome metrics exist; do
not fabricate any. Benchmarks in `benchmarks/` are reproducible and say what they do not show.

## Product Principles

1. Provenance before polish: a claim is never shown without who wrote it and how far it was checked.
2. History is kept, not erased: corrections supersede; deletion is deliberate and human.
3. Honest about uncertainty: matches found by wording are labelled as such, and "clear" is never approval.
4. People vouch, models propose: only a person can confirm, and conflicts stay visible until a person settles them.
5. Model-neutral: no AI provider is privileged in the data or the interface.
6. Say what it does not do: limits are published next to features.
