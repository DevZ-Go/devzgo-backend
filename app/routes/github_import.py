"""
Import GitHub repositories as DevZ-Go projects.

POST /github/import/authorize           -> GitHub consent URL (scope: repo) bound to the signed-in user
GET  /auth/github/callback              -> FRONTEND_URL/import/github#code=..&state=.. (state purpose "github_import")
POST /github/import/session             -> exchange the code once; returns a short-lived handle and the repo list
POST /github/import/repos/{repo_id}     -> create one project from a repo and analyse its code
POST /github/import/session/close       -> forget the handle and revoke the token at GitHub
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.security import ALGORITHM, SECRET_KEY
from app.db.session import get_db
from app.models.enums import ProjectVisibility
from app.models.project import Project
from app.models.user import User
from app.routes.workspace_upload import install_workspace_zip
from app.services import github_import as gh
from app.services.skills import sync_skills_from_projects

router = APIRouter(tags=["GitHub import"])

STATE_PURPOSE = gh.STATE_PURPOSE


def _frontend(path: str) -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/") + path


class SessionRequest(BaseModel):
    code: str
    state: str


class HandleRequest(BaseModel):
    session: str


def _imported_urls(db: Session, user_id: UUID) -> dict[str, UUID]:
    rows = db.query(Project.id, Project.github_url).filter(
        Project.owner_id == user_id, Project.github_url.isnot(None)
    ).all()
    return {url.lower().rstrip("/"): pid for pid, url in rows}


def _token_or_410(handle: str, user: User) -> str:
    token = gh.session_token(handle, user.id)
    if not token:
        raise HTTPException(status_code=410, detail="The GitHub connection expired. Connect GitHub again.")
    return token


@router.post("/github/import/authorize")
def authorize(me: User = Depends(get_current_user)):
    if not gh.configured():
        raise HTTPException(status_code=503, detail="GitHub is not configured on this server")
    state = jwt.encode(
        {"purpose": STATE_PURPOSE, "uid": str(me.id), "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        SECRET_KEY, algorithm=ALGORITHM,
    )
    query = urlencode({
        "client_id": os.getenv("GITHUB_CLIENT_ID"),
        "redirect_uri": gh.import_callback_url(),
        "scope": gh.SCOPE,
        "state": state,
        "allow_signup": "false",
    })
    return {"url": f"https://github.com/login/oauth/authorize?{query}"}


def import_redirect(code: str, state: str, error: str) -> RedirectResponse:
    """Send an import consent back to the import page; the code is exchanged there by the signed-in user."""
    if error or not code:
        return RedirectResponse(_frontend("/import/github?error=denied"))
    return RedirectResponse(_frontend(f"/import/github#{urlencode({'code': code, 'state': state})}"))


@router.post("/github/import/session")
def open_session(data: SessionRequest, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    try:
        claims = jwt.decode(data.state, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        claims = {}
    if claims.get("purpose") != STATE_PURPOSE or claims.get("uid") != str(me.id):
        raise HTTPException(status_code=400, detail="This GitHub link has expired or belongs to another account. Connect GitHub again.")
    try:
        token = gh.exchange_code(data.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="GitHub did not respond. Try again in a moment.")

    handle = gh.open_session(me.id, token)
    try:
        login = gh.github_login(token)
        repos = gh.list_repos(token)
    except httpx.HTTPError:
        gh.close_session(handle, me.id)
        raise HTTPException(status_code=502, detail="Could not read your repositories from GitHub.")

    imported = _imported_urls(db, me.id)
    items = []
    for repo in repos:
        item = gh.summarize_repo(repo)
        pid = imported.get(item["html_url"].lower().rstrip("/"))
        item["imported_project_id"] = str(pid) if pid else None
        items.append(item)
    return {"session": handle, "login": login, "repos": items}


@router.post("/github/import/repos/{repo_id}")
def import_repo(repo_id: int, data: HandleRequest, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    token = _token_or_410(data.session, me)
    try:
        repo = gh.get_repo(token, repo_id)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="GitHub did not respond. Try again in a moment.")
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found on GitHub")

    html_url = repo["html_url"]
    existing = _imported_urls(db, me.id).get(html_url.lower().rstrip("/"))
    if existing:
        raise HTTPException(status_code=409, detail="Already imported")

    try:
        readme = gh.get_readme(token, repo["full_name"])
    except httpx.HTTPError:
        readme = None
    short = (repo.get("description") or "").strip()[:300] or f"{repo['name']}, imported from GitHub."
    full = gh.readme_to_text(readme) if readme else ""
    category = gh.guess_category(repo)
    private = bool(repo.get("private"))
    activity = gh.repo_activity(token, repo["full_name"])
    pushed = repo.get("pushed_at")
    try:
        pushed_at = datetime.fromisoformat(pushed.replace("Z", "+00:00")) if isinstance(pushed, str) else None
    except ValueError:
        pushed_at = None

    project = Project(
        owner_id=me.id,
        title=repo["name"][:120],
        short_description=short,
        full_description=full or short,
        category=category,
        category_other=(repo.get("language") or None) if category.value == "Other" else None,
        visibility=ProjectVisibility.PRIVATE if private else ProjectVisibility.PUBLIC,
        github_url=html_url,
        cover_image_url=None if private else f"https://opengraph.githubassets.com/1/{repo['full_name']}",
        github_commit_count=activity["commits"],
        github_push_count=activity["pushes"],
        github_pushed_at=pushed_at,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    project_id = project.id

    code, detail, analysis = "analysed", None, None
    with tempfile.TemporaryDirectory(prefix="gh_import_") as tmp:
        archive = Path(tmp) / "repo.zip"
        try:
            if gh.download_archive(token, repo["full_name"], repo.get("default_branch") or "HEAD", archive):
                project = db.query(Project).filter(Project.id == project_id).first()
                analysis = install_workspace_zip(db, project, archive, from_github=True)
            else:
                code, detail = "empty", "The repository has no commits yet."
        except gh.ArchiveTooLarge:
            code, detail = "skipped", "The code is larger than the 50 MB upload limit, so only the details were imported."
        except HTTPException as exc:
            code, detail = "skipped", str(exc.detail)
        except httpx.HTTPError:
            code, detail = "failed", "GitHub stopped sending the code. Upload a ZIP from the project page instead."

    sync_skills_from_projects(db, me)
    db.commit()

    return {
        "repo_id": repo_id,
        "project_id": str(project_id),
        "title": repo["name"][:120],
        "visibility": "Private" if private else "Public",
        "code": code,
        "detail": detail,
        "total_files": analysis["total_files"] if analysis else 0,
        "languages": analysis["languages"] if analysis else {},
    }


@router.post("/github/import/session/close")
def close_session(data: HandleRequest, me: User = Depends(get_current_user)):
    gh.close_session(data.session, me.id)
    return {"closed": True}
