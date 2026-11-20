"""Optional digest emails. Prefs gate every send; worker may call enqueue helpers on a schedule."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.profile import UserProfile
from app.models.user import User
from app.services import email_service
from app.services.unsubscribe import unsubscribe_url


def enqueue_weekly_summary(db: Session, user: User, *, summary_lines: list[str]) -> None:
    profile = user.profile
    if profile is not None and not profile.email_notify_weekly_summary:
        return
    if not user.email:
        return
    today = date.today().isoformat()
    email_service.enqueue(
        db,
        event_type="weekly_summary",
        template="weekly_summary",
        to_email=user.email,
        context={
            "username": user.username,
            "summary": "\n".join(summary_lines),
            "unsubscribe_url": unsubscribe_url(user.id, "weekly_summary"),
        },
        idempotency_key=f"weekly_summary:{user.id}:{today}",
        user_id=user.id,
    )


def enqueue_messages_digest(db: Session, user: User, *, count: int) -> None:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one_or_none()
    if profile is not None and not profile.email_notify_messages_digest:
        return
    if not user.email or count <= 0:
        return
    today = date.today().isoformat()
    email_service.enqueue(
        db,
        event_type="messages_digest",
        template="unread_messages_digest",
        to_email=user.email,
        context={
            "username": user.username,
            "unread_count": count,
            "unsubscribe_url": unsubscribe_url(user.id, "messages_digest"),
        },
        idempotency_key=f"messages_digest:{user.id}:{today}",
        user_id=user.id,
    )
