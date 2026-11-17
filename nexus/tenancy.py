"""Workspaces, memberships and invitations: who may reach which memory, and as what.

A workspace owns exactly one memory store. People reach it through a membership with a role:

    viewer   read everything
    editor   also write records, projects and imports
    admin    also manage members, invitations, API keys, provider keys and connected apps
    owner    also rename or delete the workspace and decide who else is an owner

Every workspace always has at least one owner. These methods are mixed into ``AccountStore`` and share its
connection and transactions.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from typing import Any

from .models import NotFound, ValidationError

ROLES = ("viewer", "editor", "admin", "owner")
INVITABLE_ROLES = ("viewer", "editor", "admin")
INVITE_TTL = 60 * 60 * 24 * 7

# Limits per plan. "free" is what every workspace gets today; nothing is charged for and there is no billing
# code. The second tier exists so limits are data, not constants scattered through the code.
PLANS: dict[str, dict[str, int]] = {
    "free": {"members": 5, "projects": 20, "records": 5_000, "gateway_requests_month": 2_000,
             "api_requests_minute": 120},
    "team": {"members": 50, "projects": 500, "records": 250_000, "gateway_requests_month": 200_000,
             "api_requests_minute": 600},
}


class PermissionDenied(PermissionError):
    """The signed-in person, key or app is not allowed to do this."""


class LimitReached(ValidationError):
    """A plan limit would be exceeded."""


def role_at_least(role: str | None, needed: str) -> bool:
    return role in ROLES and ROLES.index(role) >= ROLES.index(needed)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"['\u2019]", "", name.lower())).strip("-")[:40].strip("-")
    return slug or "workspace"


class TenancyMixin:
    # ------------------------------------------------------------ workspaces

    def create_workspace(self, name: str, owner_id: str, *, store_ref: str | None = None) -> dict[str, Any]:
        from .store import new_id, now

        name = (name or "").strip()
        if not 1 <= len(name) <= 80:
            raise ValidationError("Give the workspace a name of 1 to 80 characters.")
        wid = new_id("wsp")
        if store_ref is None:
            store_ref = f"w_{wid[4:]}" if self.dialect == "pg" else f"workspaces/{wid}/memory.db"
        base = slugify(name)
        with self._tx() as c:
            slug, n = base, 1
            while c.execute("SELECT 1 FROM workspaces WHERE slug = ?", (slug,)).fetchone():
                n += 1
                slug = f"{base}-{n}"
            ts = now()
            c.execute("INSERT INTO workspaces (id, name, slug, store_ref, created_by, created_at) VALUES (?,?,?,?,?,?)",
                      (wid, name, slug, store_ref, owner_id, ts))
            c.execute("INSERT INTO memberships (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)",
                      (wid, owner_id, "owner", ts))
        return self.get_workspace(wid)

    def ensure_personal_workspace(self, user: dict[str, Any]) -> dict[str, Any]:
        """Everyone has at least one workspace. Accounts from before workspaces existed get one that points at
        the memory they already had, so nothing they saved moves or disappears."""
        existing = self.workspaces_for(user["id"])
        if existing:
            return existing[0]
        if self.dialect == "pg":
            legacy = f"u_{user['id'][4:]}"
            had_memory = bool(self._one("SELECT 1 FROM information_schema.schemata WHERE schema_name = ?", legacy))
        else:
            legacy = f"users/{user['id']}/memory.db"
            had_memory = (self.data_dir / legacy).exists()
        taken = self._one("SELECT 1 FROM workspaces WHERE store_ref = ?", legacy)
        label = (user.get("name") or user["email"].split("@")[0]).strip() or "My"
        return self.create_workspace(f"{label}'s workspace"[:80], user["id"],
                                     store_ref=legacy if had_memory and not taken else None)

    def get_workspace(self, ref: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM workspaces WHERE id = ? OR slug = ?", ref, ref)
        if not row:
            raise NotFound("No such workspace.")
        d = dict(row)
        d["limits"] = {**PLANS.get(d["plan"], PLANS["free"]), **json.loads(d["limits"] or "{}")}
        return d

    def workspaces_for(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT w.id, w.name, w.slug, w.plan, w.created_at, m.role, "
                "(SELECT count(*) FROM memberships x WHERE x.workspace_id = w.id) AS member_count "
                "FROM memberships m JOIN workspaces w ON w.id = m.workspace_id WHERE m.user_id = ? "
                "ORDER BY m.created_at", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def role_in(self, workspace_id: str, user_id: str) -> str | None:
        row = self._one("SELECT m.role FROM memberships m JOIN users u ON u.id = m.user_id "
                        "WHERE m.workspace_id = ? AND m.user_id = ? AND u.disabled = 0", workspace_id, user_id)
        return row["role"] if row else None

    def rename_workspace(self, workspace_id: str, name: str) -> dict[str, Any]:
        name = (name or "").strip()
        if not 1 <= len(name) <= 80:
            raise ValidationError("Give the workspace a name of 1 to 80 characters.")
        with self._tx() as c:
            c.execute("UPDATE workspaces SET name = ? WHERE id = ?", (name, workspace_id))
        return self.get_workspace(workspace_id)

    def set_plan(self, workspace_id: str, plan: str, limits: dict[str, int] | None = None) -> dict[str, Any]:
        """Operator action (CLI): change a workspace's plan or override individual limits."""
        if plan not in PLANS:
            raise ValidationError(f"plan must be one of {', '.join(PLANS)}")
        with self._tx() as c:
            c.execute("UPDATE workspaces SET plan = ?, limits = ? WHERE id = ?",
                      (plan, json.dumps(limits or {}), workspace_id))
        return self.get_workspace(workspace_id)

    def delete_workspace(self, workspace_id: str) -> dict[str, Any]:
        """Remove the workspace, everything that hangs off it, and its memory store. Not reversible."""
        ws = self.get_workspace(workspace_id)
        with self._lock:
            store = self._stores.pop(workspace_id, None)
            if store is not None and self.dialect != "pg":
                store.close()
        with self._tx() as c:
            c.execute("DELETE FROM tokens WHERE grant_id IN (SELECT id FROM grants WHERE workspace_id = ?)",
                      (workspace_id,))
            c.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            if self.dialect == "pg":
                if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ws["store_ref"]):
                    raise ValidationError("refusing to drop an unexpected schema name")
                c.execute(f'DROP SCHEMA IF EXISTS "{ws["store_ref"]}" CASCADE')
        if self.dialect != "pg":
            target = (self.data_dir / ws["store_ref"]).resolve()
            if self.data_dir.resolve() in target.parents and target.parent != self.data_dir.resolve():
                shutil.rmtree(target.parent, ignore_errors=True)
        return {"deleted": workspace_id, "name": ws["name"]}

    # ---------------------------------------------------------------- members

    def members(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT u.id, u.email, u.name, m.role, m.created_at AS joined_at, u.last_login_at "
                "FROM memberships m JOIN users u ON u.id = m.user_id WHERE m.workspace_id = ? "
                "ORDER BY m.created_at", (workspace_id,)).fetchall()
        return [dict(r) for r in rows]

    def _lock_workspace(self, c: Any, workspace_id: str) -> None:
        """Hold the workspace's row for this transaction, so two instances cannot each remove "the other" owner.
        SQLite already allows one writer at a time."""
        if self.dialect == "pg":
            c.execute("SELECT id FROM workspaces WHERE id = ? FOR UPDATE", (workspace_id,))

    def _owner_count(self, c: Any, workspace_id: str) -> int:
        return c.execute("SELECT count(*) FROM memberships WHERE workspace_id = ? AND role = 'owner'",
                         (workspace_id,)).fetchone()[0]

    def set_role(self, workspace_id: str, user_id: str, role: str, *, by_role: str) -> None:
        """Change a member's role. Only an owner may create or demote an owner; the last owner stays."""
        if role not in ROLES:
            raise ValidationError(f"role must be one of {', '.join(ROLES)}")
        with self._tx() as c:
            self._lock_workspace(c, workspace_id)
            row = c.execute("SELECT role FROM memberships WHERE workspace_id = ? AND user_id = ?",
                            (workspace_id, user_id)).fetchone()
            if not row:
                raise NotFound("That person is not a member of this workspace.")
            if "owner" in (role, row["role"]) and by_role != "owner":
                raise PermissionDenied("Only an owner can change who the owners are.")
            if row["role"] == "owner" and role != "owner" and self._owner_count(c, workspace_id) == 1:
                raise ValidationError("A workspace needs at least one owner. Make someone else an owner first.")
            c.execute("UPDATE memberships SET role = ? WHERE workspace_id = ? AND user_id = ?",
                      (role, workspace_id, user_id))
            if not role_at_least(role, "editor"):  # a demoted member's apps can no longer write
                c.execute("UPDATE grants SET access = 'read' WHERE workspace_id = ? AND user_id = ?",
                          (workspace_id, user_id))

    def remove_member(self, workspace_id: str, user_id: str, *, by_role: str) -> None:
        """Remove a member (or leave). Their connected apps lose access to this workspace at once."""
        with self._tx() as c:
            self._lock_workspace(c, workspace_id)
            row = c.execute("SELECT role FROM memberships WHERE workspace_id = ? AND user_id = ?",
                            (workspace_id, user_id)).fetchone()
            if not row:
                raise NotFound("That person is not a member of this workspace.")
            if row["role"] == "owner":
                if by_role != "owner":
                    raise PermissionDenied("Only an owner can remove an owner.")
                if self._owner_count(c, workspace_id) == 1:
                    raise ValidationError("A workspace needs at least one owner. Make someone else an owner first.")
            c.execute("DELETE FROM tokens WHERE grant_id IN "
                      "(SELECT id FROM grants WHERE workspace_id = ? AND user_id = ?)", (workspace_id, user_id))
            c.execute("DELETE FROM grants WHERE workspace_id = ? AND user_id = ?", (workspace_id, user_id))
            c.execute("DELETE FROM memberships WHERE workspace_id = ? AND user_id = ?", (workspace_id, user_id))

    # ------------------------------------------------------------ invitations

    def create_invitation(self, workspace_id: str, email: str, role: str, invited_by: str) -> tuple[dict[str, Any], str]:
        """Returns the invitation and its one-time token. Only the token's hash is kept."""
        from .accounts import EMAIL_RE, digest
        from .store import new_id, now

        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email) or len(email) > 254:
            raise ValidationError("Enter a valid email address.")
        if role not in INVITABLE_ROLES:
            raise ValidationError(f"role must be one of {', '.join(INVITABLE_ROLES)}")
        ws = self.get_workspace(workspace_id)
        token = "nxi_" + secrets.token_urlsafe(32)
        with self._tx() as c:
            if c.execute("SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id "
                         "WHERE m.workspace_id = ? AND u.email = ?", (workspace_id, email)).fetchone():
                raise ValidationError("That person is already a member.")
            members = c.execute("SELECT count(*) FROM memberships WHERE workspace_id = ?", (workspace_id,)).fetchone()[0]
            pending = c.execute("SELECT count(*) FROM invitations WHERE workspace_id = ? AND accepted_at IS NULL "
                                "AND revoked_at IS NULL AND expires_at > ? AND email <> ?",
                                (workspace_id, int(time.time()), email)).fetchone()[0]
            if members + pending + 1 > ws["limits"]["members"]:
                raise LimitReached(f"This workspace's plan allows {ws['limits']['members']} members.")
            # A new invitation to the same address replaces the earlier one.
            c.execute("UPDATE invitations SET revoked_at = ? WHERE workspace_id = ? AND email = ? "
                      "AND accepted_at IS NULL AND revoked_at IS NULL", (now(), workspace_id, email))
            iid = new_id("inv")
            c.execute("INSERT INTO invitations (id, workspace_id, email, role, token_hash, invited_by, created_at, "
                      "expires_at) VALUES (?,?,?,?,?,?,?,?)",
                      (iid, workspace_id, email, role, digest(token), invited_by, now(), int(time.time()) + INVITE_TTL))
        return {"id": iid, "email": email, "role": role, "workspace": ws["name"]}, token

    def invitations(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT i.id, i.email, i.role, i.created_at, i.expires_at, u.email AS invited_by "
                "FROM invitations i LEFT JOIN users u ON u.id = i.invited_by "
                "WHERE i.workspace_id = ? AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > ? "
                "ORDER BY i.created_at DESC", (workspace_id, int(time.time()))).fetchall()
        return [dict(r) for r in rows]

    def revoke_invitation(self, workspace_id: str, invitation_id: str) -> bool:
        from .store import now

        with self._tx() as c:
            return c.execute("UPDATE invitations SET revoked_at = ? WHERE id = ? AND workspace_id = ? "
                             "AND accepted_at IS NULL AND revoked_at IS NULL",
                             (now(), invitation_id, workspace_id)).rowcount == 1

    def find_invitation(self, token: str) -> dict[str, Any] | None:
        """A live invitation by its token, with the workspace's name, or None."""
        from .accounts import digest

        row = self._one(
            "SELECT i.id, i.workspace_id, i.email, i.role, w.name AS workspace, w.slug, u.email AS invited_by "
            "FROM invitations i JOIN workspaces w ON w.id = i.workspace_id LEFT JOIN users u ON u.id = i.invited_by "
            "WHERE i.token_hash = ? AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > ?",
            digest(token or ""), int(time.time()))
        return dict(row) if row else None

    def accept_invitation(self, token: str, user: dict[str, Any]) -> dict[str, Any]:
        """Join the workspace. The invitation is for one address: a different account cannot use the link."""
        from .accounts import digest
        from .store import now

        with self._tx() as c:
            inv = c.execute(
                "SELECT * FROM invitations WHERE token_hash = ? AND accepted_at IS NULL AND revoked_at IS NULL "
                "AND expires_at > ?", (digest(token or ""), int(time.time()))).fetchone()
            if not inv:
                raise ValidationError("This invitation has expired or was already used. Ask for a new one.")
            if inv["email"] != user["email"].lower():
                raise PermissionDenied(f"This invitation was sent to {inv['email']}. Sign in with that address.")
            ts = now()
            if c.execute("UPDATE invitations SET accepted_at = ? WHERE id = ? AND accepted_at IS NULL",
                         (ts, inv["id"])).rowcount != 1:
                raise ValidationError("This invitation has expired or was already used. Ask for a new one.")
            if not c.execute("SELECT 1 FROM memberships WHERE workspace_id = ? AND user_id = ?",
                             (inv["workspace_id"], user["id"])).fetchone():
                c.execute("INSERT INTO memberships (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)",
                          (inv["workspace_id"], user["id"], inv["role"], ts))
            # Joining does not mark the address as confirmed: the admin who created the invitation can read
            # its link, so following it proves nothing about who owns the mailbox.
        return self.get_workspace(inv["workspace_id"])


def require_under_limit_hook(accounts: Any, workspace: dict[str, Any], what: str) -> None:
    """Raise ``LimitReached`` if the workspace is at its plan's number of ``projects`` or ``records``."""
    projects = accounts.memory_for(workspace["id"]).list_projects()
    count = len(projects) if what == "projects" else sum(p["record_count"] for p in projects)
    if count >= workspace["limits"][what]:
        raise LimitReached(f"This workspace's plan allows {workspace['limits'][what]:,} {what}.")
