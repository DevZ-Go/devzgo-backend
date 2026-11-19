"""Optional email side-effects for connection requests and milestones."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.connection import Connection
from app.models.profile import UserProfile
from app.models.user import User
from app.services import email_service
from app.services.social import display_name
from app.services.unsubscribe import unsubscribe_url


def _profile(db: Session, user_id: UUID) -> UserProfile | None:
    return db.query(UserProfile).filter(UserProfile.user_id == user_id).one_or_none()


def accepted_count(db: Session, user_id: UUID) -> int:
    return (
        db.query(Connection.id)
        .filter(
            or_(Connection.requester_id == user_id, Connection.receiver_id == user_id),
            Connection.status == "accepted",
        )
        .count()
    )


def enqueue_connection_request(db: Session, *, requester: User, receiver: User) -> None:
    profile = _profile(db, receiver.id)
    if profile is not None and not profile.email_notify_connection_requests:
        return
    email_service.enqueue(
        db,
        event_type="connection_request",
        template="connection_request",
        to_email=receiver.email,
        context={
            "username": receiver.username,
            "from_name": display_name(requester),
            "unsubscribe_url": unsubscribe_url(receiver.id, "connection_requests"),
        },
        idempotency_key=f"connection_request:{requester.id}:{receiver.id}",
        user_id=receiver.id,
    )


def maybe_milestone(db: Session, user: User) -> None:
    profile = _profile(db, user.id)
    if profile is None:
        return
    if not profile.email_notify_connection_milestones:
        return
    count = accepted_count(db, user.id)
    if profile.connection_milestone_baseline is None:
        profile.connection_milestone_baseline = count
        return
    floor = profile.connection_milestone_last
    if floor is None:
        floor = profile.connection_milestone_baseline
    next_level = ((floor // 5) + 1) * 5
    while next_level <= count:
        email_service.enqueue(
            db,
            event_type="connection_milestone",
            template="connection_milestone",
            to_email=user.email,
            context={
                "username": user.username,
                "count": next_level,
                "unsubscribe_url": unsubscribe_url(user.id, "connection_milestones"),
            },
            idempotency_key=f"connection_milestone:{user.id}:{next_level}",
            user_id=user.id,
        )
        profile.connection_milestone_last = next_level
        next_level += 5
