"""Monthly Wrap: 11 metrics counted from real rows, a transparent persona rule, and month boundaries in the user's timezone."""

from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models.associations import project_tech
from app.models.collaboration import ProjectCollaborator, ProjectInvitation
from app.models.connection import Connection
from app.models.network import USER_POST_TYPES, ActivityComment, ActivityLike, NetworkActivity
from app.models.profile import DEFAULT_TIMEZONE
from app.models.project import Project
from app.models.project_comment import ProjectComment
from app.models.project_like import ProjectLike
from app.models.techstack import TechStack
from app.models.user import User
from app.services.social import visible_project_filter

SCHEMA_VERSION = 1
HEADLINE = ("projects_created", "connections_made", "posts_published")
PERSONAS = (  # first match wins; the matched rule is shown to the user
    ("The Builder", "Created 2 or more projects", lambda m: m["projects_created"] >= 2),
    ("The Team Player", "Joined a project or welcomed a new member", lambda m: m["projects_joined"] + m["members_joined_my_projects"] >= 1),
    ("The Connector", "Made 3 or more new connections", lambda m: m["connections_made"] >= 3),
    ("The Voice", "Published 3 or more posts", lambda m: m["posts_published"] >= 3),
    ("The Explorer", "Was active this month", lambda m: not is_empty(m)),
)


def user_timezone(user: User) -> str:
    tz = user.profile.timezone if user.profile and user.profile.timezone else DEFAULT_TIMEZONE
    try:
        ZoneInfo(tz)
        return tz
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_TIMEZONE


def current_month(tz: str, now: Optional[datetime] = None) -> date:
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz))
    return date(local.year, local.month, 1)


def shift_month(month: date, by: int) -> date:
    index = month.year * 12 + month.month - 1 + by
    return date(index // 12, index % 12 + 1, 1)


def month_bounds(month: date, tz: str) -> tuple[datetime, datetime]:
    """[start, end) of the month in the user's zone, as UTC instants."""
    zone = ZoneInfo(tz)
    start, end = (datetime(m.year, m.month, 1, tzinfo=zone).astimezone(timezone.utc) for m in (month, shift_month(month, 1)))
    return start, end


def is_empty(metrics: dict) -> bool:
    return not any(metrics.values())


def persona(metrics: dict) -> Optional[dict]:
    return next(({"label": label, "rule": rule} for label, rule, test in PERSONAS if test(metrics)), None)


def compute_metrics(db: Session, user_id, start: datetime, end: datetime) -> dict:
    def within(column):
        return and_(column >= start, column < end)

    def count(*criteria, model, joins=()):
        q = db.query(func.count(model.id))
        for target, on in joins:
            q = q.join(target, on)
        return q.filter(*criteria).scalar() or 0

    mine = (Project, Project.id == ProjectLike.project_id)
    top_tech = (db.query(TechStack.name).join(project_tech, project_tech.c.tech_id == TechStack.id)
                .join(Project, Project.id == project_tech.c.project_id)
                .filter(Project.owner_id == user_id, within(Project.created_at))
                .group_by(TechStack.name).order_by(func.count().desc(), TechStack.name).limit(3).all())
    category = (db.query(Project.category).join(ProjectLike, ProjectLike.project_id == Project.id)
                .filter(ProjectLike.user_id == user_id, within(ProjectLike.created_at), visible_project_filter(user_id))
                .group_by(Project.category).order_by(func.count().desc(), Project.category).first())
    my_activity = (NetworkActivity, NetworkActivity.id == ActivityLike.activity_id)

    return {
        "projects_created": count(Project.owner_id == user_id, within(Project.created_at), model=Project),
        "top_tech": [name for (name,) in top_tech],
        "project_likes_received": count(Project.owner_id == user_id, ProjectLike.user_id != user_id,
                                        within(ProjectLike.created_at), model=ProjectLike, joins=[mine]),
        "project_comments_received": count(Project.owner_id == user_id, ProjectComment.author_id != user_id,
                                           within(ProjectComment.created_at), model=ProjectComment,
                                           joins=[(Project, Project.id == ProjectComment.project_id)]),
        "posts_published": count(NetworkActivity.user_id == user_id, NetworkActivity.activity_type.in_(USER_POST_TYPES),
                                 within(NetworkActivity.created_at), model=NetworkActivity),
        "post_likes_received": count(NetworkActivity.user_id == user_id, ActivityLike.user_id != user_id,
                                     within(ActivityLike.created_at), model=ActivityLike, joins=[my_activity]),
        "post_comments_received": count(NetworkActivity.user_id == user_id, ActivityComment.author_id != user_id,
                                        within(ActivityComment.created_at), model=ActivityComment,
                                        joins=[(NetworkActivity, NetworkActivity.id == ActivityComment.activity_id)]),
        "connections_made": count(or_(Connection.requester_id == user_id, Connection.receiver_id == user_id),
                                  Connection.status == "accepted", within(Connection.accepted_at), model=Connection),
        "projects_joined": count(ProjectCollaborator.user_id == user_id, within(ProjectCollaborator.created_at),
                                 model=ProjectCollaborator),
        "members_joined_my_projects": count(Project.owner_id == user_id, within(ProjectCollaborator.created_at),
                                            model=ProjectCollaborator,
                                            joins=[(Project, Project.id == ProjectCollaborator.project_id)]),
        "invitations_sent": count(ProjectInvitation.inviter_id == user_id, within(ProjectInvitation.created_at),
                                  model=ProjectInvitation),
        "top_liked_category": (category[0].value if hasattr(category[0], "value") else category[0]) if category else None,
    }
