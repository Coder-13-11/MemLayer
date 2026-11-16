"""Outgoing mail: invitations, password resets and address checks.

Every message is first written to the ``outbox`` table. If NEXUS_SMTP_URL is set, it is then sent in a
background thread and marked sent (or marked with the error). With no mail server configured the message
simply stays in the outbox, where the operator can read it (``nexus admin outbox``); the app also shows
invitation links to the person who created them, so a team can start without mail.
"""

from __future__ import annotations

import smtplib
import ssl
import threading
from email.message import EmailMessage
from typing import Any
from urllib.parse import unquote, urlparse


class Mailer:
    def __init__(self, accounts: Any, smtp_url: str | None, mail_from: str):
        self.accounts, self.smtp_url, self.mail_from = accounts, smtp_url, mail_from

    @property
    def configured(self) -> bool:
        return bool(self.smtp_url)

    def send(self, to: str, subject: str, body: str) -> bool:
        """Queue a message; returns True if a mail server is configured and delivery was started."""
        mid = self.accounts.queue_email(to, subject, body)
        if not self.smtp_url:
            return False
        threading.Thread(target=self._deliver, args=(mid, to, subject, body), daemon=True).start()
        return True

    def _deliver(self, mid: str, to: str, subject: str, body: str) -> None:
        try:
            url = urlparse(self.smtp_url)
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = self.mail_from, to, subject
            msg.set_content(body)
            port = url.port or (465 if url.scheme == "smtps" else 587)
            if url.scheme == "smtps":
                server: smtplib.SMTP = smtplib.SMTP_SSL(url.hostname, port, timeout=20, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(url.hostname, port, timeout=20)
                server.starttls(context=ssl.create_default_context())
            with server:
                if url.username:
                    server.login(unquote(url.username), unquote(url.password or ""))
                server.send_message(msg)
            self.accounts.mark_email(mid)
        except Exception as e:  # recorded for the operator; the request that queued it has already returned
            self.accounts.mark_email(mid, error=f"{type(e).__name__}: {e}")
