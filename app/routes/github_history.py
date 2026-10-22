"""
GitHub insights for an imported project.

GET  /projects/{id}/github           contributors, weekly commits, recent commits and pushes
GET  /projects/{id}/github/commits   the full stored commit history, optionally for one contributor
GET  /projects/{id}/github/pushes    the stored push history, optionally for one contributor
POST /projects/{id}/github/refresh   the owner re-reads the repository with a live import handle
"""

from collections import Counter, defaultdict
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.github_history import ProjectCommit, ProjectContributor, ProjectPush
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


def _commit_view(c: ProjectCommit, person) -> dict:
    return {"sha": c.sha, "message": c.message, "committed_at": c.committed_at,
            "login": c.author_login, "name": c.author_name or c.author_login, "user": person}


def _push_view(p: ProjectPush, person) -> dict:
    return {"id": p.event_id, "branch": p.branch, "commit_count": p.commit_count, "pushed_at": p.pushed_at,
            "login": p.actor_login, "user": person}


def _contributor(db: Session, project_id: UUID, contributor_id: Optional[UUID]) -> Optional[ProjectContributor]:
    if contributor_id is None:
        return None
    row = db.query(ProjectContributor).filter_by(id=contributor_id, project_id=project_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Contributor not found")
    return row


@router.get("")
def insights(project_id: UUID, db: Session = Depends(get_db), viewer: Optional[User] = Depends(get_current_user_optional)):
    project = _project(db, project_id, viewer)
    contributors = (db.query(ProjectContributor).filter_by(project_id=project_id)
                    .order_by(ProjectContributor.commits.desc()).all())
    pushes = db.query(ProjectPush).filter_by(project_id=project_id).order_by(ProjectPush.pushed_at.desc().nullslast()).all()
    recent = (db.query(ProjectCommit).filter_by(project_id=project_id)
              .order_by(ProjectCommit.committed_at.desc().nullslast()).limit(20).all())
    match = history.matcher_for(db, [project], contributors, pushes, recent)

    def who(github_id, email=None):
        return match.user(project.id, github_id, email)

    people = _people(db, {uid for uid in [who(c.github_id, c.email) for c in contributors]
                          + [who(p.actor_github_id) for p in pushes]
                          + [who(c.author_github_id, c.author_email) for c in recent] if uid})
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
        "commits": [_commit_view(c, people.get(who(c.author_github_id, c.author_email))) for c in recent],
        "pushes": [_push_view(p, people.get(who(p.actor_github_id))) for p in pushes[:20]],
    }


@router.get("/commits")
def commit_history(project_id: UUID, contributor: Optional[UUID] = None, offset: int = Query(0, ge=0),
                   limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db),
                   viewer: Optional[User] = Depends(get_current_user_optional)):
    project = _project(db, project_id, viewer)
    query = db.query(ProjectCommit).filter_by(project_id=project_id)
    if row := _contributor(db, project_id, contributor):
        if row.github_id:
            query = query.filter(ProjectCommit.author_github_id == row.github_id)
        elif row.email:
            query = query.filter(ProjectCommit.author_github_id.is_(None), ProjectCommit.author_email == row.email)
        else:
            query = query.filter(ProjectCommit.author_github_id.is_(None), ProjectCommit.author_name == row.name)
    total = query.count()
    rows = query.order_by(ProjectCommit.committed_at.desc().nullslast(), ProjectCommit.sha).offset(offset).limit(limit).all()
    match = history.matcher_for(db, [project], [], commits=rows)
    people = _people(db, {uid for c in rows if (uid := match.user(project.id, c.author_github_id, c.author_email))})
    return {"total": total, "items": [
        _commit_view(c, people.get(match.user(project.id, c.author_github_id, c.author_email))) for c in rows
    ]}


@router.get("/pushes")
def push_history(project_id: UUID, contributor: Optional[UUID] = None, offset: int = Query(0, ge=0),
                 limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db),
                 viewer: Optional[User] = Depends(get_current_user_optional)):
    project = _project(db, project_id, viewer)
    query = db.query(ProjectPush).filter_by(project_id=project_id)
    if row := _contributor(db, project_id, contributor):
        query = query.filter(or_(ProjectPush.actor_github_id == row.github_id, ProjectPush.actor_login == row.login)
                             if row.github_id else ProjectPush.id.is_(None))
    total = query.count()
    rows = query.order_by(ProjectPush.pushed_at.desc().nullslast(), ProjectPush.event_id).offset(offset).limit(limit).all()
    match = history.matcher_for(db, [project], [], pushes=rows)
    people = _people(db, {uid for p in rows if (uid := match.user(project.id, p.actor_github_id))})
    return {"total": total, "items": [_push_view(p, people.get(match.user(project.id, p.actor_github_id))) for p in rows]}


@router.post("/refresh", dependencies=[rate_limit("github_refresh", 6, 60)])
def refresh(project_id: UUID, data: RefreshRequest, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
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
