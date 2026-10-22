"""
GitHub insights for an imported project.

GET  /projects/{id}/github           totals, weekly commits and each contributor's share
POST /projects/{id}/github/refresh   the owner re-reads the repository with a live import handle

The full commit and push history is served by the activity log (app/routes/activity_log.py).
"""

from collections import Counter, defaultdict
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user_optional, require_developer
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.github_history import ProjectContributor, ProjectPush
from app.models.project import Project
from app.models.user import User
from app.services import github_history as history
from app.services import github_import as gh
from app.services.social import can_view_project, user_summary

router = APIRouter(prefix="/projects/{project_id}/github", tags=["GitHub history"])


class RefreshRequest(BaseModel):
    session: str


def _project(db: Session, project_id: UUID, viewer: Optional[User]) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or not can_view_project(db, project, viewer.id if viewer else None):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _people(db: Session, ids: set[UUID]) -> dict:
    if not ids:
        return {}
    users = db.query(User).options(selectinload(User.profile)).filter(User.id.in_(ids)).all()
    return {u.id: user_summary(u).model_dump(mode="json") for u in users}


@router.get("")
def insights(project_id: UUID, db: Session = Depends(get_db), viewer: Optional[User] = Depends(get_current_user_optional)):
    project = _project(db, project_id, viewer)
    contributors = (db.query(ProjectContributor).filter_by(project_id=project_id)
                    .order_by(ProjectContributor.commits.desc()).all())
    pushes = db.query(ProjectPush).filter_by(project_id=project_id).order_by(ProjectPush.pushed_at.desc().nullslast()).all()
    match = history.matcher_for(db, [project], contributors)

    def who(github_id, email=None):
        return match.user(project.id, github_id, email)

    people = _people(db, {uid for uid in (who(c.github_id, c.email) for c in contributors) if uid})
    pushes_by = Counter(p.actor_github_id or p.actor_login for p in pushes)
    total = sum(c.commits or 0 for c in contributors)
    weekly: dict[int, int] = defaultdict(int)
    for c in contributors:
        for week, count in c.weekly or []:
            weekly[int(week)] += int(count)

    return {
        "github_url": project.github_url,
        "synced_at": project.github_synced_at,
        "can_refresh": bool(viewer and viewer.id == project.owner_id and project.github_url),
        "totals": {"commits": project.github_commit_count or total, "pushes": len(pushes), "contributors": len(contributors)},
        "weekly": sorted([w, n] for w, n in weekly.items()),
        "contributors": [{
            "id": c.id,
            "login": c.login,
            "name": c.name or c.login or "Unknown author",
            "avatar_url": c.avatar_url,
            "commits": c.commits or 0,
            "additions": c.additions,
            "deletions": c.deletions,
            "pushes": pushes_by.get(c.github_id, 0) if c.github_id else 0,
            "share": round((c.commits or 0) / total * 100, 1) if total else 0,
            "first_commit_at": c.first_commit_at,
            "last_commit_at": c.last_commit_at,
            "weekly": c.weekly or [],
            "user": people.get(who(c.github_id, c.email)),
        } for c in contributors],
    }


@router.post("/refresh", dependencies=[rate_limit("github_refresh", 6, 60)])
def refresh(project_id: UUID, data: RefreshRequest, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project = _project(db, project_id, me)
    if project.owner_id != me.id:
        raise HTTPException(status_code=403, detail="Only the project owner can refresh GitHub history")
    full_name = history.full_name_from_url(project.github_url)
    if not full_name:
        raise HTTPException(status_code=400, detail="This project is not linked to a GitHub repository")
    token = gh.session_token(data.session, me.id)
    if not token:
        raise HTTPException(status_code=410, detail="The GitHub connection expired. Connect GitHub again.")
    try:
        repo = gh.repo_by_name(token, full_name)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="GitHub did not respond. Try again in a moment.")
    if repo is None:
        raise HTTPException(status_code=404, detail="GitHub could not find this repository with your account")
    total = gh.repo_activity(token, full_name)["commits"]
    if not history.capture(db, project, token, full_name, total):
        raise HTTPException(status_code=502, detail="GitHub did not respond. Try again in a moment.")
    db.commit()
    return insights(project_id, db, me)
