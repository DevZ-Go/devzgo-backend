"""Transactional email.

SMTP (any provider, e.g. a Gmail app password) is used when SMTP_HOST is set, otherwise the
Resend API when RESEND_API_KEY is set. With neither (local dev) the email is logged instead.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

import httpx

log = logging.getLogger("uvicorn.error")

DEFAULT_FROM = "DevZ-Go <onboarding@resend.dev>"


def _send_smtp(to: str, subject: str, html: str) -> None:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587") or 587)
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    name, addr = parseaddr(os.getenv("SMTP_FROM") or os.getenv("EMAIL_FROM") or user)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((name or "DevZ-Go", addr or user))
    msg["To"] = to
    msg.set_content("Open this email in a client that shows HTML to see the link.")
    msg.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
            if user:
                server.login(user, password)
            server.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls(context=context)
        if user:
            server.login(user, password)
        server.send_message(msg)


def _send_resend(key: str, to: str, subject: str, html: str) -> None:
    sender = os.getenv("EMAIL_FROM", DEFAULT_FROM)
    r = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {key}"},
        json={"from": sender, "to": [to], "subject": subject, "html": html},
        timeout=10,
    )
    if r.status_code == 403 and "resend.dev" in sender:
        log.error(
            "Email send failed (%s): Resend's test sender only delivers to the Resend account owner. "
            "Verify a domain in Resend and set EMAIL_FROM to an address on it, or set SMTP_HOST/SMTP_USER/SMTP_PASSWORD.",
            subject,
        )
        return
    if r.is_error:
        log.error("Email send failed (%s): Resend answered %s: %s", subject, r.status_code, r.text[:300])


def send_email(to: str, subject: str, html: str, dev_note: str = "") -> None:
    try:
        if os.getenv("SMTP_HOST"):
            _send_smtp(to, subject, html)
            return
        key = os.getenv("RESEND_API_KEY")
        if key:
            _send_resend(key, to, subject, html)
            return
    except (httpx.HTTPError, smtplib.SMTPException, OSError) as exc:
        log.error("Email send failed (%s): %s", subject, exc)
        return
    log.warning("No SMTP_HOST or RESEND_API_KEY set; email not sent. %s", dev_note)
