# Research findings and what they changed

Collected 2026-11-16 by three independent research passes (competitors, gateways and MCP, academic literature).
Search was rate-limited for part of each pass, so coverage is a sample. Claims are marked:
**[V]** primary source opened, **[S]** seen in a search summary only, **[U]** unverified.
Nothing here was produced by running NEXUS; product measurements live in `docs/verification.md`.

## 1. The market

### Dedicated memory layers sell to developers building one agent, not to teams

| Product | What it stores | Notes |
|---|---|---|
| Mem0 / OpenMemory MCP | Extracted per-user facts | $24M raised Oct 2025 [S]. Audit logs, SSO, on-prem are enterprise-only [U]. |
| Zep / Graphiti | Temporal knowledge graph; changed facts are invalidated, not deleted | Graphiti is Apache-2.0 with an MCP server [S]. Community Edition discontinued Apr 2025 [U]. |
| Letta (MemGPT) | Agent runtime with self-edited memory | A runtime, not a layer other clients share [S]. |
| Supermemory | Vector memory plus connectors | Had a prompt-injecting "Memory Router"; current docs describe SDK middleware instead [S]. |
| Cognee, LangMem, Basic Memory, Pieces | Graph from documents; LangChain library; local Markdown; individual capture | [S] |

Small 2026 projects already describe "typed, provenance-tracked shared team memory with contradiction flags":
MemClaw/Caura (https://caura.ai/blog/shared-governed-memory/), Engram (https://pypi.org/project/engram-team),
Maindex Teams [U], Mycelium. None shows real traction; none tracks evidence-level verification status.

### Vendor benchmarks are not evidence

- Mem0 and Zep publicly disputed each other's LoCoMo numbers; Zep restated its score [V]
  (https://github.com/getzep/zep-papers/issues/5).
- An independent audit found 6.4% of LoCoMo's answer key wrong and an LLM judge accepting 62.8% of deliberately
  wrong answers [V, not peer reviewed] (https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer).
- On MemoryAgentBench, plain BM25 (60.5) beat Mem0 (32.6), Zep (37.5), MemGPT (34.3) and Cognee (28.3) on
  accurate retrieval [V] (https://arxiv.org/abs/2507.05257).

### Native vendor memory

- Claude memory is per project, Team/Enterprise [S]. Claude Code's team layer is a committed `CLAUDE.md`; auto
  memory is machine-local [S]. ChatGPT and Gemini memory is per vendor [U].
- GitHub Copilot Memory stores repository facts with code citations and re-validates them before use [S]
  (https://docs.github.com/en/copilot/concepts/prompting-and-context/copilot-memory). Anything derivable from
  the repository will be absorbed by vendors.
- What vendors structurally will not do: hold a rival model's output as a first-class record, attribute human
  versus model, or give a team one audit trail across tools.

### Pain, in users' words

A 49-post Cursor forum thread (May–June 2026) [S]: a remembered "we use Jest" surviving a migration, stale
`AGENTS.md`, tools drifting apart, agents overwriting each other's Markdown, and people asking for a log of what
context was injected (https://forum.cursor.com/t/how-are-people-handling-context-across-different-ai-coding-tools/159891).
Open requests for shared team memory in Claude Code [S].

## 2. Gateways and MCP

- Provider fan-out, BYOK, fallbacks, caching and logging are free or near-free from Cloudflare, Vercel,
  OpenRouter, LiteLLM and Portkey [S]. A general gateway is a commodity.
- A gateway is a credential honeypot: malicious LiteLLM 1.82.7/1.82.8 were published to PyPI on 2026-03-24 through
  a compromised CI action and stole credentials [V] (https://research.jfrog.com/post/litellm-compromised-teampcp/).
- Prompt mutation breaks provider prompt caches; cache stability is now a recognised design constraint, including
  in the MCP spec [V] (https://modelcontextprotocol.io/specification/2026-07-28/changelog.md).
- Wire protocols that matter: Chat Completions (the long tail of tools), Responses (Codex CLI dropped Chat
  Completions in Feb 2026 [S]), Anthropic Messages (Claude Code via `ANTHROPIC_BASE_URL`).
- MCP spec 2026-07-28 is stateless, deprecates Dynamic Client Registration in favour of Client ID Metadata
  Documents, and the Python SDK 2.x is a breaking rewrite (`FastMCP` → `MCPServer`) [V]
  (https://py.sdk.modelcontextprotocol.io/whats-new/index.md). SDK 1.30.0 still serves 2025-11-25 clients.
- Best practice for stored BYOK keys: per-credential data key, AES-256-GCM, decryption bound to tenant context [S].

## 3. Literature

- **Keep verbatim sources.** Replacing raw text with extracted facts hurts accuracy; facts as additional index
  keys help [V] (LongMemEval, https://arxiv.org/abs/2410.10813).
- **Inject little.** Reliability falls with input length; focused ~300-token prompts beat 113k-token ones [V]
  (Chroma, https://www.trychroma.com/research/context-rot).
- **Contradiction detection is high-precision, low-recall at best.** LLM validators: precision 0.70–0.99,
  recall 0.01–0.57 [V] (https://arxiv.org/abs/2504.00180). Multi-hop conflict resolution tops out at 28% [V]
  (MemoryAgentBench). Keeping conflicts visible instead of collapsing them enables safer abstention [V]
  (StateFuse, https://arxiv.org/abs/2607.05844).
- **Shared memory multiplies poisoning.** Query-only memory injection succeeds >95% [V] (MINJA,
  https://arxiv.org/abs/2503.03704); sleeper poisoning writes fabricated memories up to 99.8% of the time [V]
  (https://arxiv.org/abs/2605.15338); prompt-injection defences do not cover it [V] (https://arxiv.org/abs/2606.04329).
- **Rationale capture dies when the writer pays and someone else benefits** [S] (Grudin 1996; ADR studies:
  about half of repositories with ADRs hold 1–5).
- No benchmark exists for multi-agent shared memory with provenance [V, absence within the sample searched].

## 4. What this changed in the product

| Original direction | Verdict | What NEXUS does instead |
|---|---|---|
| "Model-independent project intelligence for humans, teams and agents" | No buyer; reads like a dozen landing pages | One buyer: the lead of a 5–30 person engineering team running two or more coding agents. One job: agents stop re-litigating and silently violating decisions already made. |
| Persistent cross-model memory | Crowded | Kept as the substrate, but the product is the **ledger**: decisions, assumptions and findings with status, evidence, author (human or model) and history. |
| Contradiction detection | Table stakes, and easy to get muted | Deterministic and labelled by confidence. Precision first: a *preflight check* flags plans that match a superseded or retracted record (high precision by construction) and exact subject/value conflicts; everything else is returned as "related, not judged". |
| Unified AI gateway with routing | Commodity and a honeypot | A thin pass-through for clients that can change a base URL but cannot speak MCP. Three native wire formats, no translation, no marketplace. Its one job: add a stable, labelled context block and return a **context receipt**. Provider keys can be passed through per request and never stored. |
| Context injection | Breaks caches; injects unreviewed text | The briefing is pinned per project for a window so the prompt prefix stays cacheable, capped in size, appended after the client's own system prompt, and can be restricted to human-confirmed records. |
| (not in the brief) | Write discipline is the real problem | Reads that need no model cooperation: session-start briefing through the CLI/hook and the gateway. Writes default to unverified; only a signed-in person can confirm a record; a model edit clears the confirmation. |
| (not in the brief) | Poisoning scales with sharing | Trust tiers are the same feature as provenance: every write records channel and author, AI clients cannot delete or confirm, read-only grants exist, stored text is always delivered as quoted data. |
| Benchmarks | LoCoMo-style scores are discredited | Small, reproducible, deterministic benchmarks with BM25 and full-context baselines, published with their limits. No LLM-judged scores. |

## 5. What we are still likely wrong about

1. Human review is a tax. If confirming records feels like curating a wiki, the ledger rots. The review queue
   has to be minutes per week.
2. The default competitor is free Markdown in Git. NEXUS must import and export it losslessly.
3. Outcome proof (fewer reverted PRs, fewer repeated decisions) needs real teams; none exist yet.
4. MCP is moving under us: the stateless 2026-07 spec and SDK 2.x are a required migration, not an option.
