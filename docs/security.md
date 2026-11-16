# Security assessment

This is an internal assessment of release candidate 0.2.0. It combines the design's own threat model with the
findings of two independent reviews run against the build: an adversarial security review that attacked a
running instance, and a senior-engineer code review. It is not a substitute for an outside audit, which has
not been done.

## What is being protected

1. **A workspace's record** from other workspaces, and from members acting beyond their role.
2. **Provider API keys** that a workspace stores.
3. **The integrity of the record**: that what a model is told is what the team recorded, attributed to whoever
   really wrote it.
4. **Accounts and sessions.**

## Who might attack

- A member of another workspace, or anyone with an account, trying to read or change a workspace they are not in.
- A viewer, editor or API key trying to act as an admin.
- A website the victim visits (cross-site requests, framing, a malicious OAuth client).
- **A poisoned entry**: text written into the record by a compromised AI tool, a careless teammate or an imported
  document, aimed at the models that will read it. Research shows this is the attack a shared memory multiplies.
- Someone with a copy of the database.
- Someone trying to make the gateway call an internal address.

## What the reviews tried and could not break

Confirmed by attack against a running instance (SQLite), unless marked otherwise:

- **Tenant isolation.** A member or key of workspace B received 404 for A's records, projects, sources,
  receipts, keys and invitations, and could not delete or link to them. Naming A's workspace in the header
  returned 403. The gateway refused A's project.
- **Roles.** Viewer writes, key creation, invitations, role changes, audit, export and confirmation returned
  403. An admin could not become owner, demote or remove the owner, or mint an admin key. A downgrade applied
  on the next request.
- **Keys.** A viewer key cannot write; an editor key cannot confirm; an API key is rejected on `/api` and `/mcp`;
  administration does not exist under `/v1`.
- **Cross-site requests.** Posts to `/login` and `/consent` with a foreign or null origin returned 403. API
  writes without the custom header, or from a foreign origin, returned 403.
- **Redirects.** `next=//evil…` and absolute URLs fell back to `/app`.
- **OAuth.** Mismatched redirect, missing PKCE, wrong verifier, code reuse and refresh reuse were all refused.
- **Gateway.** A stored key never reached a different base URL; redirects are not followed; private, loopback,
  metadata, decimal and hex address forms were blocked; no secret appeared in any API, the audit log or an export.
- **Injection into pages.** No HTML sinks in the app's JavaScript; server-rendered pages escape every value; the
  content security policy allows scripts from the site's own origin only.
- **Path traversal and SQL.** Traversal attempts returned 404; SQL is parameterised and identifiers validated
  (read, not attacked).
- **Crypto.** AES-256-GCM, a fresh data key and nonce per secret, bound to workspace and row.

Postgres schema isolation was reviewed by reading and is covered by tests, but was not attacked.

## Findings and what was done

No critical or high-severity finding was reported. Every item below came from the reviews.

### Fixed in this release, with a regression test

| Finding | Fix |
|---|---|
| An invited account was marked as a confirmed address, although the inviting admin can read the link. | Joining by invitation no longer confirms the address. |
| Ten failed sign-ins for an address locked that address out from anywhere. | Failures are counted per network and per (network, address); a stranger elsewhere cannot lock someone out. |
| Stored text could forge a record line, a record id or the "confirmed" tag inside a briefing; a project name could inject attributes into the block's opening tag. | The id and trust tag are now written before the text; square brackets, angle brackets and double quotes in stored text are neutralised; explanations are marked `note:`. |
| The "confirmed only" briefing admitted entries with self-attached evidence, and let an unconfirmed entry displace a confirmed one. | That mode now requires a person's confirmation; a replaced entry is listed only if its replacement is confirmed; conflicts only if they involve a confirmed entry. |
| Request bodies were unbounded and the gateway read them before authenticating. | Bodies are refused by declared length before reading (2 MB; 12 MB for model requests) and capped when sent without a length. Deeply nested JSON no longer crashes the parser. |
| Anyone could register unlimited OAuth clients; the consent page showed `claude.ai@evil.example` as the return address. | Registration is limited per network. The consent page shows the real host. Redirect addresses with embedded credentials or script schemes are refused. |
| A read-only key could spend the workspace's stored provider key. | Stored provider keys need a read-and-write key. |
| A NEXUS key placed in a provider header was forwarded upstream. | Any forwarded header containing a NEXUS key is dropped. |
| The address guard missed NAT64 addresses. | Blocked. |
| A program that named no model was recorded as "human". | It is recorded as itself; "human" is reserved for a signed-in person. |
| Requesting a new reset link cancelled the previous one; sent mail kept its links in the database. | Older links stay valid until used or expired; a delivered message's body is redacted. |
| Authorization codes, refresh tokens, reset links and invitations could each be used twice when two server instances raced; two owners could demote each other and leave no owner. | Each is now decided by a single SQL statement, and owner changes lock the workspace row. A two-connection race test covers them. |
| Malformed input (wrong types, non-numeric parameters, NUL bytes) returned HTTP 500. | Validated; wrong types return 400. A 24-case regression test covers them. |
| The export link could download a different workspace than the one on screen. | The export is fetched with the workspace named. |
| A cut-off streamed response could go unmetered; a compressed stream would have been relayed corrupted. | Cleanup and metering are shielded from cancellation; upstream is asked not to compress. |
| The memory API ran database work on the server's event loop. | Those handlers now run in worker threads. |
| Opening a MemLayer 0.1 Postgres database started empty without a setting. | The earlier schema is detected and upgraded in place. |
| Compose shipped a default database password; `.env.example` was ignored by git. | The password is required; the example file is tracked. |

### Known and not fixed

These are real. They are listed in the order they should be addressed.

1. **One database connection per process, behind a lock.** Sign-in, consent and invitation pages and the MCP
   tools still do database work on the event loop. A slow query delays other requests on that process. A
   connection pool is the first roadmap item after MCP.
2. **Postgres cost per request.** Each statement outside a transaction is four round trips; the app's change
   poll sends about twenty statements. Fine locally, slow against a remote database.
3. **Rate limits, quotas and briefing pins are per process and in memory.** They reset on restart, are looser
   with several instances, and the monthly quota can be overshot by concurrent requests. Fifty keys give fifty
   times the per-minute limit.
4. **The address guard checks, then connects.** A DNS answer could change in between. Impact is limited: https
   with certificate verification is enforced and redirects are not followed. The fix is to connect to the
   address that was checked.
5. **An invitation still creates an account for an address the admin chose**, and bypasses closed sign-up by
   design. The account is no longer marked confirmed, but the address is taken until its owner resets the
   password, at which point they land in the inviter's workspace.
6. **Refresh-token reuse does not revoke the whole token family.**
7. **A signed-in editor can still supersede a confirmed entry.** History keeps it and the confirmed-only briefing
   ignores the replacement, but the default briefing shows the new entry (labelled unverified).
8. **Without mail configured, reset and invitation links sit in the outbox** until read. With a data folder and
   no `NEXUS_SECRET_KEY`, the generated key file sits beside the database, so one backup holds both.
9. **No retention** for usage events, receipts, the outbox or the audit log.
10. **The content security policy has no `form-action`**, because OAuth redirects after the consent form go to
    other sites and to app schemes. Cross-site form posts are refused by origin checks instead.
11. **CI actions are pinned to version tags, not commit hashes.** For a project that holds provider keys, pin to
    hashes before making the repository public.
12. **The mock model is on by default.** It is harmless but should be turned off in production.

### Not assessed

- The Vercel deployment path and the container image.
- Real browsers other than the built-in one; no screen-reader pass.
- Behaviour of real AI clients given a hostile record. The briefing's defences are structural; whether a given
  model ignores an instruction embedded in a title is that model's behaviour, and was not measured.
- Denial of service beyond body size.

## Operating it safely

- Set `NEXUS_SECRET_KEY` explicitly and store it apart from database backups.
- Terminate TLS in front of the service and set `--forwarded-allow-ips` to your proxy, or every visitor appears
  as one address and shares one sign-in limit.
- Prefer pass-through mode for provider keys where your tools allow it.
- Use `NEXUS_SIGNUP=invite` or `closed` unless you mean to run an open service.
- For teams worried about one tool misleading another, set the briefing to confirmed entries only.
