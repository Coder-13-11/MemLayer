"""Who is making this request, in which workspace, and what they may do.

Every hosted request (browser session, API key, connected app over MCP, gateway call) is resolved to one
``Principal`` before any handler runs, and handlers reach memory only through it. There is no code path from
a request to a memory store that does not go through a workspace membership, key or grant.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .tenancy import PermissionDenied, role_at_least


@dataclass(frozen=True)
class Principal:
    channel: str                      # app | api | mcp | gateway
    actor: str                        # shown in history: "ana@example.com", "key:ci", "ana@example.com via Claude"
    workspace: dict[str, Any]
    role: str                         # viewer | editor | admin | owner
    user: dict[str, Any] | None = None
    key: dict[str, Any] | None = None
    read_only: bool = False           # an app the user connected with read-only access
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def workspace_id(self) -> str:
        return self.workspace["id"]

    @property
    def human(self) -> str | None:
        """The person's address when a signed-in person is acting directly; None for keys and apps."""
        return self.user["email"] if self.channel == "app" and self.user else None

    def can(self, needed: str) -> bool:
        if self.read_only and needed != "viewer":
            return False
        return role_at_least(self.role, needed)

    def require(self, needed: str, what: str = "do this") -> None:
        if not self.can(needed):
            if self.read_only:
                raise PermissionDenied(f"This connection is read-only, so it cannot {what}.")
            raise PermissionDenied(f"Your role in this workspace ({self.role}) cannot {what}. It needs {needed}.")


_current: ContextVar[Principal | None] = ContextVar("nexus_principal", default=None)


def current() -> Principal:
    p = _current.get()
    if p is None:  # every hosted route sets one first; reaching here means a route forgot to
        raise PermissionDenied("Not signed in.")
    return p


def maybe_current() -> Principal | None:
    return _current.get()


def set_current(p: Principal | None):
    return _current.set(p)


def reset_current(token) -> None:
    _current.reset(token)
