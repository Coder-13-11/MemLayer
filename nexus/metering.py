"""Usage events, context receipts, the audit log, email links and the outbox. Mixed into ``AccountStore``.

Usage events are what limits are checked against and what billing would read. Receipts record exactly which
records a model was handed. The audit log is append-only: no code path updates or deletes its rows.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from .models import NotFound, ValidationError

EMAIL_TOKEN_TTL = {"reset": 60 * 60, "verify": 60 * 60 * 24 * 3}


def _month_start() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00")


class MeteringMixin:
    # ------------------------------------------------------------------ usage

    def record_usage(self, workspace_id: str, *, channel: str, operation: str, actor: str | None = None,
                     project_id: str | None = None, provider: str | None = None, model: str | None = None,
                     status: int = 200, input_tokens: int = 0, output_tokens: int = 0, context_tokens: int = 0,
                     latency_ms: int = 0, receipt_id: str | None = None) -> str:
        from .store import new_id, now

        eid = new_id("evt")
        with self._tx() as c:
            c.execute("INSERT INTO usage_events (id, workspace_id, at, channel, actor, operation, project_id, provider, "
                      "model, status, input_tokens, output_tokens, context_tokens, latency_ms, receipt_id) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, workspace_id, now(), channel, actor, operation, project_id, provider, model, int(status),
                       int(input_tokens or 0), int(output_tokens or 0), int(context_tokens or 0), int(latency_ms or 0),
                       receipt_id))
        return eid

    def month_gateway_requests(self, workspace_id: str) -> int:
        row = self._one("SELECT count(*) FROM usage_events WHERE workspace_id = ? AND channel = 'gateway' AND at >= ?",
                        workspace_id, _month_start())
        return row[0]

    def usage_summary(self, workspace_id: str, days: int = 30) -> dict[str, Any]:
        days = max(1, min(int(days), 90))
        since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%dT00:00:00")
        with self._lock:
            def rows(sql: str) -> list[dict[str, Any]]:
                # Postgres returns sums as Decimal (and NULL for an empty group); both become plain integers.
                return [{k: int(v or 0) if isinstance(v, Decimal) or (v is None and k not in ("provider", "model", "actor"))
                         else v for k, v in dict(r).items()}
                        for r in self.conn.execute(sql, (workspace_id, since))]

            by_day = rows("SELECT substr(at, 1, 10) AS day, channel, count(*) AS calls, "
                          "sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens "
                          "FROM usage_events WHERE workspace_id = ? AND at >= ? GROUP BY substr(at, 1, 10), channel "
                          "ORDER BY day")
            by_model = rows("SELECT provider, model, count(*) AS calls, sum(input_tokens) AS input_tokens, "
                            "sum(output_tokens) AS output_tokens, sum(context_tokens) AS context_tokens, "
                            "sum(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors, "
                            "sum(latency_ms) AS latency_ms FROM usage_events WHERE workspace_id = ? AND at >= ? "
                            "AND channel = 'gateway' GROUP BY provider, model ORDER BY calls DESC")
            by_actor = rows("SELECT channel, actor, count(*) AS calls, max(at) AS last_at FROM usage_events "
                            "WHERE workspace_id = ? AND at >= ? GROUP BY channel, actor ORDER BY calls DESC LIMIT 25")
        for m in by_model:
            m["avg_latency_ms"] = round((m.pop("latency_ms") or 0) / m["calls"]) if m["calls"] else 0
        ws = self.get_workspace(workspace_id)
        return {"days": days, "since": since, "by_day": by_day, "by_model": by_model, "by_actor": by_actor,
                "month": {"gateway_requests": self.month_gateway_requests(workspace_id),
                          "gateway_limit": ws["limits"]["gateway_requests_month"]},
                "limits": ws["limits"], "plan": ws["plan"]}

    # --------------------------------------------------------------- receipts

    def save_receipt(self, workspace_id: str, *, project_id: str, project_name: str, channel: str, briefing: str,
                     record_refs: Iterable[dict[str, Any]], token_estimate: int, actor: str | None = None,
                     model: str | None = None, query: str | None = None) -> str:
        from .store import new_id, now

        rid = new_id("rcp")
        with self._tx() as c:
            c.execute("INSERT INTO receipts (id, workspace_id, project_id, project_name, at, channel, actor, model, "
                      "query, record_refs, token_estimate, briefing_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (rid, workspace_id, project_id, project_name, now(), channel, actor, model,
                       (query or "")[:500] or None, json.dumps(list(record_refs)), int(token_estimate),
                       hashlib.sha256(briefing.encode()).hexdigest()))
        return rid

    def receipts(self, workspace_id: str, limit: int = 50, project_id: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, project_id, project_name, at, channel, actor, model, query, record_refs, token_estimate "
                "FROM receipts WHERE workspace_id = ? AND (CAST(? AS TEXT) IS NULL OR project_id = ?) "
                "ORDER BY at DESC LIMIT ?", (workspace_id, project_id, project_id, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["record_count"] = len(json.loads(d.pop("record_refs")))
            out.append(d)
        return out

    def get_receipt(self, workspace_id: str, receipt_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM receipts WHERE id = ? AND workspace_id = ?", receipt_id, workspace_id)
        if not row:
            raise NotFound("No such receipt in this workspace.")
        d = dict(row)
        d["record_refs"] = json.loads(d["record_refs"])
        return d

    # ------------------------------------------------------------------ audit

    def audit(self, action: str, *, actor: str, workspace_id: str | None = None, user_id: str | None = None,
              target: str | None = None, detail: dict[str, Any] | None = None, ip: str | None = None) -> None:
        from .store import new_id, now

        with self._tx() as c:
            c.execute("INSERT INTO audit_log (id, workspace_id, user_id, actor, action, target, detail, ip, at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (new_id("aud"), workspace_id, user_id, actor[:200], action, (target or "")[:300] or None,
                       json.dumps(detail or {}), ip, now()))

    def audit_log(self, workspace_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self.conn.execute("SELECT id, actor, action, target, detail, ip, at FROM audit_log "
                                     "WHERE workspace_id = ? ORDER BY at DESC LIMIT ?", (workspace_id, limit)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    # ------------------------------------------------- email links and outbox

    def create_email_token(self, user_id: str, purpose: str) -> str:
        from .accounts import digest

        token = "nxe_" + secrets.token_urlsafe(32)
        with self._tx() as c:
            c.execute("DELETE FROM email_tokens WHERE expires_at < ?", (int(time.time()),))
            c.execute("INSERT INTO email_tokens (token_hash, user_id, purpose, expires_at) VALUES (?,?,?,?)",
                      (digest(token), user_id, purpose, int(time.time()) + EMAIL_TOKEN_TTL[purpose]))
        return token

    def peek_email_token(self, token: str, purpose: str) -> str | None:
        from .accounts import digest

        row = self._one("SELECT user_id FROM email_tokens WHERE token_hash = ? AND purpose = ? AND expires_at > ?",
                        digest(token or ""), purpose, int(time.time()))
        return row["user_id"] if row else None

    def take_email_token(self, token: str, purpose: str) -> str | None:
        """Consume a link: returns the user it was for, exactly once."""
        from .accounts import digest

        with self._tx() as c:
            row = c.execute("SELECT user_id FROM email_tokens WHERE token_hash = ? AND purpose = ? AND expires_at > ?",
                            (digest(token or ""), purpose, int(time.time()))).fetchone()
            if not row or c.execute("DELETE FROM email_tokens WHERE token_hash = ?", (digest(token),)).rowcount != 1:
                return None
            # Using one link retires every other link of the same kind for that account.
            c.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?", (row["user_id"], purpose))
            return row["user_id"]

    def queue_email(self, to_email: str, subject: str, body: str) -> str:
        from .store import new_id, now

        mid = new_id("msg")
        with self._tx() as c:
            c.execute("INSERT INTO outbox (id, to_email, subject, body, created_at) VALUES (?,?,?,?,?)",
                      (mid, to_email, subject[:200], body, now()))
        return mid

    def mark_email(self, message_id: str, *, error: str | None = None) -> None:
        from .store import now

        with self._tx() as c:
            c.execute("UPDATE outbox SET sent_at = ?, error = ? WHERE id = ?",
                      (None if error else now(), (error or "")[:500] or None, message_id))
            if not error:  # a delivered message's links should not also sit in the database
                c.execute("UPDATE outbox SET body = '(sent)' WHERE id = ?", (message_id,))

    def outbox(self, unsent_only: bool = True, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM outbox" + (" WHERE sent_at IS NULL" if unsent_only else "")
                + " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


__all__ = ["MeteringMixin", "ValidationError"]
