"""Accounts for the hosted service: users, OAuth clients, grants and tokens.

This lives in its own SQLite file (accounts.db), separate from memory. Each
user's memory is a separate SQLite file under ``users/<user_id>/memory.db``, so
one user's data can never appear in another user's query, and exporting or
deleting an account is a single file.

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

from .models import NotFound, ValidationError
from .store import MemoryStore, new_id, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    info TEXT NOT NULL,
    created_at TEXT NOT NULL
);
-- An authorization request waiting for the user to sign in and approve it.
CREATE TABLE IF NOT EXISTS auth_requests (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    params TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    data TEXT NOT NULL,
    expires_at REAL NOT NULL
);
-- One grant per approval of an app; every token issued under it shares its id, so revoking is one update.
CREATE TABLE IF NOT EXISTS grants (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    client_name TEXT,
    scopes TEXT NOT NULL,
    resource TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS grants_user ON grants(user_id);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    grant_id TEXT NOT NULL REFERENCES grants(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS tokens_grant ON tokens(grant_id);
-- Private-beta invite requests from the public site.
CREATE TABLE IF NOT EXISTS beta_requests (
    email TEXT PRIMARY KEY COLLATE NOCASE,
    topic TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10
ACCESS_TTL = 3600
REFRESH_TTL = 60 * 60 * 24 * 30
CODE_TTL = 300
REQUEST_TTL = 900
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


class AccountStore:
    def __init__(self, data_dir: str | os.PathLike):
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.data_dir / "accounts.db", isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._lock = threading.RLock()
        self._stores: dict[str, MemoryStore] = {}

    def close(self) -> None:
        for s in self._stores.values():
            s.close()
        self.conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def _one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, args).fetchone()

    # ------------------------------------------------------------------ users

    def create_user(self, email: str, password: str) -> dict[str, Any]:
        email = (email or "").strip()
        if not EMAIL_RE.match(email) or len(email) > 254:
            raise ValidationError("Enter a valid email address.")
        if len(password or "") < MIN_PASSWORD:
            raise ValidationError(f"Use a password of at least {MIN_PASSWORD} characters.")
        user = {"id": new_id("usr"), "email": email, "created_at": now()}
        try:
            with self._tx() as c:
                c.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                          (user["id"], email, hash_password(password), user["created_at"]))
        except sqlite3.IntegrityError:
            raise ValidationError("An account with this email already exists. Sign in instead.") from None
        return user

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM users WHERE email = ?", (email or "").strip())
        ok = check_password(password or "", row["password_hash"] if row else _DUMMY_HASH)
        if not row or not ok or row["disabled"]:
            return None
        with self._tx() as c:
            c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now(), row["id"]))
        return {"id": row["id"], "email": row["email"]}

    def get_user(self, user_id: str) -> dict[str, Any]:
        row = self._one("SELECT id, email, created_at, last_login_at, disabled FROM users WHERE id = ?", user_id)
        if not row:
            raise NotFound(f"user {user_id} not found")
        return dict(row)

    def memory_path(self, user_id: str) -> Path:
        if not re.fullmatch(r"usr_[0-9a-f]{12}", user_id):
            raise ValidationError("bad user id")
        return self.data_dir / "users" / user_id / "memory.db"

    def memory_for(self, user_id: str) -> MemoryStore:
        """The user's own memory database, opened once and reused."""
        with self._lock:
            store = self._stores.get(user_id)
            if store is None:
                store = self._stores[user_id] = MemoryStore(self.memory_path(user_id))
            return store

    # ------------------------------------------------------------ beta list

    def add_beta_request(self, email: str, topic: str = "") -> bool:
        """Record an invite request. Returns False if the email was already on the list."""
        email = (email or "").strip()
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
            c.execute("INSERT OR REPLACE INTO oauth_clients (client_id, info, created_at) VALUES (?, ?, ?)",
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

    def issue_code(self, user_id: str, client_id: str, client_name: str | None, params: dict[str, Any]) -> str:
        code = secrets.token_urlsafe(32)
        grant_id = new_id("grt")
        with self._tx() as c:
            c.execute("DELETE FROM auth_codes WHERE expires_at < ?", (time.time(),))
            c.execute("INSERT INTO grants (id, user_id, client_id, client_name, scopes, resource, created_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (grant_id, user_id, client_id, client_name, json.dumps(params["scopes"]), params.get("resource"), now()))
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
            if not row:
                return None
            c.execute("DELETE FROM auth_codes WHERE code_hash = ?", (digest(code),))
            return row["grant_id"]

    def issue_tokens(self, grant_id: str) -> dict[str, Any]:
        access, refresh = "mla_" + secrets.token_urlsafe(32), "mlr_" + secrets.token_urlsafe(32)
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
            if not row:
                return None
            c.execute("DELETE FROM tokens WHERE grant_id = ?", (row["grant_id"],))
            return row["grant_id"]

    def lookup_token(self, token: str, kind: str) -> dict[str, Any] | None:
        """A live token with its grant and user, or None if expired, revoked or the user is disabled."""
        row = self._one(
            "SELECT t.kind, t.expires_at, g.id AS grant_id, g.user_id, g.client_id, g.scopes, g.resource, "
            "g.last_used_at FROM tokens t JOIN grants g ON g.id = t.grant_id JOIN users u ON u.id = g.user_id "
            "WHERE t.token_hash = ? AND t.kind = ? AND g.revoked_at IS NULL AND u.disabled = 0 AND t.expires_at > ?",
            digest(token or ""), kind, int(time.time()))
        if not row:
            return None
        d = dict(row)
        d["scopes"] = json.loads(d["scopes"])
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
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, client_id, client_name, created_at, last_used_at FROM grants "
                "WHERE user_id = ? AND revoked_at IS NULL ORDER BY created_at DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()
