"""Signed unsubscribe tokens for optional email categories."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode
from uuid import UUID

from app.core.public_urls import backend_base

CATEGORIES = {
    "connection_requests": "email_notify_connection_requests",
    "connection_milestones": "email_notify_connection_milestones",
    "messages_digest": "email_notify_messages_digest",
    "collaborations": "email_notify_collaborations",
    "project_activity": "email_notify_project_activity",
    "recruiter": "email_notify_recruiter",
    "weekly_summary": "email_notify_weekly_summary",
}


def _secret() -> bytes:
    return (os.getenv("EMAIL_UNSUBSCRIBE_SECRET") or os.getenv("SECRET_KEY") or "dev").encode()


def make_token(user_id: UUID, category: str, *, ttl_seconds: int = 60 * 60 * 24 * 90) -> str:
    exp = int(time.time()) + ttl_seconds
    body = f"{user_id}:{category}:{exp}"
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}:{sig}"


def parse_token(token: str) -> tuple[UUID, str] | None:
    try:
        user_s, category, exp_s, sig = token.rsplit(":", 3)
        body = f"{user_s}:{category}:{exp_s}"
        expect = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            return None
        if int(exp_s) < int(time.time()):
            return None
        if category not in CATEGORIES:
            return None
        return UUID(user_s), category
    except (ValueError, TypeError):
        return None


def unsubscribe_url(user_id: UUID, category: str) -> str:
    token = make_token(user_id, category)
    return f"{backend_base()}/email/unsubscribe?{urlencode({'token': token})}"
