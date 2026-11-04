"""Vercel entry point: the whole hosted service as one ASGI app. Vercel finds this file by name and sends it
every request with its original path, so no rewrites are needed.

Environment (set in the Vercel project):
  DATABASE_URL or POSTGRES_URL   Postgres connection string (the pooled one). Neon from the Vercel Marketplace
                                 sets one of these automatically.
  MEMLAYER_PUBLIC_URL            optional; defaults to https://<production domain>
  MEMLAYER_SIGNUP_CODE           optional invite code required to create an account
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memlayer.hosted import create_hosted_app  # noqa: E402


def _setting(*names: str) -> str | None:
    return next((os.environ[n] for n in names if os.environ.get(n)), None)


database_url = _setting("MEMLAYER_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL")
if not database_url:
    raise RuntimeError("Set DATABASE_URL (or connect Neon from the Vercel Marketplace) before deploying.")
public_url = _setting("MEMLAYER_PUBLIC_URL") or f"https://{os.environ['VERCEL_PROJECT_PRODUCTION_URL']}"

app = create_hosted_app(database_url, public_url, signup_code=_setting("MEMLAYER_SIGNUP_CODE"))[0]
