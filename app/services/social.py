"""Helpers shared by the social routes (network, messages, connections, collaborations)."""

from typing import Optional
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.collaboration import ProjectCollaborator
from app.models.enums import ProjectVisibility
from app.models.notification import Notification
from app.models.project import Project
from app.models.user import User
from app.realtime.broadcaster import queue_event
from app.schemas.network import ConnectionUserSummary


def display_name(user: User) -> str:
    return user.profile.full_name if user.profile and user.profile.full_name else user.username


def user_summary(user: User) -> ConnectionUserSummary:
    p = user.profile
    return ConnectionUserSummary(
        id=user.id,
        username=user.username,
        full_name=display_name(user),
        headline=p.headline if p else None,
        avatar_url=p.avatar_url if p else None,
    )


def visible_project_filter(viewer_id: Optional[UUID]):
    """SQL condition: project is Public, or the viewer owns it or collaborates on it."""
    clause = Project.visibility == ProjectVisibility.PUBLIC
    if viewer_id:
        clause = or_(
            clause,
            Project.owner_id == viewer_id,
            Project.collaborators.any(ProjectCollaborator.user_id == viewer_id),
        )
    return clause


def can_view_project(db: Session, project: Project, viewer_id: Optional[UUID]) -> bool:
    return db.query(Project.id).filter(Project.id == project.id, visible_project_filter(viewer_id)).first() is not None


def notify(
    db: Session,
    recipient_id: UUID,
    actor: User,
    type_: str,
    related_id: Optional[str],
    message: str,
    once: bool = False,
) -> Optional[Notification]:
    """Queue a notification (committed by the caller). `once` skips it if the same one already exists."""
    if recipient_id == actor.id:
        return None
    if once and db.query(Notification.id).filter_by(
        recipient_id=recipient_id, actor_id=actor.id, type=type_, related_id=related_id
    ).first():
        return None
    notif = Notification(
        recipient_id=recipient_id, actor_id=actor.id, type=type_, related_id=related_id, message=message[:255]
    )
    db.add(notif)
    queue_event(db, recipient_id, {"type": "notification.new", "notification_type": type_, "message": notif.message})
    return notif
