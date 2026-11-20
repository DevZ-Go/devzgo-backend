"""Pause an account until the next sign-in, or schedule permanent deletion with a 30-day cancel window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User

DELETE_GRACE = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_hidden(user: User) -> bool:
    return user.deactivated_at is not None or user.delete_after is not None


def purge_due_accounts(db: Session) -> int:
    """Permanently remove accounts whose delete_after has passed. Returns how many were removed."""
    from app.services import email_service

    due = db.query(User).filter(User.delete_after.isnot(None), User.delete_after <= _now()).all()
    for user in due:
        email_service.enqueue(
            db,
            event_type="account_deleted",
            template="account_deleted",
            to_email=user.email,
            context={"username": user.username},
            idempotency_key=f"account_deleted:{user.id}",
            user_id=None,
        )
        db.delete(user)
    if due:
        db.commit()
    return len(due)


def purge_if_due(db: Session, user: User) -> None:
    """If this account's grace window ended, delete it and raise 401."""
    if user.delete_after is not None and user.delete_after <= _now():
        db.delete(user)
        db.commit()
        raise HTTPException(status_code=401, detail="This account was permanently deleted.")


def reactivate_on_login(db: Session, user: User) -> User:
    """Cancel a pending deletion and clear a pause. Call only after credentials are verified."""
    purge_if_due(db, user)
    if user.suspended_at is not None:
        raise HTTPException(
            status_code=403,
            detail="This account is suspended. A DevZ-Go admin has to restore it before you can sign in.",
        )
    if user.deactivated_at is None and user.delete_after is None:
        return user
    was_paused = user.deactivated_at is not None and user.delete_after is None
    user.deactivated_at = None
    user.delete_after = None
    if was_paused:
        from app.services import email_service

        email_service.enqueue(
            db,
            event_type="account_restored",
            template="account_restored",
            to_email=user.email,
            context={"username": user.username},
            idempotency_key=f"account_restored:{user.id}:{_now().date().isoformat()}",
            user_id=user.id,
        )
    db.commit()
    db.refresh(user)
    return user


def deactivate(db: Session, user: User) -> User:
    """Hide the account until the next successful sign-in. Signs out every session."""
    from app.services import email_service

    user.deactivated_at = _now()
    user.delete_after = None
    user.token_version = (user.token_version or 0) + 1
    email_service.enqueue(
        db,
        event_type="account_paused",
        template="account_paused",
        to_email=user.email,
        context={"username": user.username},
        idempotency_key=f"account_paused:{user.id}:{user.token_version}",
        user_id=user.id,
    )
    db.commit()
    db.refresh(user)
    return user


def schedule_delete(db: Session, user: User) -> User:
    """Hide the account and schedule permanent deletion in 30 days. Signs out every session."""
    from app.services import email_service

    now = _now()
    user.deactivated_at = now
    user.delete_after = now + DELETE_GRACE
    user.token_version = (user.token_version or 0) + 1
    email_service.enqueue(
        db,
        event_type="account_deletion_requested",
        template="account_deletion_requested",
        to_email=user.email,
        context={"username": user.username, "delete_after": user.delete_after.isoformat()},
        idempotency_key=f"account_deletion_requested:{user.id}:{user.delete_after.isoformat()}",
        user_id=user.id,
    )
    db.commit()
    db.refresh(user)
    return user


def cancel_delete(db: Session, user: User) -> User:
    """Clear a pending deletion while staying signed in. Does not un-pause; sign in again to resume."""
    if user.delete_after is None:
        raise HTTPException(status_code=400, detail="No deletion is scheduled for this account.")
    user.delete_after = None
    db.commit()
    db.refresh(user)
    return user


def status_for(user: User) -> dict:
    return {
        "deactivated": user.deactivated_at is not None,
        "delete_after": user.delete_after,
        "deactivated_at": user.deactivated_at,
    }


def require_active_recipient(user: User) -> None:
    if is_hidden(user):
        raise HTTPException(status_code=404, detail="Recipient developer not found")
