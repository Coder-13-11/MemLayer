# Roadmap

Ordered by what most limits a real team adopting NEXUS today. Each item says why it is where it is.

## Now: before inviting outside teams

1. **Move to the 2026-07 MCP specification and SDK 2.x.** The server is on the 1.x SDK. The new revision is
   stateless, replaces dynamic client registration with client-ID metadata documents, and the SDK is a breaking
   rewrite. Clients will move; this is required, not optional. Keep dynamic registration as a fallback and test
   against each real client (ChatGPT, Claude, Cursor, VS Code, Claude Code, Codex).
2. **Test against real clients and real providers.** Everything is verified with the official MCP client library
   and stand-in provider servers. No test has used a live ChatGPT/Claude/Cursor connection or a live provider
   key. This is the largest gap between "tests pass" and "works for a customer".
3. **A connection pool and off-loop database work.** Each process shares one database connection behind a lock,
   and the memory API runs queries on the event loop. Move to a pool with per-request connections and run all
   store calls in threads. This is the first thing that breaks under load.
4. **Shared rate limits.** Limits and briefing pins are per process. Put counters in the database (or Redis) so
   several instances enforce one limit.
5. **An outside security review.** The internal assessment is in docs/security.md. A shared record read by many
   AI tools, with a gateway attached, deserves someone paid to break it.

## Next: make the record fill itself and stay trusted

6. **Capture that needs no model cooperation.** The research finding that matters most: agents do not reliably
   save decisions. Add a session-end hook that proposes entries from the session for a person to accept, and a
   pull-request integration that asks "this PR changes X; is there a decision to record?".
7. **Check plans at pull-request time.** A GitHub check that runs `nexus check` on a PR's description and
   commit messages, and comments with the replaced decision. This is where the wedge customer already works.
8. **A weekly review that takes minutes.** Review is the tax that kills wikis. Add keyboard triage (confirm,
   replace, dismiss), batching by topic, and a digest of what agents wrote this week.
9. **Import and export as Markdown in Git.** The default competitor is free Markdown in a repository. Round-trip
   decisions to `docs/decisions/*.md` so adopting NEXUS never means leaving Git.
10. **Lineage quarantine.** When an entry is retracted, mark everything that depends on it and every receipt
    that included it, so a team can see what a bad entry touched.

## Later: commercial and enterprise

11. **Billing.** Plans and limits exist as data; nothing charges. Add Stripe, seat-based pricing and a usage page
    that matches the invoice.
12. **Single sign-on, two-factor sign-in, SCIM.**
13. **A cloud key service for the master key**, and customer-managed keys. `crypto.py` has one seam for it.
14. **Semantic search as an addition to keyword search**, never a replacement: benchmarks show keyword search
    is a strong baseline, and extracted facts should index verbatim text, not replace it.
15. **An optional model-assisted review**, run with the workspace's own provider key, that proposes conflicts
    for a person to accept. Labelled, opt-in, never automatic.
16. **Pruning and archiving** for usage events, receipts and the audit log.
17. **A public benchmark for shared memory with provenance.** None exists. Publishing one, with baselines, is a
    credible way to be known for honesty in a field whose numbers are disputed.

## What would change this order

- If the first teams use the gateway more than MCP, items 3 and 4 move ahead of 1.
- If nobody confirms entries, item 8 moves to the top: the trust tier is only worth something if it is used.
- If teams ask "why not a Markdown file", item 9 moves to the top.
