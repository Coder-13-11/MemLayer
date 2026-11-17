"""The control plane: users, sessions, OAuth clients and grants, plus (through mixins) workspaces, API keys,
provider credentials, metering and audit.

This lives in its own database (accounts.db, or a Postgres schema), separate from memory. Each workspace's
memory is a separate SQLite file under ``workspaces/<id>/memory.db``, or a separate Postgres schema, so one
workspace's records can never appear in another workspace's query, and exporting or deleting a workspace is
one file or one schema. The tables are created and upgraded by numbered migrations (``migrate.py``).

Secrets are never stored in the clear: passwords are scrypt hashes, and
authorization codes, access tokens and refresh tokens are stored as SHA-256
hashes of random values.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from collections import OrderedDict

from . import migrate
from .crypto import KeyRing
from .keys import KeysMixin
from .metering import MeteringMixin
from .models import NotFound, ValidationError
from .pg import PgConnection, is_pg_url
from .store import MemoryStore, new_id, now
from .tenancy import TenancyMixin, role_at_least

CONTROL_SCHEMA = os.environ.get("NEXUS_CONTROL_SCHEMA", "nexus_control")  # Postgres schema for these tables
LEGACY_CONTROL_SCHEMA = "memlayer_accounts"
MAX_OPEN_STORES = 256

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10
ACCESS_TTL = 3600
REFRESH_TTL = 60 * 60 * 24 * 30
CODE_TTL = 300
REQUEST_TTL = 900
SESSION_TTL = 60 * 60 * 24 * 30
LAST_USED_RESOLUTION = 60  # seconds; avoid a write on every MCP call


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${key.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, key = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), key)


# Checked against when the email is unknown, so a failed sign-in takes the same time either way.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


class AccountStore(TenancyMixin, KeysMixin, MeteringMixin):
    def __init__(self, location: str | os.PathLike, *, keyring: KeyRing | None = None,
                 allow_insecure_upstreams: bool = False):
        """``location`` is a folder (accounts.db plus one memory file per workspace) or a postgres:// URL (the
        control tables in one schema plus one schema per workspace)."""
        self.location = str(location)
        self._stores: OrderedDict[str, MemoryStore] = OrderedDict()
        self.allow_insecure_upstreams = allow_insecure_upstreams
        if is_pg_url(self.location):
            self.dialect = "pg"
            self.data_dir = None
            schema = CONTROL_SCHEMA
            probe = PgConnection(self.location, "public", schema_sql=None)
            existing = {r["schema_name"] for r in probe.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name IN (?, ?)",
                (CONTROL_SCHEMA, LEGACY_CONTROL_SCHEMA))}
            if LEGACY_CONTROL_SCHEMA in existing and CONTROL_SCHEMA not in existing:
                # A database created by MemLayer 0.1: upgrade its accounts in place instead of starting empty.
                schema = LEGACY_CONTROL_SCHEMA
            self.control_schema = schema
            self.conn = PgConnection(self.location, schema, schema_sql=None)
            self._lock = self.conn.lock
            with self._lock:
                self.conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        else:
            self.dialect = "sqlite"
            self.data_dir = Path(location).expanduser()
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.data_dir / "accounts.db", isolation_level=None, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA busy_timeout = 5000")
            self.conn.execute("PRAGMA journal_mode = WAL")
            self._lock = threading.RLock()
        self.migrations_applied = migrate.apply(self)
        self.keyring = keyring or KeyRing.from_environment(self.data_dir)

    def close(self) -> None:
        for s in self._stores.values():
            s.close()
        self.conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self.dialect == "pg":
                self.conn.begin()
            else:
                self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                if self.dialect == "pg":
                    self.conn.rollback()
                else:
                    self.conn.execute("ROLLBACK")
                raise
            if self.dialect == "pg":
                self.conn.commit()
            else:
                self.conn.execute("COMMIT")

    def _one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, args).fetchone()

    # ------------------------------------------------------------------ users

    def create_user(self, email: str, password: str, name: str = "") -> dict[str, Any]:
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email) or len(email) > 254:
            raise ValidationError("Enter a valid email address.")
        check_new_password(password)
        name = (name or "").strip()[:80]
        user = {"id": new_id("usr"), "email": email, "name": name, "created_at": now()}
        try:
            with self._tx() as c:
                c.execute("INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                          (user["id"], email, name, hash_password(password), user["created_at"]))
        except Exception as e:  # sqlite3.IntegrityError or psycopg's UniqueViolation
            if "UNIQUE" in str(e).upper() or "unique" in type(e).__name__.lower() or "duplicate" in str(e):
                raise ValidationError("An account with this email already exists. Sign in instead.") from None
            raise
        return user

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM users WHERE email = ?", (email or "").strip().lower())
        ok = check_password(password or "", row["password_hash"] if row else _DUMMY_HASH)
        if not row or not ok or row["disabled"]:
            return None
        with self._tx() as c:
            c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now(), row["id"]))
        return {"id": row["id"], "email": row["email"], "name": row["name"]}

    def get_user(self, user_id: str) -> dict[str, Any]:
        row = self._one("SELECT id, email, name, created_at, last_login_at, disabled, email_verified_at "
                        "FROM users WHERE id = ?", user_id)
        if not row:
            raise NotFound(f"user {user_id} not found")
        return dict(row)

    def find_user(self, email: str) -> dict[str, Any] | None:
        row = self._one("SELECT id, email, name FROM users WHERE email = ? AND disabled = 0",
                        (email or "").strip().lower())
        return dict(row) if row else None

    def update_profile(self, user_id: str, name: str) -> dict[str, Any]:
        with self._tx() as c:
            c.execute("UPDATE users SET name = ? WHERE id = ?", ((name or "").strip()[:80], user_id))
        return self.get_user(user_id)

    def set_password(self, user_id: str, password: str, *, keep_session: str | None = None) -> None:
        """Change a password and sign the account out everywhere else: other browsers and every connected app."""
        check_new_password(password)
        with self._tx() as c:
            c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
            c.execute("DELETE FROM web_sessions WHERE user_id = ? AND id_hash <> ?",
                      (user_id, digest(keep_session) if keep_session else ""))
            c.execute("DELETE FROM tokens WHERE grant_id IN (SELECT id FROM grants WHERE user_id = ?)", (user_id,))

    def verify_password(self, user_id: str, password: str) -> bool:
        row = self._one("SELECT password_hash FROM users WHERE id = ?", user_id)
        return bool(row) and check_password(password or "", row["password_hash"])

    def mark_verified(self, user_id: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE users SET email_verified_at = coalesce(email_verified_at, ?) WHERE id = ?",
                      (now(), user_id))

    def delete_user(self, user_id: str) -> dict[str, Any]:
        """Delete an account. Workspaces where they are the only member go with it; a workspace they solely own
        but share with others must be handed over or deleted first."""
        mine = self.workspaces_for(user_id)
        for ws in mine:
            if ws["role"] == "owner" and ws["member_count"] > 1:
                owners = self._one("SELECT count(*) FROM memberships WHERE workspace_id = ? AND role = 'owner'", ws["id"])
                if owners[0] == 1:
                    raise ValidationError(f"You are the only owner of \u201c{ws['name']}\u201d, which has other members. "
                                          "Make someone else an owner, or delete the workspace, first.")
        removed = [self.delete_workspace(ws["id"])["name"] for ws in mine if ws["member_count"] == 1]
        with self._tx() as c:
            c.execute("DELETE FROM tokens WHERE grant_id IN (SELECT id FROM grants WHERE user_id = ?)", (user_id,))
            c.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return {"deleted": user_id, "workspaces_deleted": removed}

    # --------------------------------------------------------- memory stores

    def memory_for(self, workspace_id: str) -> MemoryStore:
        """The workspace's own memory (a file, or a Postgres schema). Opened on first use and kept open; the
        least recently used SQLite files are closed once many are open."""
        if not re.fullmatch(r"wsp_[0-9a-f]{12}", workspace_id or ""):
            raise ValidationError("bad workspace id")
        with self._lock:
            store = self._stores.get(workspace_id)
            if store is not None:
                self._stores.move_to_end(workspace_id)
                return store
            ref = self.get_workspace(workspace_id)["store_ref"]
            if self.dialect == "pg":
                store = MemoryStore(self.location, schema=ref)
            else:
                target = (self.data_dir / ref).resolve()
                if self.data_dir.resolve() not in target.parents:
                    raise ValidationError("bad store location")
                store = MemoryStore(target)
            self._stores[workspace_id] = store
            while self.dialect != "pg" and len(self._stores) > MAX_OPEN_STORES:
                _, oldest = self._stores.popitem(last=False)
                oldest.close()
            return store

    # ------------------------------------------------------- dashboard sessions

    def create_session(self, user_id: str) -> str:
        """A signed-in browser session for the dashboard. Only the hash of the id is stored."""
        sid = secrets.token_urlsafe(32)
        with self._tx() as c:
            c.execute("DELETE FROM web_sessions WHERE expires_at < ?", (int(time.time()),))
            c.execute("INSERT INTO web_sessions (id_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                      (digest(sid), user_id, now(), int(time.time()) + SESSION_TTL))
        return sid

    def session_user(self, sid: str | None) -> dict[str, Any] | None:
        if not sid:
            return None
        row = self._one("SELECT u.id, u.email, u.name FROM web_sessions s JOIN users u ON u.id = s.user_id "
                        "WHERE s.id_hash = ? AND s.expires_at > ? AND u.disabled = 0", digest(sid), int(time.time()))
        return {"id": row["id"], "email": row["email"], "name": row["name"]} if row else None

    def end_session(self, sid: str | None) -> None:
        if sid:
            with self._tx() as c:
                c.execute("DELETE FROM web_sessions WHERE id_hash = ?", (digest(sid),))

    # ------------------------------------------------------------ beta list

    def add_beta_request(self, email: str, topic: str = "") -> bool:
        """Record an invite request. Returns False if the email was already on the list."""
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email) or len(email) > 254:
            raise ValidationError("Enter an email address we can send the invite to.")
        with self._tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO beta_requests (email, topic, created_at) VALUES (?, ?, ?)",
                            (email, (topic or "").strip()[:500], now()))
            return cur.rowcount == 1

    def beta_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM beta_requests ORDER BY created_at")]

    # ---------------------------------------------------------- OAuth clients

    def save_client(self, client_id: str, info: dict[str, Any]) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO oauth_clients (client_id, info, created_at) VALUES (?, ?, ?) "
                      "ON CONFLICT (client_id) DO UPDATE SET info = excluded.info",
                      (client_id, json.dumps(info), now()))

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT info FROM oauth_clients WHERE client_id = ?", client_id)
        return json.loads(row["info"]) if row else None

    # ------------------------------------------------- pending authorizations

    def create_request(self, client_id: str, params: dict[str, Any]) -> str:
        rid = secrets.token_urlsafe(24)
        with self._tx() as c:
            c.execute("DELETE FROM auth_requests WHERE expires_at < ?", (time.time(),))
            c.execute("INSERT INTO auth_requests (id, client_id, params, expires_at) VALUES (?, ?, ?, ?)",
                      (rid, client_id, json.dumps(params), time.time() + REQUEST_TTL))
        return rid

    def get_request(self, rid: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM auth_requests WHERE id = ? AND expires_at >= ?", rid or "", time.time())
        if not row:
            return None
        return {"id": row["id"], "client_id": row["client_id"], "params": json.loads(row["params"])}

    def take_request(self, rid: str) -> dict[str, Any] | None:
        """Consume a pending request so it can be approved or denied exactly once."""
        req = self.get_request(rid)
        if req:
            with self._tx() as c:
                if c.execute("DELETE FROM auth_requests WHERE id = ?", (rid,)).rowcount != 1:
                    return None
        return req

    # ---------------------------------------------------- codes, grants, tokens

    def issue_code(self, user_id: str, client_id: str, client_name: str | None, params: dict[str, Any], *,
                   workspace_id: str, access: str = "write") -> str:
        """Record the user's approval of an app for one workspace and return the one-time authorization code."""
        if access not in ("read", "write"):
            raise ValidationError("access must be read or write")
        role = self.role_in(workspace_id, user_id)
        if role is None:
            raise ValidationError("You are not a member of that workspace.")
        if not role_at_least(role, "editor"):
            access = "read"
        code = secrets.token_urlsafe(32)
        grant_id = new_id("grt")
        with self._tx() as c:
            c.execute("DELETE FROM auth_codes WHERE expires_at < ?", (time.time(),))
            c.execute("INSERT INTO grants (id, user_id, client_id, client_name, scopes, resource, created_at, "
                      "workspace_id, access) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (grant_id, user_id, client_id, client_name, json.dumps(params["scopes"]), params.get("resource"),
                       now(), workspace_id, access))
            c.execute("INSERT INTO auth_codes (code_hash, grant_id, data, expires_at) VALUES (?, ?, ?, ?)",
                      (digest(code), grant_id, json.dumps({**params, "client_id": client_id, "user_id": user_id}),
                       time.time() + CODE_TTL))
        return code

    def load_code(self, code: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM auth_codes WHERE code_hash = ?", digest(code or ""))
        if not row:
            return None
        return {**json.loads(row["data"]), "grant_id": row["grant_id"], "expires_at": row["expires_at"]}

    def redeem_code(self, code: str) -> str | None:
        """Delete the code and return its grant id; None if it was already used."""
        with self._tx() as c:
            row = c.execute("SELECT grant_id FROM auth_codes WHERE code_hash = ?", (digest(code),)).fetchone()
            # The delete decides who wins: with several server instances, only one of them removes the row.
            if not row or c.execute("DELETE FROM auth_codes WHERE code_hash = ?", (digest(code),)).rowcount != 1:
                return None
            return row["grant_id"]

    def issue_tokens(self, grant_id: str) -> dict[str, Any]:
        access, refresh = "nxa_" + secrets.token_urlsafe(32), "nxr_" + secrets.token_urlsafe(32)
        t = int(time.time())
        with self._tx() as c:
            c.executemany("INSERT INTO tokens (token_hash, kind, grant_id, expires_at) VALUES (?, ?, ?, ?)",
                          [(digest(access), "access", grant_id, t + ACCESS_TTL),
                           (digest(refresh), "refresh", grant_id, t + REFRESH_TTL)])
        return {"access_token": access, "refresh_token": refresh, "expires_in": ACCESS_TTL}

    def rotate_refresh(self, refresh: str) -> str | None:
        """Consume a refresh token (single use) and drop the grant's old access tokens."""
        with self._tx() as c:
            row = c.execute("SELECT grant_id FROM tokens WHERE token_hash = ? AND kind = 'refresh'",
                            (digest(refresh),)).fetchone()
            if not row or c.execute("DELETE FROM tokens WHERE token_hash = ? AND kind = 'refresh'",
                                    (digest(refresh),)).rowcount != 1:
                return None
            c.execute("DELETE FROM tokens WHERE grant_id = ?", (row["grant_id"],))
            return row["grant_id"]

    def lookup_token(self, token: str, kind: str) -> dict[str, Any] | None:
        """A live token with its grant, user and workspace, or None if it expired, was revoked, the user is
        disabled, or the user is no longer a member of the workspace. The role is read on every call, so a
        change of role or a removal takes effect on the app's next request."""
        sql = ("SELECT t.kind, t.expires_at, g.id AS grant_id, g.user_id, g.client_id, g.client_name, g.scopes, "
               "g.resource, g.last_used_at, g.workspace_id, g.access, u.email, m.role "
               "FROM tokens t JOIN grants g ON g.id = t.grant_id JOIN users u ON u.id = g.user_id "
               "LEFT JOIN memberships m ON m.workspace_id = g.workspace_id AND m.user_id = g.user_id "
               "WHERE t.token_hash = ? AND t.kind = ? AND g.revoked_at IS NULL AND u.disabled = 0 AND t.expires_at > ?")
        args = (digest(token or ""), kind, int(time.time()))
        row = self._one(sql, *args)
        if row and row["workspace_id"] is None:
            # An approval from before workspaces existed: it was for the user's own memory, which is now
            # their personal workspace.
            ws = self.ensure_personal_workspace({"id": row["user_id"], "email": row["email"]})
            with self._tx() as c:
                c.execute("UPDATE grants SET workspace_id = ? WHERE user_id = ? AND workspace_id IS NULL",
                          (ws["id"], row["user_id"]))
            row = self._one(sql, *args)
        if not row or row["role"] is None:
            return None
        d = dict(row)
        d["scopes"] = json.loads(d["scopes"])
        d["can_write"] = d["access"] == "write" and role_at_least(d["role"], "editor")
        return d

    def touch_grant(self, grant_id: str, last_used_at: str | None) -> None:
        """Record when an app last used its access, at most once a minute."""
        if last_used_at and time.time() - _epoch(last_used_at) < LAST_USED_RESOLUTION:
            return
        with self._tx() as c:
            c.execute("UPDATE grants SET last_used_at = ? WHERE id = ?", (now(), grant_id))

    def revoke_grant(self, grant_id: str, user_id: str | None = None) -> bool:
        with self._tx() as c:
            sql, args = "UPDATE grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", [now(), grant_id]
            if user_id:
                sql += " AND user_id = ?"
                args.append(user_id)
            changed = c.execute(sql, args).rowcount == 1
            c.execute("DELETE FROM tokens WHERE grant_id = ?", (grant_id,))
        return changed

    def grants_for(self, user_id: str) -> list[dict[str, Any]]:
        """Apps this person connected, across their workspaces."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT g.id, g.client_id, g.client_name, g.created_at, g.last_used_at, g.access, g.workspace_id, "
                "w.name AS workspace FROM grants g LEFT JOIN workspaces w ON w.id = g.workspace_id "
                "WHERE g.user_id = ? AND g.revoked_at IS NULL ORDER BY g.created_at DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def workspace_grants(self, workspace_id: str) -> list[dict[str, Any]]:
        """Every app connected to a workspace, with who connected it. For workspace admins."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT g.id, g.client_id, g.client_name, g.created_at, g.last_used_at, g.access, g.user_id, u.email "
                "FROM grants g JOIN users u ON u.id = g.user_id WHERE g.workspace_id = ? AND g.revoked_at IS NULL "
                "ORDER BY g.created_at DESC", (workspace_id,)).fetchall()
        return [dict(r) for r in rows]

    def revoke_workspace_grant(self, workspace_id: str, grant_id: str, user_id: str | None = None) -> bool:
        """Disconnect an app from a workspace. With ``user_id``, only that person's own connection."""
        with self._tx() as c:
            sql, args = ("UPDATE grants SET revoked_at = ? WHERE id = ? AND workspace_id = ? AND revoked_at IS NULL",
                         [now(), grant_id, workspace_id])
            if user_id:
                sql += " AND user_id = ?"
                args.append(user_id)
            changed = c.execute(sql, args).rowcount == 1
            if changed:
                c.execute("DELETE FROM tokens WHERE grant_id = ?", (grant_id,))
        return changed


def check_new_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise ValidationError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if len(password) > 1024:
        raise ValidationError("That password is too long.")


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()
