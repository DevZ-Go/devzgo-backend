"""Low-level transactional email transport (Resend or SMTP).

Prefer EmailService.enqueue + the email worker for durable delivery.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Any

import httpx

log = logging.getLogger("uvicorn.error")

DEFAULT_FROM = "DevZ-Go <onboarding@resend.dev>"


def _send_smtp(to: str, subject: str, html: str, text: str | None = None) -> str | None:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587") or 587)
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    name, addr = parseaddr(os.getenv("SMTP_FROM") or os.getenv("EMAIL_FROM") or user)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((name or "DevZ-Go", addr or user))
    msg["To"] = to
    msg.set_content(text or "Open this email in a client that shows HTML.")
    msg.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
            if user:
                server.login(user, password)
            server.send_message(msg)
        return None
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls(context=context)
        if user:
            server.login(user, password)
        server.send_message(msg)
    return None


def _send_resend(
    key: str,
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
    idempotency_key: str | None = None,
) -> str | None:
    sender = os.getenv("EMAIL_FROM", DEFAULT_FROM)
    headers: dict[str, str] = {"Authorization": f"Bearer {key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:256]
    body: dict[str, Any] = {"from": sender, "to": [to], "subject": subject, "html": html}
    if text:
        body["text"] = text
    r = httpx.post(
        "https://api.resend.com/emails",
        headers=headers,
        json=body,
        timeout=10,
    )
    if r.status_code == 403 and "resend.dev" in sender:
        log.error(
            "Email send failed (%s): Resend's test sender only delivers to the Resend account owner. "
            "Verify a domain in Resend and set EMAIL_FROM to an address on it, or set SMTP_HOST/SMTP_USER/SMTP_PASSWORD.",
            subject,
        )
        raise RuntimeError("resend_test_sender_restricted")
    if r.is_error:
        log.error("Email send failed (%s): Resend answered %s: %s", subject, r.status_code, r.text[:300])
        raise RuntimeError(f"resend_{r.status_code}")
    try:
        return str((r.json() or {}).get("id") or "") or None
    except Exception:
        return None


def send_email(
    to: str,
    subject: str,
    html: str,
    dev_note: str = "",
    text: str | None = None,
    idempotency_key: str | None = None,
) -> str | None:
    """Send immediately. Returns provider message id when Resend supplies one."""
    try:
        if os.getenv("SMTP_HOST"):
            return _send_smtp(to, subject, html, text=text)
        key = os.getenv("RESEND_API_KEY")
        if key:
            return _send_resend(key, to, subject, html, text=text, idempotency_key=idempotency_key)
    except (httpx.HTTPError, smtplib.SMTPException, OSError, RuntimeError) as exc:
        log.error("Email send failed (%s): %s", subject, exc)
        raise
    log.warning("No SMTP_HOST or RESEND_API_KEY set; email not sent. %s", dev_note)
    return None
