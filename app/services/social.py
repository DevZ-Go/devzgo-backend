"""Helpers shared by the social routes (network, messages, connections, collaborations)."""

from typing import Dict, Optional
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.collaboration import ProjectCollaborator, level_can_comment
from app.models.connection import Connection
from app.models.enums import ProjectVisibility
from app.models.notification import Notification
from app.models.profile import UserProfile
from app.models.project import Project
from app.models.project_comment import ProjectComment
from app.models.project_like import ProjectLike
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


def _accepted_sql(viewer_id: UUID, other_column):
    return exists(select(Connection.id).where(
        Connection.status == "accepted",
        or_(
            and_(Connection.requester_id == viewer_id, Connection.receiver_id == other_column),
            and_(Connection.requester_id == other_column, Connection.receiver_id == viewer_id),
        ),
    ))


def audience_clause(user_column, field: str, closed_value: str, viewer_id: Optional[UUID]):
    """People who set this field to `closed_value` are visible to themselves and accepted connections."""
    closed = exists(select(UserProfile.id).where(
        UserProfile.user_id == user_column,
        getattr(UserProfile, field) == closed_value,
    ))
    if viewer_id is None:
        return ~closed
    return or_(~closed, user_column == viewer_id, _accepted_sql(viewer_id, user_column))


def public_project_filter(viewer_id: Optional[UUID]):
    """Public projects whose owner has not limited their profile to connections."""
    return and_(
        Project.visibility == ProjectVisibility.PUBLIC,
        audience_clause(Project.owner_id, "profile_visibility", "connections", viewer_id),
    )


def visible_project_filter(viewer_id: Optional[UUID]):
    """SQL condition: a public project the viewer may see, or one they own or collaborate on."""
    clause = public_project_filter(viewer_id)
    if viewer_id:
        clause = or_(
            clause,
            Project.owner_id == viewer_id,
            Project.collaborators.any(ProjectCollaborator.user_id == viewer_id),
        )
    return clause


def is_connected(db: Session, a: UUID, b: UUID) -> bool:
    if a == b:
        return True
    return db.query(Connection.id).filter(
        Connection.status == "accepted",
        or_(
            and_(Connection.requester_id == a, Connection.receiver_id == b),
            and_(Connection.requester_id == b, Connection.receiver_id == a),
        ),
    ).first() is not None


def profile_open_to(db: Session, owner_id: UUID, viewer_id: Optional[UUID]) -> bool:
    if viewer_id is not None and viewer_id == owner_id:
        return True
    profile = db.query(UserProfile).filter_by(user_id=owner_id).first()
    if profile is None or profile.profile_visibility != "connections":
        return True
    return viewer_id is not None and is_connected(db, viewer_id, owner_id)


def _setting(user: User, field: str, default):
    profile = user.profile
    if profile is None:
        return default
    value = getattr(profile, field)
    return default if value is None else value


def may_message(db: Session, sender_id: UUID, recipient: User) -> bool:
    if _setting(recipient, "message_policy", "anyone") != "connections":
        return True
    return is_connected(db, sender_id, recipient.id) or accepted_recruitment(db, sender_id, recipient.id)


def accepted_recruitment(db: Session, a: UUID, b: UUID) -> bool:
    """A candidate who accepted a recruiter's request can message with that recruiter either way."""
    from app.models.recruiting import RecruitmentRequest

    return db.query(RecruitmentRequest.id).filter(
        RecruitmentRequest.status == "accepted",
        or_(
            and_(RecruitmentRequest.recruiter_id == a, RecruitmentRequest.candidate_id == b),
            and_(RecruitmentRequest.recruiter_id == b, RecruitmentRequest.candidate_id == a),
        ),
    ).first() is not None


def may_comment(db: Session, commenter_id: UUID, author_id: UUID) -> bool:
    if commenter_id == author_id:
        return True
    profile = db.query(UserProfile).filter_by(user_id=author_id).first()
    if profile is None or profile.comment_policy != "connections":
        return True
    return is_connected(db, commenter_id, author_id)


def may_comment_on_project(db: Session, commenter_id: UUID, project: Project) -> bool:
    """Members follow their access level (viewers only read); everyone else follows the owner's comment policy."""
    if commenter_id == project.owner_id:
        return True
    member = db.query(ProjectCollaborator.access).filter_by(project_id=project.id, user_id=commenter_id).first()
    if member:
        return level_can_comment(member[0])
    return may_comment(db, commenter_id, project.owner_id)


def accepts_connection_requests(user: User) -> bool:
    return _setting(user, "allow_connection_requests", True) is not False


def audience_open(db: Session, owner: User, viewer_id: Optional[UUID], field: str, default: str = "public") -> bool:
    """public, connections, or private (and collaboration's none, treated like private)."""
    if viewer_id is not None and viewer_id == owner.id:
        return True
    policy = _setting(owner, field, default)
    if policy in ("private", "none"):
        return False
    if policy == "connections":
        return viewer_id is not None and is_connected(db, viewer_id, owner.id)
    return True


def may_invite(db: Session, actor_id: UUID, target: User) -> bool:
    policy = _setting(target, "collaboration_policy", "anyone")
    if policy == "none":
        return False
    if policy == "connections":
        return is_connected(db, actor_id, target.id)
    return True


NOTIFY_FIELDS = {
    "like": "notify_likes",
    "project_like": "notify_likes",
    "achievement": "notify_likes",
    "comment": "notify_comments",
    "project_comment": "notify_comments",
    "share": "notify_shares",
    "project_share": "notify_shares",
    "connection_request": "notify_connections",
    "connection_accepted": "notify_connections",
    "new_message": "notify_messages",
    "collaboration_invitation": "notify_collaborations",
    "collaboration_request": "notify_collaborations",
    "collaboration_accepted": "notify_collaborations",
    "collaboration_joined": "notify_collaborations",
    "collaboration_left": "notify_collaborations",
    "collaboration_removed": "notify_collaborations",
    "recruitment_request": "notify_messages",
    "recruitment_accepted": "notify_messages",
    "recruitment_declined": "notify_messages",
}


def can_view_project(db: Session, project: Project, viewer_id: Optional[UUID]) -> bool:
    return db.query(Project.id).filter(Project.id == project.id, visible_project_filter(viewer_id)).first() is not None


def project_access(db: Session, project: Project, user_id: Optional[UUID]) -> Dict[str, bool]:
    """What one person may do with a project: the owner may do everything; members get what their access level allows."""
    if user_id is not None and project.owner_id == user_id:
        return dict(owner=True, member=False, view=True, edit=True, files=True, secrets=True, comment=True, manage_team=True)
    m = db.query(ProjectCollaborator).filter_by(project_id=project.id, user_id=user_id).first() if user_id else None
    open_public = project.visibility == ProjectVisibility.PUBLIC and profile_open_to(db, project.owner_id, user_id)
    if m is not None:
        comment = level_can_comment(m.access)
    else:
        comment = bool(user_id and open_public and may_comment(db, user_id, project.owner_id))
    return dict(owner=False, member=m is not None, view=m is not None or open_public,
                edit=bool(m and m.can_edit_details), files=bool(m and m.can_manage_files), secrets=bool(m and m.can_view_secrets),
                comment=comment, manage_team=bool(m and m.access == "manager"))


def project_stats(db: Session, project_ids, viewer_id: Optional[UUID]) -> Dict[UUID, dict]:
    """likes_count, is_liked and comments_count per project, in three grouped queries."""
    ids = list(set(project_ids))
    if not ids:
        return {}

    def counts(model):
        return dict(db.query(model.project_id, func.count()).filter(model.project_id.in_(ids)).group_by(model.project_id).all())

    likes, comments = counts(ProjectLike), counts(ProjectComment)
    liked = {r[0] for r in db.query(ProjectLike.project_id).filter(
        ProjectLike.project_id.in_(ids), ProjectLike.user_id == viewer_id).all()} if viewer_id else set()
    return {i: {"likes_count": likes.get(i, 0), "is_liked": i in liked, "comments_count": comments.get(i, 0)} for i in ids}


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
    field = NOTIFY_FIELDS.get(type_)
    if field:
        profile = db.query(UserProfile).filter_by(user_id=recipient_id).first()
        if profile is not None and getattr(profile, field) is False:
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


LIKE_MILESTONES = (5, 10, 25)


def maybe_like_milestone(db: Session, recipient_id: UUID, actor: User, related_id: str, count: int, noun: str) -> None:
    """Tell the author once when a post or project crosses a like milestone."""
    if count not in LIKE_MILESTONES:
        return
    notify(db, recipient_id, actor, "achievement", f"{related_id}:{count}", f"Your {noun} reached {count} likes.", once=True)
