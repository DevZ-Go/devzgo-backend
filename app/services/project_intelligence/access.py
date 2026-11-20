"""Audience checks for Project Intelligence insights and claims."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.user import User
from app.services.project_intelligence.preferences import get_project_settings
from app.services.social import is_connected, project_access


def viewer_is_recruiter(viewer: Optional[User]) -> bool:
    return bool(viewer and getattr(viewer, "is_recruiter", False))


def viewer_is_platform_admin(db: Session, viewer: Optional[User]) -> bool:
    if not viewer:
        return False
    from app.services.project_intelligence.visitor_ai import is_platform_admin

    return is_platform_admin(db, viewer)


def can_see_insights(db: Session, project: Project, viewer_id: Optional[UUID], viewer: Optional[User] = None) -> tuple[bool, bool]:
    """Return (allowed, is_team). Team (and platform admins) see unredacted technical paths."""
    access = project_access(db, project, viewer_id) if viewer_id else {"owner": False, "files": False, "member": False}
    is_team = bool(access.get("owner") or access.get("files") or access.get("member"))
    if not is_team and viewer_is_platform_admin(db, viewer):
        is_team = True
    if is_team:
        return True, True

    settings = get_project_settings(db, project.id)
    audience = settings.insight_audience or "public"

    if audience == "private":
        return False, False
    if not can_view_project_basic(db, project, viewer_id):
        return False, False
    if audience == "public":
        return True, False
    if audience == "connections":
        return bool(viewer_id and is_connected(db, project.owner_id, viewer_id)), False
    if audience == "recruiters":
        return viewer_is_recruiter(viewer) or is_team, False
    return False, False


def can_view_project_basic(db: Session, project: Project, viewer_id: Optional[UUID]) -> bool:
    from app.services.social import can_view_project
    return can_view_project(db, project, viewer_id)


def redact_technical(technical: dict) -> dict:
    """Strip file paths and route file references for non-team viewers."""
    tech = dict(technical or {})
    for key in ("api_routes", "ui_pages", "data_models"):
        items = tech.get(key) or []
        cleaned = []
        for item in items:
            if not isinstance(item, dict):
                continue
            row = {k: v for k, v in item.items() if k != "file"}
            cleaned.append(row)
        tech[key] = cleaned
    return tech
