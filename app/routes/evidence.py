"""Skill matrix and project quality report: read-only views over evidence the viewer is allowed to see."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user_optional, require_developer
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.project import Project
from app.models.user import User
from app.routes.workspace_upload import reanalyse_source
from app.schemas.evidence import QualityReport, SkillMatrix
from app.services.project_quality import quality_report
from app.services.skill_matrix import skill_matrix
from app.services.social import can_view_project, project_access

router = APIRouter(tags=["Evidence"])


@router.get("/profiles/{user_id}/skill-matrix", response_model=SkillMatrix)
def get_skill_matrix(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Technologies this developer has shown, grouped by category, with the projects and files behind each."""
    user = db.query(User).options(selectinload(User.profile)).filter(User.id == user_id).first()
    if not user or user.is_recruiter:
        raise HTTPException(status_code=404, detail="Developer not found")
    return skill_matrix(db, user, current_user.id if current_user else None)


def _viewable(db: Session, project_id: UUID, viewer_id: Optional[UUID]) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or not can_view_project(db, project, viewer_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/projects/{project_id}/quality-report", response_model=QualityReport)
def get_quality_report(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Rule-based checks over the recorded source and the project page. Security findings go to the project team only."""
    viewer_id = current_user.id if current_user else None
    return quality_report(db, _viewable(db, project_id, viewer_id), viewer_id)


@router.post(
    "/projects/{project_id}/quality-report/refresh",
    response_model=QualityReport,
    dependencies=[rate_limit("quality-refresh", 6, 600)],
)
def refresh_quality_report(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_developer),
):
    """Re-analyse the stored copy of the last upload or import. Files, languages and tech stay as they are."""
    project = _viewable(db, project_id, current_user.id)
    access = project_access(db, project, current_user.id)
    if not (access["owner"] or access["files"]):
        raise HTTPException(status_code=403, detail="Only the project team can re-analyse the source")
    if not reanalyse_source(db, project):
        raise HTTPException(status_code=409, detail="There is no stored source to analyse yet. Upload a ZIP or import from GitHub first.")
    db.expire_all()
    return quality_report(db, _viewable(db, project_id, current_user.id), current_user.id)
