"""Everything the hosted service reads from its environment, in one place.

    NEXUS_PUBLIC_URL            https:// address people and AI apps connect to (required)
    NEXUS_DATABASE_URL          Postgres URL; DATABASE_URL and POSTGRES_URL are accepted too
    NEXUS_DATA_DIR              folder for SQLite files when no database URL is set (default ~/.nexus/hosted)
    NEXUS_SECRET_KEY            32-byte key (base64 or hex) that seals stored provider keys; see crypto.py
    NEXUS_SECRET_KEYS_OLD       earlier keys, comma separated, still accepted for reading
    NEXUS_SIGNUP                "open" (default), "invite" (needs NEXUS_SIGNUP_CODE or a workspace invitation)
                                or "closed" (workspace invitations only)
    NEXUS_SIGNUP_CODE           the code new accounts must enter when NEXUS_SIGNUP=invite
    NEXUS_SMTP_URL              smtp://user:password@host:587 (or smtps://) for outgoing mail; optional
    NEXUS_MAIL_FROM             From address for outgoing mail
    NEXUS_UPSTREAM_OPENAI       override a provider's base URL (also _ANTHROPIC, _GOOGLE); for proxies and tests
    NEXUS_ALLOW_INSECURE_UPSTREAMS  "1" lets provider base URLs be http or private addresses (local testing only)
    NEXUS_BRIEFING_PIN_SECONDS  how long a gateway briefing is held unchanged (default 900)
    NEXUS_MOCK_PROVIDER         "0" disables the built-in mock model (default on)
    PORT, HOST                  listen address (default 0.0.0.0:8000)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

DEFAULT_UPSTREAMS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
}
SIGNUP_MODES = ("open", "invite", "closed")


def _env(*names: str) -> str | None:
    return next((os.environ[n] for n in names if os.environ.get(n)), None)


@dataclass
class Settings:
    public_url: str
    location: str
    signup: str = "open"
    signup_code: str | None = None
    smtp_url: str | None = None
    mail_from: str = "NEXUS <no-reply@localhost>"
    upstreams: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_UPSTREAMS))
    allow_insecure_upstreams: bool = False
    briefing_pin_seconds: int = 900
    mock_provider: bool = True

    def __post_init__(self) -> None:
        self.public_url = self.public_url.rstrip("/")
        parsed = urlparse(self.public_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("NEXUS_PUBLIC_URL must be a full address such as https://nexus.example.com")
        if self.signup_code and self.signup == "open":
            self.signup = "invite"
        if self.signup not in SIGNUP_MODES:
            raise ValueError(f"NEXUS_SIGNUP must be one of {', '.join(SIGNUP_MODES)}")

    @property
    def origin(self) -> str:
        p = urlparse(self.public_url)
        return f"{p.scheme}://{p.netloc}"

    @property
    def https(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def is_local(self) -> bool:
        return urlparse(self.public_url).hostname in ("localhost", "127.0.0.1", "::1")

    @classmethod
    def from_env(cls, *, public_url: str | None = None, location: str | None = None) -> "Settings":
        public_url = public_url or _env("NEXUS_PUBLIC_URL", "MEMLAYER_PUBLIC_URL")
        if not public_url and os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"):
            public_url = f"https://{os.environ['VERCEL_PROJECT_PRODUCTION_URL']}"
        if not public_url:
            raise ValueError("Set NEXUS_PUBLIC_URL to the address people will connect to.")
        location = (location or _env("NEXUS_DATABASE_URL", "MEMLAYER_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL")
                    or _env("NEXUS_DATA_DIR", "MEMLAYER_DATA_DIR") or "~/.nexus/hosted")
        upstreams = dict(DEFAULT_UPSTREAMS)
        for name in upstreams:
            override = os.environ.get(f"NEXUS_UPSTREAM_{name.upper()}")
            if override:
                upstreams[name] = override.rstrip("/")
        return cls(
            public_url=public_url, location=location,
            signup=os.environ.get("NEXUS_SIGNUP", "open"),
            signup_code=_env("NEXUS_SIGNUP_CODE", "MEMLAYER_SIGNUP_CODE"),
            smtp_url=os.environ.get("NEXUS_SMTP_URL") or None,
            mail_from=os.environ.get("NEXUS_MAIL_FROM", "NEXUS <no-reply@localhost>"),
            upstreams=upstreams,
            allow_insecure_upstreams=os.environ.get("NEXUS_ALLOW_INSECURE_UPSTREAMS") == "1",
            briefing_pin_seconds=int(os.environ.get("NEXUS_BRIEFING_PIN_SECONDS", "900")),
            mock_provider=os.environ.get("NEXUS_MOCK_PROVIDER", "1") != "0",
        )
