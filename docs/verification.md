# Verification

What was tested, how, and what came back. Three labels are used throughout:

- **Verified**: run against the real code in this repository, with the result stated.
- **Verified against a stand-in**: run end to end, but the other party (an AI provider, an AI client) was a
  local stand-in that speaks the real protocol, not the real service.
- **Not verified**: not run. Listed so nobody assumes otherwise.

## Automated tests

| Run | Result |
|---|---|
| `pytest -q` on SQLite | 101 passed, 45 skipped (the skipped tests are the Postgres variants) |
| `pytest -q` with `NEXUS_TEST_PG_URL` set, Postgres 17.11 | 145 passed, 1 skipped |
| `python benchmarks/run.py --check` | reproduces |
| `python -m build`-style wheel (`pip wheel .`) | contains migrations, schemas, every static file, the `memlayer` alias |

The suite starts the real service on a local port (uvicorn) and drives it over HTTP; MCP tests use the official
MCP client library, including its OAuth client.

### What the tests cover (Verified)

- **Isolation between workspaces.** A key or member of one workspace gets 404 for another's projects, records,
  sources, graph, gaps, context, briefing, export and receipts, by id and by name; cannot write to, link to or
  supersede another workspace's records; cannot use the gateway with another workspace's project; and naming
  another workspace in the header is refused. Checked over MCP, the REST API and the app API, on SQLite and
  Postgres.
- **Roles.** Viewer, editor, admin and owner each tried against every administrative action. The last owner
  cannot leave or be demoted. API keys cannot administer. Read-only app connections cannot write.
- **Role changes apply at once.** A connected app's existing token stops writing when its owner is demoted and
  stops working when they are removed.
- **Confirmation.** Only a signed-in person can confirm; a key is refused; a key or model rewording a confirmed
  entry clears it; history keeps both versions.
- **Invitations.** Single use, bound to one address (a different signed-in account is refused), replaced or
  withdrawn links stop working, the member limit counts pending invitations.
- **Sign-in.** Rate limiting, cross-site form posts refused, sign-in only ever redirects into the site, password
  change and reset sign out other sessions, reset links are single use, the reset page does not reveal whether
  an address exists.
- **Secrets.** Passwords, session ids, API keys, tokens, invitation and reset links and provider keys do not
  appear in a dump of the control database. Provider keys do not appear in any API response, the export or the
  audit log. A sealed key does not decrypt in another workspace's context or after tampering. Key rotation works
  and a lost key fails closed.
- **OAuth.** Strict issuer discovery, a public PKCE client without a secret, code and refresh-token single use,
  revocation, choosing a workspace and read-only access at consent.
- **The gateway** (against stand-ins, see below): where the briefing is placed for each wire format, that the
  client's own system text and cache markers are untouched, pinning, refresh, confirmed-only and off modes,
  streaming relayed unchanged, usage read from a stream event split across network chunks, stored and
  pass-through keys, that the NEXUS key never goes upstream, provider errors and rate-limit headers passed
  through, monthly quota and per-minute limits, refusal of private provider addresses.
- **The briefing.** Deterministic output, a hard size limit, ordering, the confirmed-only policy, and that stored
  text cannot close the block or imitate its headings.
- **The plan check** and its input validation; the CLI's `brief` and `check` (exit codes included).
- **Limits, export, account and workspace deletion** (the workspace's file or schema is removed).
- **Upgrade from 0.1** on the SQLite layout: an account with a memory file and an app approval but no workspace
  keeps both.

## Benchmarks (Verified, reproducible)

`python benchmarks/run.py`. Seeded; no model and no network. Raw output: `benchmarks/results.json`.

### Plan check, on 32 labelled steps

The steps were written and labelled before the check was run on them (`benchmarks/plan_check_cases.json`).
Sixteen should block (twelve repeat a replaced decision, four state a conflicting value); sixteen should not.

| | Correctly flagged | False alarms | Missed | Precision | Recall |
|---|---|---|---|---|---|
| Flag nothing (baseline) | 0 | 0 | 16 | n/a | 0.00 |
| Flag any step sharing a word with a replaced record (baseline) | 12 | 10 | 4 | 0.55 | 0.75 |
| NEXUS check, first version | 13 | 4 | 3 | 0.77 | 0.81 |
| **NEXUS check, as shipped** | **13** | **2** | **3** | **0.87** | **0.81** |

What it still gets wrong, and why this is inherent to matching words:

- *Missed (3):* paraphrases that share no words with the record ("Go back to the old test runner we used at
  kickoff").
- *False alarms (2):* steps that name a replaced thing in order to remove or explain it ("Delete the direct
  Stripe SDK import…", "Document why we moved from Jest to Vitest…").

The improvement between the two NEXUS rows came from one rule, found by reading the first run's false alarms: a
step resembles a replaced record only if it uses a term the old record had and its replacement dropped.

**Read this as:** on a small, hand-written set for one invented project, the check catches about four in five
repeats and is right about seven times in eight when it speaks. It is not a measurement on real projects.

### Conflicts the review finds, by how they were planted

| Planted as | Found |
|---|---|
| An explicit "contradicts" link (10) | 10 of 10 |
| The same claim key with different values (10) | 10 of 10 |
| Disagreement only in the wording (10) | **0 of 10** |
| Reported conflicts that were not planted | 0 |

The third row is the product's stated limit, measured: with no model, a conflict nobody linked or keyed is
invisible. A connected AI tool asked to review the project can record such conflicts; that path is not benchmarked.

### Size of what a model is handed (example project, 18 entries)

| | Tokens (characters ÷ 4) |
|---|---|
| Full export | 7,360 |
| Structured context (what `get_project_context` returns) | 3,153 |
| Briefing, standard size | 1,029 (all 18 entries) |
| Briefing, small | 558 (10 entries; says 12 items were left out) |
| Briefing, confirmed entries only | 539 (7 entries) |

### Keyword search, 2,000 seeded records, 50 queries

| Query | Target is first | Target in top five |
|---|---|---|
| An exact reference string | 1.00 | 1.00 |
| Descriptive words (ambiguous by construction) | 0.82 | 1.00 |
| Most recent five, no ranking (baseline) | n/a | 0.00 |

### Speed (one machine, one run; orders of magnitude only)

| Operation | 200 records | 2,000 records |
|---|---|---|
| Load a project snapshot | 1.7 ms | 17 ms |
| Build a briefing | 1.7 ms | 18 ms |
| Review | 0.4 ms | 4.5 ms |
| Plan check, 3 steps | 2.9 ms | 31 ms |
| Search | 0.7 ms | 1.5 ms |

## The running product, by hand in a browser (Verified)

On a local instance (SQLite), in the app's built-in browser, at 1440×900 and 375×812:

- Sign up, land on the welcome screen, load the example project, open each project tab.
- Home, project overview, briefing with size and policy controls, plan check, receipts (including a receipt
  written by the playground), playground with the mock model in Chat Completions format, settings (connect,
  members), a second workspace created and switched to.
- Public site: landing page (typing transfer, plan-check stamp, receipt toggle, copy buttons), documentation,
  pricing, password-reset page.
- Phone width: landing page has no horizontal overflow; the app's navigation opens as a drawer.

Screenshots from these sessions are in `docs/screenshots/`.

## Verified against a stand-in only

- **AI providers.** The gateway's tests run against local servers that speak the Chat Completions, Responses and
  Anthropic Messages formats, including streaming. No request was sent to OpenAI, Anthropic or Google.
- **AI clients.** MCP and OAuth are exercised with the official MCP Python client. ChatGPT, Claude, Cursor,
  VS Code, Claude Code and Codex were not connected.

## Not verified

- Any deployment to a public host. The Dockerfile was not built in this session (the Docker daemon was not
  running); the CI workflow that builds it has not run on a CI service.
- Outgoing mail over SMTP. The outbox and the no-mail paths are tested; delivery is not.
- In-place upgrade of a real MemLayer 0.1 Postgres database.
- The app in Firefox or Safari, with a screen reader, or under sustained load.
- Dark theme beyond a visual check of tokens.
- Behaviour with several instances behind a load balancer.

## Independent reviews

Two independent reviews were run against this build: an adversarial security review and a senior-engineer code
review. Their findings and what was done about each are in [docs/security.md](security.md) and
[docs/handoff.md](handoff.md).
