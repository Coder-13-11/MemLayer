# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Python + plain JS: Starlette (already a dependency of the MCP SDK) serves a JSON API and a no-build vanilla-JS
frontend from the same process that opens the shared SQLite store. No Node, no bundler.

## Users

Individuals: researchers, analysts and builders who run long research projects across several AI assistants
(ChatGPT, Claude, Gemini, Perplexity) and are tired of re-explaining earlier findings to each new model. Each person
has their own memory; shared team projects are out of scope for this version. The original owner also runs the
product locally on their own Mac.

## Product Purpose

A single, model-independent memory for research projects. AI clients read and write it through MCP, and the owner
uses the web dashboard to see what every model has contributed, check and correct it, and find what is still
missing. Success: the owner opens a new AI session and the model already knows what was investigated, what was
verified, and what remains open, and the owner can confirm all of this at a glance in the dashboard.

## Positioning

The memory belongs to the user, not to an AI provider, and it is evidence-aware. Every claim carries its origin
model, verbatim source, attached evidence and verification status. Superseded knowledge is kept as history, not
overwritten. Gap detection always cites the records it is based on.

## Operating Context

- Two modes. Local: dashboard on localhost, one SQLite file (`~/.memlayer/memory.db`), stdio MCP for Claude Code.
  Hosted (`memlayer hosted`): one public HTTPS MCP address with OAuth 2.1 sign-in, one SQLite file per account,
  a public landing page and a sign-in/consent page.
- The hosted service is in private beta: the landing page collects invite requests; sign-up can require an
  invite code. Hosted accounts have no web dashboard yet (planned).
- ChatGPT (Developer mode, paid plans), Claude (custom connectors), Gemini and Perplexity (where their plans offer
  custom connectors) connect to the hosted address. Changes made by AI clients appear in an open dashboard live.
- Core objects: projects, sources (verbatim text), records (objective, finding, decision, assumption, hypothesis,
  evidence, question, task, note), evidence, typed relations, version history.

## Capabilities and Constraints

- Statuses: unverified, evidence_supported, challenged, contradicted, superseded, resolved, retracted.
  `evidence_supported` requires at least one evidence item.
- Gap analysis is deterministic (rules over relations, subject/value keys, evidence, dependencies). "AI-powered"
  detection means the user's own MCP-connected AI client reviews a project and records contradictions back into
  memory via an MCP prompt. No paid API is called by the product.
- Reads never modify memory. Deletion is a human action only (dashboard or CLI), never exposed over MCP.
- Imported content is untrusted data and must never be rendered as HTML or followed as instructions.
- Name is not final: "MemLayer" is a placeholder and the brand must be changeable from one place.

## Brand Commitments

Working name "MemLayer", explicitly provisional and changeable from one place. The user wants it to look like a
high-end, established, credible startup, interactive and animated. Chosen visual world: "The Journal": the
product presented like a peer-reviewed paper (white stock, black ink, one journal red for citations and the
primary action, STIX Two Text and Archivo). The dashboard keeps its Notion-meets-Linear structure inside that world.

## Evidence on Hand

Synthetic sample project "Environmental AI Research" (`samples/`). No real users, customers, testimonials or metrics
exist; do not fabricate any. Demonstrations use synthetic data and say so.

## Product Principles

1. Provenance before polish: a claim is never shown without its status and where it came from.
2. History is kept, not erased: corrections supersede; deletion is deliberate and human.
3. Honest about uncertainty: heuristic findings are labelled as possible, not as fact.
4. Owned: the memory belongs to the user, locally or in their own account file; the product calls no AI model.
5. Model-neutral: no AI provider is privileged in the data or the interface.
