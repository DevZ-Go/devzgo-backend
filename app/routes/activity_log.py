"""
Activity logs built from repositories imported from GitHub.

GET /profiles/{user_id}/activity-log          a person's commits and pushes, or their whole team's (scope=team)
GET /projects/{project_id}/github/activity-log everyone's commits and pushes on one project
"""

from datetime import date
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user_optional
from app.db.session import get_db
from app.models.project import Project
from app.models.user import User
from app.services import activity_log as log
from app.services.social import can_view_project, profile_open_to, user_summary

router = APIRouter(tags=["Activity log"])

Kind = Literal["all", "commits", "pushes"]


def _query(kind: str, q: str, project: Optional[UUID], offset: int, limit: int, tz: str, day: Optional[date] = None) -> log.LogQuery:
    return log.LogQuery(kind=kind, q=q[:100], project_id=project, day=day, offset=offset, limit=limit, tz=tz)


@router.get("/profiles/{user_id}/activity-log")
def person_log(
    user_id: UUID,
    scope: Literal["mine", "team"] = "mine",
    kind: Kind = "all",
    q: str = "",
    project: Optional[UUID] = None,
    day: Optional[date] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    tz: str = "UTC",
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    user = db.query(User).options(selectinload(User.profile)).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Developer not found")
    viewer_id = viewer.id if viewer else None
    subject = {"kind": "person", "user": user_summary(user).model_dump(mode="json"), "scope": scope}
    if not profile_open_to(db, user.id, viewer_id):
        return {**log.build(db, viewer_id, Project.id.is_(None), None, None, _query(kind, q, None, 0, 1, tz)),
                "subject": subject, "restricted": True}
    if scope == "team":
        body = log.build(db, viewer_id, log.team_projects(user.id), None, None, _query(kind, q, project, offset, limit, tz, day))
    else:
        body = log.build(db, viewer_id, Project.id.isnot(None), log.commits_by(user), log.pushes_by(user),
                         _query(kind, q, project, offset, limit, tz, day))
    return {**body, "subject": subject, "restricted": False}


@router.get("/projects/{project_id}/github/activity-log")
def project_log(
    project_id: UUID,
    kind: Kind = "all",
    q: str = "",
    day: Optional[date] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    tz: str = "UTC",
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    viewer_id = viewer.id if viewer else None
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or not can_view_project(db, project, viewer_id):
        raise HTTPException(status_code=404, detail="Project not found")
    body = log.build(db, viewer_id, Project.id == project.id, None, None, _query(kind, q, None, offset, limit, tz, day))
    subject = {
        "kind": "project",
        "project": {"id": project.id, "title": project.title, "github_url": log.repo_url(project),
                    "synced_at": project.github_synced_at},
        "user": user_summary(project.owner).model_dump(mode="json"),
    }
    return {**body, "subject": subject, "restricted": False}
