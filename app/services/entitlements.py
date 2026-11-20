"""Premium entitlement checks. Never affects match/badge/search ranking."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.entitlements import Entitlement
from app.models.user import User

PREMIUM_FEATURES = ("resume_studio", "project_intelligence")


def _dev_grant_ids() -> set[str]:
    raw = os.getenv("RESUME_STUDIO_DEV_GRANTS") or ""
    return {part.strip() for part in raw.split(",") if part.strip()}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _is_platform_admin(db: Session, user_id: UUID) -> bool:
    """Platform admins and the reserved owner email get every premium feature."""
    from app.models.admin import PlatformAdmin
    from app.services.platform_admin import is_owner_email

    if db.query(PlatformAdmin.user_id).filter(PlatformAdmin.user_id == user_id).first() is not None:
        return True
    user = db.query(User).filter(User.id == user_id).first()
    return bool(user and is_owner_email(user.email))


def has_feature(db: Session, user_id: UUID, feature: str = "resume_studio") -> bool:
    if str(user_id) in _dev_grant_ids():
        return True
    if feature in PREMIUM_FEATURES and _is_platform_admin(db, user_id):
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


def grant_premium_bundle(
    db: Session,
    user_id: UUID,
    *,
    source: str = "admin_grant",
    granted_by: UUID | None = None,
    days: int | None = None,
    features: tuple[str, ...] = PREMIUM_FEATURES,
) -> list[Entitlement]:
    """Grant every listed premium feature (Resume Studio + Project Intelligence by default)."""
    return [
        grant(db, user_id, feature, source=source, granted_by=granted_by, days=days)
        for feature in features
    ]


def revoke_premium_bundle(
    db: Session,
    user_id: UUID,
    *,
    features: tuple[str, ...] = PREMIUM_FEATURES,
) -> int:
    removed = 0
    for feature in features:
        if revoke(db, user_id, feature):
            removed += 1
    return removed


def grant_all_developers(
    db: Session,
    *,
    feature: str | None = None,
    source: str = "admin_grant",
    granted_by: UUID | None = None,
    days: int | None = None,
) -> int:
    """Grant premium to every non-recruiter account. Returns how many user bundles were upserted."""
    users = db.query(User).filter(User.account_type != "recruiter").all()
    features = (feature,) if feature else PREMIUM_FEATURES
    count = 0
    for user in users:
        grant_premium_bundle(
            db, user.id, source=source, granted_by=granted_by, days=days, features=features
        )
        count += 1
    return count


def list_for_user(db: Session, user_id: UUID) -> list[dict]:
    features = []
    admin = _is_platform_admin(db, user_id)
    for feature in PREMIUM_FEATURES:
        row = db.query(Entitlement).filter(Entitlement.user_id == user_id, Entitlement.feature == feature).first()
        active = has_feature(db, user_id, feature)
        source = None
        if row:
            source = row.source
        elif admin and active:
            source = "platform_admin"
        elif str(user_id) in _dev_grant_ids() and active:
            source = "dev_grant"
        features.append({
            "feature": feature,
            "active": active,
            "expires_at": row.expires_at.isoformat() if row and row.expires_at else None,
            "source": source,
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
