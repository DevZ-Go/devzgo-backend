"""Transactional email through the Resend API. Without RESEND_API_KEY (local dev) the email is logged instead."""

import logging
import os

import httpx

log = logging.getLogger("uvicorn.error")


def send_email(to: str, subject: str, html: str, dev_note: str = "") -> None:
    key = os.getenv("RESEND_API_KEY")
    if not key:
        log.warning("RESEND_API_KEY not set; email to %s not sent. %s", to, dev_note)
        return
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={"from": os.getenv("EMAIL_FROM", "DevZ-Go <onboarding@resend.dev>"), "to": [to], "subject": subject, "html": html},
            timeout=10,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("Email send failed (%s): %s", subject, exc)
