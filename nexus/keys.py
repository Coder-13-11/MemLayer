"""NEXUS API keys and the provider keys a workspace brings (BYOK). Mixed into ``AccountStore``.

A NEXUS API key (``nxk_...``) is shown once, at creation; only its SHA-256 hash is stored. It acts as a
workspace member with a fixed role (viewer or editor: a key can never administer a workspace) and a set of
scopes: ``memory`` for the REST API, ``gateway`` for the model pass-through.

A provider credential is the workspace's own OpenAI, Anthropic, Google or OpenAI-compatible key. It is sealed
with ``crypto.KeyRing`` and is only ever opened in memory, by the gateway, at the moment of a request. No API
returns it; the dashboard shows the last four characters.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from .models import NotFound, ValidationError

KEY_ROLES = ("viewer", "editor")
KEY_SCOPES = ("memory", "gateway")
PROVIDERS = ("openai", "anthropic", "google", "openai_compatible")
MAX_KEYS_PER_WORKSPACE = 50


class KeysMixin:
    # ------------------------------------------------------------- API keys

    def create_api_key(self, workspace_id: str, name: str, *, role: str = "editor", scopes: list[str] | None = None,
                       default_project: str | None = None, created_by: str | None = None,
                       expires_in_days: int | None = None) -> tuple[dict[str, Any], str]:
        from .accounts import digest
        from .store import new_id, now

        name = (name or "").strip()
        if not 1 <= len(name) <= 80:
            raise ValidationError("Give the key a name of 1 to 80 characters, such as where it will be used.")
        if role not in KEY_ROLES:
            raise ValidationError(f"role must be one of {', '.join(KEY_ROLES)}")
        scopes = scopes or list(KEY_SCOPES)
        if not all(isinstance(s, str) for s in scopes):
            raise ValidationError(f"scopes must be chosen from {', '.join(KEY_SCOPES)}")
        scopes = list(dict.fromkeys(scopes))
        if not scopes or any(s not in KEY_SCOPES for s in scopes):
            raise ValidationError(f"scopes must be chosen from {', '.join(KEY_SCOPES)}")
        if expires_in_days is not None and not 1 <= int(expires_in_days) <= 3650:
            raise ValidationError("expires_in_days must be between 1 and 3650")
        secret = "nxk_" + secrets.token_urlsafe(32)
        kid = new_id("key")
        expires = int(time.time()) + int(expires_in_days) * 86400 if expires_in_days else None
        with self._tx() as c:
            live = c.execute("SELECT count(*) FROM api_keys WHERE workspace_id = ? AND revoked_at IS NULL",
                             (workspace_id,)).fetchone()[0]
            if live >= MAX_KEYS_PER_WORKSPACE:
                raise ValidationError(f"A workspace can have {MAX_KEYS_PER_WORKSPACE} active keys. Revoke one first.")
            c.execute("INSERT INTO api_keys (id, workspace_id, name, prefix, key_hash, role, scopes, default_project, "
                      "created_by, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (kid, workspace_id, name, secret[:12], digest(secret), role, json.dumps(scopes),
                       (default_project or "").strip() or None, created_by, now(), expires))
        return self._key_row(kid), secret

    def _key_row(self, key_id: str) -> dict[str, Any]:
        row = self._one("SELECT id, workspace_id, name, prefix, role, scopes, default_project, created_at, "
                        "last_used_at, expires_at, revoked_at FROM api_keys WHERE id = ?", key_id)
        d = dict(row)
        d["scopes"] = json.loads(d["scopes"])
        return d

    def api_keys(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            ids = [r["id"] for r in self.conn.execute(
                "SELECT id FROM api_keys WHERE workspace_id = ? AND revoked_at IS NULL ORDER BY created_at DESC",
                (workspace_id,))]
        return [self._key_row(i) for i in ids]

    def revoke_api_key(self, workspace_id: str, key_id: str) -> bool:
        from .store import now

        with self._tx() as c:
            return c.execute("UPDATE api_keys SET revoked_at = ? WHERE id = ? AND workspace_id = ? "
                             "AND revoked_at IS NULL", (now(), key_id, workspace_id)).rowcount == 1

    def lookup_api_key(self, secret: str) -> dict[str, Any] | None:
        """The live key behind a secret, or None if it is unknown, revoked or expired."""
        from .accounts import LAST_USED_RESOLUTION, _epoch, digest
        from .store import now

        if not secret or not secret.startswith("nxk_"):
            return None
        row = self._one("SELECT id, workspace_id, name, role, scopes, default_project, last_used_at, expires_at "
                        "FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL", digest(secret))
        if not row or (row["expires_at"] and row["expires_at"] < time.time()):
            return None
        d = dict(row)
        d["scopes"] = json.loads(d["scopes"])
        if not d["last_used_at"] or time.time() - _epoch(d["last_used_at"]) > LAST_USED_RESOLUTION:
            with self._tx() as c:
                c.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now(), d["id"]))
        return d

    # -------------------------------------------------- provider credentials

    def _context(self, workspace_id: str, credential_id: str) -> str:
        return f"nexus:provider-credential:{workspace_id}:{credential_id}"

    def set_credential(self, workspace_id: str, provider: str, secret: str, *, label: str = "default",
                       base_url: str | None = None, created_by: str | None = None) -> dict[str, Any]:
        """Store (or replace) the workspace's key for a provider. Returns its metadata, never the key."""
        from .crypto import SecretsUnavailable
        from .models import require_url
        from .store import new_id, now

        if provider not in PROVIDERS:
            raise ValidationError(f"provider must be one of {', '.join(PROVIDERS)}")
        secret = (secret or "").strip()
        if not 8 <= len(secret) <= 4096 or any(ch.isspace() for ch in secret):
            raise ValidationError("That does not look like an API key.")
        label = (label or "default").strip()[:60] or "default"
        base_url = require_url(base_url, "base_url")
        if provider == "openai_compatible" and not base_url:
            raise ValidationError("An OpenAI-compatible provider needs its base URL, for example "
                                  "https://openrouter.ai/api/v1")
        if base_url and not base_url.startswith("https://") and not self.allow_insecure_upstreams:
            raise ValidationError("A provider base URL must use https.")
        if self.keyring is None:
            raise SecretsUnavailable("This server has no NEXUS_SECRET_KEY configured, so provider keys cannot be "
                                     "stored. Ask the operator to set one.")
        with self._tx() as c:
            old = c.execute("SELECT id FROM provider_credentials WHERE workspace_id = ? AND provider = ? "
                            "AND label = ?", (workspace_id, provider, label)).fetchone()
            cid = old["id"] if old else new_id("crd")
            sealed = self.keyring.seal(secret, self._context(workspace_id, cid))
            hint = secret[-4:]
            if old:
                c.execute("UPDATE provider_credentials SET base_url = ?, ciphertext = ?, wrapped_key = ?, "
                          "key_version = ?, hint = ?, created_by = ?, created_at = ?, last_used_at = NULL WHERE id = ?",
                          (base_url, sealed["ciphertext"], sealed["wrapped_key"], sealed["key_version"], hint,
                           created_by, now(), cid))
            else:
                c.execute("INSERT INTO provider_credentials (id, workspace_id, provider, label, base_url, ciphertext, "
                          "wrapped_key, key_version, hint, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (cid, workspace_id, provider, label, base_url, sealed["ciphertext"], sealed["wrapped_key"],
                           sealed["key_version"], hint, created_by, now()))
        return next(x for x in self.credentials(workspace_id) if x["id"] == cid)

    def credentials(self, workspace_id: str) -> list[dict[str, Any]]:
        """Metadata only: the sealed key never leaves this module except through ``open_credential``."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, provider, label, base_url, hint, created_at, last_used_at FROM provider_credentials "
                "WHERE workspace_id = ? ORDER BY provider, label", (workspace_id,)).fetchall()
        return [dict(r) for r in rows]

    def delete_credential(self, workspace_id: str, credential_id: str) -> bool:
        with self._tx() as c:
            return c.execute("DELETE FROM provider_credentials WHERE id = ? AND workspace_id = ?",
                             (credential_id, workspace_id)).rowcount == 1

    def open_credential(self, workspace_id: str, provider: str, label: str = "default") -> dict[str, Any]:
        """Decrypt a credential for immediate use by the gateway. The caller must not log or return it."""
        from .crypto import SecretsUnavailable
        from .store import now

        row = self._one("SELECT * FROM provider_credentials WHERE workspace_id = ? AND provider = ? AND label = ?",
                        workspace_id, provider, label)
        if not row:
            raise NotFound(f"This workspace has no {provider} key stored. Add one under Providers, or send your "
                           "provider key with the request.")
        if self.keyring is None:
            raise SecretsUnavailable("This server has no NEXUS_SECRET_KEY configured, so stored keys cannot be read.")
        secret = self.keyring.open(row["ciphertext"], row["wrapped_key"], row["key_version"],
                                   self._context(workspace_id, row["id"]))
        with self._tx() as c:
            c.execute("UPDATE provider_credentials SET last_used_at = ? WHERE id = ?", (now(), row["id"]))
        return {"secret": secret, "base_url": row["base_url"], "provider": provider, "id": row["id"]}

    def rotate_credentials(self) -> int:
        """Re-seal every credential that was sealed with an older master key. Returns how many were re-sealed."""
        if self.keyring is None:
            return 0
        with self._lock:
            rows = [dict(r) for r in self.conn.execute("SELECT * FROM provider_credentials")]
        n = 0
        for row in rows:
            if not self.keyring.needs_rotation(row["key_version"]):
                continue
            ctx = self._context(row["workspace_id"], row["id"])
            sealed = self.keyring.seal(self.keyring.open(row["ciphertext"], row["wrapped_key"], row["key_version"], ctx), ctx)
            with self._tx() as c:
                c.execute("UPDATE provider_credentials SET ciphertext = ?, wrapped_key = ?, key_version = ? WHERE id = ?",
                          (sealed["ciphertext"], sealed["wrapped_key"], sealed["key_version"], row["id"]))
            n += 1
        return n
