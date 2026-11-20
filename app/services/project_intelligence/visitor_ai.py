"""Community AI summaries for viewable projects — generate once, serve to everyone."""

from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.admin import PlatformAdmin
from app.models.project import Project
from app.models.project_intelligence import PiVisitorAiRequest, ProjectAiPublication
from app.models.user import User
from app.services.platform_admin import is_owner_email
from app.services.project_intelligence.quota import get_pi_settings


class VisitorAiError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, status: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.status = status or {}


def is_platform_admin(db: Session, user: User) -> bool:
    if is_owner_email(user.email):
        return True
    return (
        db.query(PlatformAdmin.user_id).filter(PlatformAdmin.user_id == user.id).first() is not None
    )


def community_summary_ready(db: Session, project_id: UUID) -> bool:
    """True when a valid AI brief already exists (low-quality legacy rows do not count)."""
    from app.models.project import Project
    from app.services.project_intelligence.quality import QUALITY_VALID, is_valid_ai_explanation

    pub = db.query(ProjectAiPublication).filter(ProjectAiPublication.project_id == project_id).first()
    if not pub:
        return False
    simple = pub.simple_explanation or {}
    if getattr(pub, "quality_status", None) == QUALITY_VALID and (simple.get("overview") or simple.get("headline")):
        return True
    project = db.query(Project).filter(Project.id == project_id).first()
    title = project.title if project else ""
    return is_valid_ai_explanation(simple, title=title or "")


def visitor_quota_status(db: Session, user: User) -> dict:
    settings = get_pi_settings(db)
    admin = is_platform_admin(db, user)
    limit = int(settings.get("visitor_ai_project_limit", 5))
    # Distinct projects where this user triggered a real (non-cache) generation
    used = int(
        db.query(func.count(func.distinct(PiVisitorAiRequest.project_id)))
        .filter(
            PiVisitorAiRequest.user_id == user.id,
            PiVisitorAiRequest.was_cache_hit.is_(False),
        )
        .scalar()
        or 0
    )
    return {
        "is_admin": admin,
        "visitor_ai_enabled": bool(settings.get("visitor_ai_enabled", True)),
        "used": 0 if admin else used,
        "limit": None if admin else limit,
        "remaining": None if admin else max(0, limit - used),
        "can_request_new": True if admin else (used < limit),
    }


def prior_visitor_request(db: Session, user_id: UUID, project_id: UUID) -> PiVisitorAiRequest | None:
    return (
        db.query(PiVisitorAiRequest)
        .filter(PiVisitorAiRequest.user_id == user_id, PiVisitorAiRequest.project_id == project_id)
        .first()
    )


def record_visitor_request(
    db: Session,
    *,
    user_id: UUID,
    project_id: UUID,
    was_cache_hit: bool,
    generation_record_id: UUID | None = None,
) -> PiVisitorAiRequest:
    existing = (
        db.query(PiVisitorAiRequest)
        .filter(PiVisitorAiRequest.user_id == user_id, PiVisitorAiRequest.project_id == project_id)
        .first()
    )
    if existing:
        return existing
    row = PiVisitorAiRequest(
        id=uuid.uuid4(),
        user_id=user_id,
        project_id=project_id,
        was_cache_hit=was_cache_hit,
        generation_record_id=generation_record_id,
    )
    db.add(row)
    db.flush()
    return row


def assert_can_visitor_generate(db: Session, user: User, project: Project) -> dict:
    settings = get_pi_settings(db)
    if not settings.get("ai_generate_enabled", True):
        raise VisitorAiError("AI generation is temporarily disabled.", status_code=403)
    if not settings.get("visitor_ai_enabled", True) and not is_platform_admin(db, user):
        raise VisitorAiError("Community AI summaries are disabled.", status_code=403)

    status = visitor_quota_status(db, user)
    # Already have a shared summary — anyone who can view may refresh/cache freely
    if community_summary_ready(db, project.id):
        return {**status, "cache_available": True}

    prior = prior_visitor_request(db, user.id, project.id)
    # Retry on the same project does not consume an extra slot
    if prior or status["can_request_new"]:
        return {**status, "cache_available": False}

    raise VisitorAiError(
        f"You've used your {status['limit']} community AI project summaries. "
        "You can still read summaries that other people already generated.",
        status_code=402,
        status=status,
    )
