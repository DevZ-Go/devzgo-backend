"""Public email unsubscribe and Resend delivery webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.email import EmailDeliveryEvent, EmailOutbox
from app.models.profile import UserProfile
from app.models.user import User
from app.services.unsubscribe import CATEGORIES, parse_token

log = logging.getLogger("uvicorn.error")
router = APIRouter(tags=["Email"])


def _domain_class(email: str | None) -> str:
    domain = (email or "").rsplit("@", 1)[-1].lower()
    if domain in {"outlook.com", "hotmail.com", "live.com", "msn.com", "office365.com"} or domain.endswith(".onmicrosoft.com"):
        return "microsoft"
    if domain in {"gmail.com", "googlemail.com"}:
        return "google"
    return "other"


@router.get("/email/unsubscribe", response_class=HTMLResponse)
def unsubscribe(token: str = Query(...), db: Session = Depends(get_db)):
    parsed = parse_token(token)
    if not parsed:
        raise HTTPException(status_code=400, detail="This unsubscribe link is invalid or has expired.")
    user_id, category = parsed
    field = CATEGORIES[category]
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).one_or_none()
    if profile is None:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Account not found")
        profile = UserProfile(user_id=user.id)
        db.add(profile)
    setattr(profile, field, False)
    if category == "connection_milestones" and profile.connection_milestone_baseline is None:
        # Baseline set lazily on next accept; nothing else to do.
        pass
    db.commit()
    return HTMLResponse(
        "<!doctype html><html><body style='font-family:system-ui;background:#f8f6f0;padding:2rem'>"
        "<h1 style='color:#721c2a'>Unsubscribed</h1>"
        "<p>You will no longer receive this kind of optional email from DevZ-Go. "
        "Security and account emails may still be sent when needed.</p>"
        "</body></html>"
    )


def _verify_resend_signature(payload: bytes, headers: dict[str, str]) -> bool:
    secret = os.getenv("RESEND_WEBHOOK_SECRET", "")
    if not secret:
        # Without a secret configured, refuse production-looking signatures rather than accepting all.
        return False
    # Resend uses Svix-style signing: whsec_… base; accept simple HMAC of body with secret for flexibility.
    sig = headers.get("svix-signature") or headers.get("resend-signature") or headers.get("x-resend-signature") or ""
    if not sig:
        return False
    # Try raw HMAC hex of body
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    parts = [p.strip() for p in sig.replace("v1,", "").split(" ")]
    for part in parts:
        candidate = part.split(",")[-1] if "," in part else part
        if candidate.startswith("v1="):
            candidate = candidate[3:]
        try:
            if hmac.compare_digest(candidate, digest) or hmac.compare_digest(candidate, digest.encode().hex() if False else digest):
                return True
        except Exception:
            continue
    # Also accept secret-prefixed svix form when secret is the raw key
    try:
        import base64

        if secret.startswith("whsec_"):
            key = base64.b64decode(secret[len("whsec_"):])
            msg_id = headers.get("svix-id", "")
            timestamp = headers.get("svix-timestamp", "")
            to_sign = f"{msg_id}.{timestamp}.{payload.decode()}".encode()
            expected = base64.b64encode(hmac.new(key, to_sign, hashlib.sha256).digest()).decode()
            for part in sig.split(" "):
                if part.startswith("v1=") and hmac.compare_digest(part[3:], expected):
                    return True
    except Exception:
        return False
    return False


@router.post("/webhooks/resend")
async def resend_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not _verify_resend_signature(raw, headers):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        body = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    event_type = str(body.get("type") or body.get("event") or "")
    data = body.get("data") or {}
    provider_event_id = str(
        body.get("id")
        or headers.get("svix-id")
        or data.get("email_id")
        or hashlib.sha256(raw).hexdigest()
    )
    existing = db.query(EmailDeliveryEvent).filter_by(provider_event_id=provider_event_id).one_or_none()
    if existing:
        return {"ok": True, "duplicate": True}

    email_id = str(data.get("email_id") or data.get("id") or "")
    outbox = None
    if email_id:
        outbox = db.query(EmailOutbox).filter(EmailOutbox.provider_message_id == email_id).one_or_none()

    to_list = data.get("to") or []
    to_email = to_list[0] if isinstance(to_list, list) and to_list else data.get("to")
    db.add(
        EmailDeliveryEvent(
            id=uuid4(),
            outbox_id=outbox.id if outbox else None,
            provider_event_id=provider_event_id,
            event_type=event_type or "unknown",
            recipient_domain_class=_domain_class(str(to_email) if to_email else None),
            detail=(event_type)[:200],
        )
    )
    if outbox and event_type in {"email.bounced", "email.complained", "email.suppressed", "email.failed"}:
        outbox.status = "failed"
        outbox.last_error = (event_type)[:500]
    db.commit()
    log.info("resend webhook type=%s domain=%s", event_type, _domain_class(str(to_email) if to_email else None))
    return {"ok": True}
