# Deployment and operations

NEXUS is one process. It needs a place to keep data (a folder of SQLite files, or Postgres) and an HTTPS
address in front of it. Nothing here has been deployed to a public host as part of this release; the container
build and the Postgres path are tested locally and in CI configuration (see docs/verification.md).

## Choose a shape

| Shape | Data | Good for |
|---|---|---|
| One container + Postgres (`docker compose`) | Postgres 17 | A team. The recommended production shape. |
| One process + a data folder | SQLite files on a persistent volume | A small team or a trial. One instance only. |
| Serverless (Vercel, `asgi.py`) + managed Postgres | Postgres | Works, with the limits listed below. |

## Docker and Postgres

```bash
cp .env.example .env
# edit .env: NEXUS_PUBLIC_URL=https://nexus.example.com
#            NEXUS_SECRET_KEY=<output of: nexus admin generate-key>
#            POSTGRES_PASSWORD=<a real password>
docker compose up -d
```

The service listens on `127.0.0.1:8000`. Put a TLS-terminating proxy (Caddy, nginx, a load balancer) in front.
The image installs dependencies from `requirements.lock`, runs as a non-root user and has a health check on
`/readyz`.

If the proxy is on another host, run uvicorn with `--forwarded-allow-ips` set to the proxy's address so the
audit log and rate limits see real client addresses. `nexus hosted` already enables proxy headers.

## One process and a data folder

```bash
pip install .
export NEXUS_PUBLIC_URL=https://nexus.example.com
export NEXUS_SECRET_KEY=$(nexus admin generate-key)
nexus hosted --data-dir /var/lib/nexus --port 8000
```

Keep exactly one instance: the data is files on its disk.

## Settings

| Variable | Meaning |
|---|---|
| `NEXUS_PUBLIC_URL` | The https address people and AI apps connect to. Required. Plain http is accepted only for localhost. |
| `NEXUS_DATABASE_URL` | Postgres URL. `DATABASE_URL` and `POSTGRES_URL` are accepted too. |
| `NEXUS_DATA_DIR` | Folder for SQLite files when no database URL is set. Default `~/.nexus/hosted`. |
| `NEXUS_SECRET_KEY` | 32 random bytes, base64 or hex. Seals stored provider keys. On Postgres, required to store them. With a data folder and no key set, a key file is created in the folder (`secret.key`, owner-only). |
| `NEXUS_SECRET_KEYS_OLD` | Earlier keys, comma separated, accepted for reading during rotation. |
| `NEXUS_SIGNUP` | `open` (default), `invite` (needs `NEXUS_SIGNUP_CODE` or a workspace invitation) or `closed` (invitations only). |
| `NEXUS_SIGNUP_CODE` | The code new accounts must enter when sign-up is `invite`. |
| `NEXUS_SMTP_URL`, `NEXUS_MAIL_FROM` | Outgoing mail: `smtp://user:password@host:587` or `smtps://…:465`. Optional. |
| `NEXUS_BRIEFING_PIN_SECONDS` | How long a gateway briefing is reused unchanged. Default 900. |
| `NEXUS_MOCK_PROVIDER` | `0` disables the built-in mock model. Consider disabling it in production. |
| `NEXUS_UPSTREAM_OPENAI`, `_ANTHROPIC`, `_GOOGLE` | Override a provider's base URL (a regional endpoint or an internal proxy). |
| `NEXUS_ALLOW_INSECURE_UPSTREAMS` | `1` allows http and private provider addresses. For local testing only. |
| `NEXUS_CONTROL_SCHEMA` | Postgres schema for the control tables. Default `nexus_control`. |
| `NEXUS_BRAND_NAME` | Display name, if you rename the product. |
| `PORT`, `HOST` | Listen address. Default `0.0.0.0:8000`. |

## Without mail

Everything works without a mail server. Invitation links are shown to the admin who created them. Password
reset links go to the outbox:

```bash
nexus admin outbox                    # unsent messages, with their links
nexus admin reset-link a@example.com  # a fresh one-hour link for an account
```

On a local server (`localhost`), the reset page shows the link directly.

## Migrations

The service applies pending control-plane migrations when it starts. To do it as a separate step:

```bash
nexus admin migrate    # applies migrations; gives pre-workspace accounts their workspace
nexus admin status     # applied and pending
```

`/readyz` returns 503 while migrations are pending. Migrations are additive. On Postgres, concurrent instances
are serialised with an advisory lock. Workspace memory stores upgrade themselves when first opened.

## Upgrading from MemLayer 0.1

0.1 kept one memory per user and no workspaces.

1. Back up first.
2. Environment variables are now `NEXUS_*`. The old `MEMLAYER_PUBLIC_URL`, `MEMLAYER_DATABASE_URL`,
   `MEMLAYER_DATA_DIR` and `MEMLAYER_SIGNUP_CODE` are still read.
3. On Postgres, 0.1 kept accounts in the schema `memlayer_accounts`. Set `NEXUS_CONTROL_SCHEMA=memlayer_accounts`
   to upgrade that database in place.
4. Start the service, or run `nexus admin migrate`. Each existing account gets a personal workspace that points
   at the memory it already had. Existing app connections keep working and are attached to that workspace.
5. A local single-user install keeps using `~/.memlayer/memory.db` until `~/.nexus/memory.db` exists. The
   `memlayer` Python package remains as a thin alias, so existing `python -m memlayer.server` commands still run.

This path is covered by a test for the SQLite layout. The Postgres in-place upgrade (step 3) has not been run
against a real 0.1 database.

## Backups and restore

- **Postgres:** dump the whole database (`pg_dump`). It contains the control schema and one schema per workspace.
- **Data folder:** copy the folder while the service is stopped, or use SQLite's online backup per file. Include
  `secret.key` if you did not set `NEXUS_SECRET_KEY`.
- **The secret key:** store it separately from the database. Without it, stored provider keys cannot be read;
  with both, they can.
- **Per workspace:** any admin can download a JSON export from Settings, Workspace.

No automated backup is configured by this repository.

## Rotating the secret key

```bash
NEXUS_SECRET_KEY=<new> NEXUS_SECRET_KEYS_OLD=<old> nexus admin rotate-keys
```

Restart the service with the same two variables, then remove the old key once the command reports nothing left
to re-seal.

## Health and what to watch

- `/healthz`: the process is up. `/readyz`: the database is reachable, migrations are applied, and whether a
  secret key is configured.
- `nexus admin workspaces`: size and plan per workspace.
- Logs: uvicorn's access log. Request and response bodies are never logged by the gateway.
- Tables that grow without bound: `usage_events`, `receipts`, `audit_log`. There is no pruning job yet.

## Plans and limits

Limits are data on the workspace. Change them without a deploy:

```bash
nexus admin set-plan acme team
nexus admin set-plan acme free --limit gateway_requests_month=10000 --limit members=12
```

There is no billing integration.

## Serverless notes (Vercel)

`asgi.py` exposes the app for Vercel's Python runtime with a managed Postgres. It works, with these effects:

- Rate limits and briefing pins are per instance and reset on cold start, so limits are looser than configured.
- Each instance opens its own database connection; use a pooled connection string.
- Long streaming gateway responses are bound by the platform's function duration limit.

For a team that uses the gateway heavily, prefer a long-running container.

## Scaling limits to know about

- Each process uses **one database connection** guarded by a lock. Throughput is bounded by that. Run more
  processes for more throughput, knowing that rate limits and pins are then per process.
- Memory API handlers run database work on the event loop; a slow query delays other requests on that process.
- One Postgres schema per workspace is comfortable into the low tens of thousands of workspaces.

These are the first items on the roadmap.
