"""Vercel entry point: the whole hosted service as one ASGI app. Vercel finds this file by name and sends it
every request with its original path, so no rewrites are needed.

Environment (set in the Vercel project):
  DATABASE_URL or POSTGRES_URL   Postgres connection string (the pooled one). Neon from the Vercel Marketplace
                                 sets one of these automatically.
  NEXUS_PUBLIC_URL               optional; defaults to https://<production domain>
  NEXUS_SECRET_KEY               required to store provider keys (nexus admin generate-key)
  NEXUS_SIGNUP, NEXUS_SIGNUP_CODE  optional: open | invite | closed, and the invite code
  NEXUS_CONTROL_SCHEMA           set to memlayer_accounts when upgrading a database created by MemLayer 0.1

On a serverless host each instance keeps its own rate-limit counters and briefing pins; see docs/deployment.md.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nexus.hosted import create_hosted_app  # noqa: E402


def _setting(*names: str) -> str | None:
    return next((os.environ[n] for n in names if os.environ.get(n)), None)


database_url = _setting("NEXUS_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL")
if not database_url:
    raise RuntimeError("Set DATABASE_URL (or connect Neon from the Vercel Marketplace) before deploying.")
public_url = _setting("NEXUS_PUBLIC_URL") or f"https://{os.environ['VERCEL_PROJECT_PRODUCTION_URL']}"

app = create_hosted_app(database_url, public_url, signup_code=_setting("NEXUS_SIGNUP_CODE"))[0]
