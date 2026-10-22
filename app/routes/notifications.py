from fastapi import APIRouter, Depends, HTTPException, Query, status
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, selectinload
from sqlalchemy import desc
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.notification import Notification
from app.core.dependencies import get_current_user
from app.schemas.network import NotificationResponse
from app.services.social import display_name

router = APIRouter(prefix="/notifications", tags=["Notifications"])
RECENT = timedelta(hours=48)


def _recent():
    return Notification.created_at >= datetime.now(timezone.utc) - RECENT

def _format_notification(n: Notification) -> NotificationResponse:
    actor_name = display_name(n.actor)
    actor_avatar = n.actor.profile.avatar_url if n.actor.profile else None

    return NotificationResponse(
        id=n.id,
        recipient_id=n.recipient_id,
        actor_id=n.actor_id,
        actor_name=actor_name,
        actor_username=n.actor.username,
        actor_avatar=actor_avatar,
        type=n.type,
        related_id=n.related_id,
        message=n.message,
        read=n.read,
        created_at=n.created_at,
    )

@router.get("", response_model=List[NotificationResponse])
def get_user_notifications(
    before: Optional[datetime] = Query(None, description="Return notifications older than this (pagination)"),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Notification).options(selectinload(Notification.actor).selectinload(User.profile)).filter(
        Notification.recipient_id == current_user.id,
        _recent(),
    )
    if before:
        query = query.filter(Notification.created_at < before)
    notifs = query.order_by(desc(Notification.created_at)).limit(limit).all()

    return [_format_notification(n) for n in notifs]

@router.put("/{notification_id}/read")
def mark_notification_read(
    notification_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notif = db.query(Notification).filter(Notification.id == notification_id).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    if notif.recipient_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    notif.read = True
    db.commit()
    return {"message": "Notification marked as read"}

@router.put("/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.query(Notification).filter(
        Notification.recipient_id == current_user.id,
        Notification.read == False,
    ).update({"read": True})
    db.commit()
    return {"message": "All notifications marked as read"}

@router.get("/unread-count")
def get_unread_notifications_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = db.query(Notification).filter(
        Notification.recipient_id == current_user.id,
        Notification.read == False,
        _recent(),
    ).count()
    return {"unread_count": count}
