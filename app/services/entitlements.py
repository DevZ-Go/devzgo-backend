"""Premium entitlement checks. Resume Studio only; never affects match/badge/search ranking."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.entitlements import Entitlement
from app.models.user import User


def _dev_grant_ids() -> set[str]:
    raw = os.getenv("RESUME_STUDIO_DEV_GRANTS") or ""
    return {part.strip() for part in raw.split(",") if part.strip()}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def has_feature(db: Session, user_id: UUID, feature: str = "resume_studio") -> bool:
    if str(user_id) in _dev_grant_ids():
        return True
    row = db.query(Entitlement).filter(Entitlement.user_id == user_id, Entitlement.feature == feature).first()
    if not row:
        return False
    expires = _aware(row.expires_at)
    if expires and expires < datetime.now(timezone.utc):
        return False
    return True


def grant(
    db: Session,
    user_id: UUID,
    feature: str,
    *,
    source: str = "dev_grant",
    granted_by: UUID | None = None,
    expires_at: datetime | None = None,
    days: int | None = None,
) -> Entitlement:
    if days is not None:
        if days < 1:
            raise ValueError("days must be at least 1")
        expires_at = datetime.now(timezone.utc) + timedelta(days=int(days))
    row = db.query(Entitlement).filter(Entitlement.user_id == user_id, Entitlement.feature == feature).first()
    if row:
        row.source = source
        row.granted_by = granted_by
        row.expires_at = expires_at
        return row
    row = Entitlement(
        user_id=user_id, feature=feature, source=source, granted_by=granted_by, expires_at=expires_at,
    )
    db.add(row)
    return row


def revoke(db: Session, user_id: UUID, feature: str = "resume_studio") -> bool:
    row = db.query(Entitlement).filter(Entitlement.user_id == user_id, Entitlement.feature == feature).first()
    if not row:
        return False
    db.delete(row)
    return True


def grant_all_developers(
    db: Session,
    *,
    feature: str = "resume_studio",
    source: str = "admin_grant",
    granted_by: UUID | None = None,
    days: int | None = None,
) -> int:
    """Grant premium to every non-recruiter account. Returns how many rows were upserted."""
    users = db.query(User).filter(User.is_recruiter.is_(False)).all()
    count = 0
    for user in users:
        grant(db, user.id, feature, source=source, granted_by=granted_by, days=days)
        count += 1
    return count


def list_for_user(db: Session, user_id: UUID) -> list[dict]:
    features = []
    for feature in ("resume_studio", "project_intelligence"):
        row = db.query(Entitlement).filter(Entitlement.user_id == user_id, Entitlement.feature == feature).first()
        active = has_feature(db, user_id, feature)
        features.append({
            "feature": feature,
            "active": active,
            "expires_at": row.expires_at.isoformat() if row and row.expires_at else None,
            "source": row.source if row else ("dev_grant" if str(user_id) in _dev_grant_ids() and active else None),
        })
    return features


def notify_expired_premium(db: Session, *, limit: int = 50) -> int:
    """Enqueue one email per expired entitlement (idempotent)."""
    from app.services import email_service

    now = datetime.now(timezone.utc)
    rows = (
        db.query(Entitlement, User)
        .join(User, User.id == Entitlement.user_id)
        .filter(
            Entitlement.feature == "resume_studio",
            Entitlement.expires_at.isnot(None),
            Entitlement.expires_at <= now,
        )
        .limit(limit)
        .all()
    )
    sent = 0
    for entitlement, user in rows:
        if not user.email:
            continue
        expires = _aware(entitlement.expires_at)
        key = f"premium_expired:{user.id}:{expires.isoformat() if expires else 'none'}"
        outbox = email_service.enqueue(
            db,
            event_type="premium_expired",
            template="premium_expired",
            to_email=user.email,
            context={"username": user.username},
            idempotency_key=key,
        )
        if outbox:
            email_service.try_send_now(db, outbox)
            sent += 1
    return sent
