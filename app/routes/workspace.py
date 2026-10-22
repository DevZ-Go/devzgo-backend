"""Code workspace: drafts, live branches, one-file and multi-file commits, pull requests, and sync."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_developer
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.project import Project
from app.models.user import User
from app.models.workspace import WorkspaceAuditEvent, WorkspaceDraft, WorkspaceSyncJob
from app.services import crypto, github_app
from app.services.github_connection import access_token, status_of
from app.services.social import project_access
from app.services.workspace_drafts import discard, public, save
from app.services.workspace_files import is_secret_path, normalize_relative_path
from app.services.workspace_git import GitDenied, branch_problem, branches, can_read, commit_file, commit_files, compare, create_branch, file_at, open_pull, remember_repo, require_push, tree
from app.services.workspace_sync import enqueue, run_job
from app.models.workspace import GitHubConnection
from app.services.file_types import EDIT_MAX_BYTES, blocked_edit_reason

router = APIRouter(prefix="/projects/{project_id}/workspace", tags=["Code workspace"])


class DraftIn(BaseModel):
    branch: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=500)
    content: str
    base_blob_sha: str | None = None
    base_commit_sha: str | None = None
    line_ending: str | None = None
    has_bom: bool | None = None


class CommitIn(BaseModel):
    branch: str
    path: str
    content: str
    message: str = Field(min_length=1, max_length=400)
    base_blob_sha: str | None = None
    base_commit_sha: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=80)


class CommitFileIn(BaseModel):
    path: str
    content: str
    base_blob_sha: str | None = None


class MultiCommitIn(BaseModel):
    branch: str
    message: str = Field(min_length=1, max_length=400)
    parent_sha: str = Field(min_length=40, max_length=40)
    files: list[CommitFileIn] = Field(min_length=1, max_length=20)
    idempotency_key: str = Field(min_length=8, max_length=80)


class BranchIn(BaseModel):
    name: str
    from_sha: str = Field(min_length=40, max_length=40)


class PullIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = ""
    head: str
    base: str


class RefreshIn(BaseModel):
    kind: str


def _project(db: Session, project_id: UUID, user: User) -> tuple[Project, dict]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    access = project_access(db, project, user.id)
    if not access["view"]:
        raise HTTPException(status_code=403, detail="Private project")
    return project, access


def _writer(db: Session, project: Project, user: User) -> str:
    if user.account_type == "recruiter":
        raise HTTPException(status_code=403, detail="Recruiters can't edit project files.")
    access = project_access(db, project, user.id)
    if not access["files"]:
        raise HTTPException(status_code=403, detail="You can't edit files in this project.")
    if (project.source_kind or "zip") != "github":
        raise HTTPException(status_code=409, detail="ZIP projects are read-only. Import from GitHub to edit.")
    token = access_token(db, user)
    try:
        body = require_push(project, token)
    except GitDenied as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    remember_repo(db, project, body)
    return token


def _guard_path(path: str) -> str:
    try:
        relative = normalize_relative_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc
    reason = blocked_edit_reason(relative)
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    return relative


def _audit(db: Session, user: User, project: Project, action: str, outcome: str, **fields) -> WorkspaceAuditEvent:
    row = WorkspaceAuditEvent(
        user_id=user.id, project_id=project.id, action=action, outcome=outcome,
        repo_full_name=project.github_full_name, **fields,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(WorkspaceAuditEvent).filter_by(idempotency_key=fields.get("idempotency_key")).one_or_none()
        if existing:
            return existing
        raise
    db.refresh(row)
    return row


def _prior(db: Session, key: str) -> WorkspaceAuditEvent | None:
    return db.query(WorkspaceAuditEvent).filter_by(idempotency_key=key, outcome="ok").one_or_none()


def _finish(db: Session, project: Project, user: User, background: BackgroundTasks, audit: WorkspaceAuditEvent,
            branch: str, sha: str | None, path: str | None, paths: list[str]) -> dict:
    for item in paths:
        db.query(WorkspaceDraft).filter_by(user_id=user.id, project_id=project.id, branch=branch, path=item).delete()
    db.commit()
    job = enqueue(db, project, user.id, audit.id, "commit", branch, sha, path if len(paths) == 1 else None)
    background.add_task(run_job, job.id)
    return {"committed": True, "sha": sha, "url": audit.url, "branch": branch, "sync": "pending", "replayed": False}


@router.get("")
def workspace_status(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    project, access = _project(db, project_id, me)
    connection = db.query(GitHubConnection).filter_by(user_id=me.id).one_or_none()
    latest = db.query(WorkspaceSyncJob).filter_by(project_id=project.id).order_by(WorkspaceSyncJob.created_at.desc()).first()
    can_edit = bool(access["files"] and me.account_type != "recruiter" and (project.source_kind or "zip") == "github")
    return {
        "source_kind": project.source_kind or "zip",
        "repository": project.github_full_name,
        "default_branch": project.github_default_branch,
        "tracked_branch": project.github_tracked_branch or project.github_default_branch,
        "can_edit": can_edit,
        "configured": github_app.configured() and crypto.configured(),
        "connection": status_of(connection),
        "sync": None if latest is None else {
            "id": str(latest.id), "status": latest.status, "error": latest.error, "commit_sha": latest.commit_sha,
            "branch": latest.branch, "steps": latest.steps,
        },
    }


@router.get("/drafts")
def list_drafts(project_id: UUID, branch: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, access = _project(db, project_id, me)
    if not access["files"]:
        raise HTTPException(status_code=403, detail="You can't edit files in this project.")
    rows = db.query(WorkspaceDraft).filter_by(user_id=me.id, project_id=project.id, branch=branch).all()
    return [public(row) for row in rows]


@router.put("/drafts", dependencies=[rate_limit("draft", 60, 60)])
def put_draft(project_id: UUID, data: DraftIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, access = _project(db, project_id, me)
    if not access["files"] or (project.source_kind or "zip") != "github":
        raise HTTPException(status_code=403, detail="This project can't be edited.")
    if len(data.content.encode()) > EDIT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Files larger than 1 MB can't be edited here.")
    row = save(db, me, project, data.branch, data.path, data.content, data.base_blob_sha, data.base_commit_sha, data.line_ending, data.has_bom)
    return public(row)


@router.delete("/drafts", dependencies=[rate_limit("draft", 60, 60)])
def delete_draft(project_id: UUID, branch: str, path: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, access = _project(db, project_id, me)
    if not access["files"]:
        raise HTTPException(status_code=403, detail="You can't edit files in this project.")
    discard(db, me, project.id, branch, path)
    return {"deleted": True}


@router.get("/branches")
def list_branches(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, _access = _project(db, project_id, me)
    if (project.source_kind or "zip") != "github":
        raise HTTPException(status_code=409, detail="This project has no GitHub repository.")
    token = access_token(db, me)
    try:
        body = can_read(project, token)
        remember_repo(db, project, body)
        rows = branches(token, project.github_full_name)
    except GitDenied as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    default = body.get("default_branch")
    return [{"name": row.get("name"), "sha": (row.get("commit") or {}).get("sha"), "protected": bool(row.get("protected")),
             "default": row.get("name") == default} for row in rows]


@router.get("/tree")
def live_tree(project_id: UUID, ref: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, _access = _project(db, project_id, me)
    token = access_token(db, me)
    try:
        can_read(project, token)
        return tree(token, project.github_full_name, ref)
    except GitDenied as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.get("/file")
def live_file(project_id: UUID, ref: str, path: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, access = _project(db, project_id, me)
    relative = normalize_safe(path)
    if is_secret_path(relative) and not access["secrets"]:
        raise HTTPException(status_code=403, detail="This file may contain secrets and is hidden from public preview.")
    token = access_token(db, me)
    try:
        can_read(project, token)
        found = file_at(token, project.github_full_name, relative, ref)
    except GitDenied as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    found["editable"] = found.get("content") is not None and (found.get("size") or 0) <= EDIT_MAX_BYTES and blocked_edit_reason(relative) is None
    found["blocked"] = blocked_edit_reason(relative)
    return found


def normalize_safe(path: str) -> str:
    try:
        return normalize_relative_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc


@router.post("/branches", dependencies=[rate_limit("git_write", 20, 60)])
def make_branch(project_id: UUID, data: BranchIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, _access = _project(db, project_id, me)
    problem = branch_problem(data.name)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    token = _writer(db, project, me)
    try:
        made = create_branch(token, project.github_full_name, data.name, data.from_sha)
    except GitDenied as exc:
        _audit(db, me, project, "create_branch", "failed", branch=data.name, error_code=str(exc.status))
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    _audit(db, me, project, "create_branch", "ok", branch=data.name, commit_sha=made["sha"])
    return made


@router.post("/commit", dependencies=[rate_limit("git_write", 20, 60)])
def commit_one(project_id: UUID, data: CommitIn, background: BackgroundTasks, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, _access = _project(db, project_id, me)
    prior = _prior(db, data.idempotency_key)
    if prior:
        return {"committed": True, "sha": prior.commit_sha, "url": prior.url, "branch": prior.branch, "sync": "pending", "replayed": True}
    relative = _guard_path(data.path)
    if len(data.content.encode()) > EDIT_MAX_BYTES or "\x00" in data.content:
        raise HTTPException(status_code=400, detail="Only UTF-8 text files up to 1 MB can be committed here.")
    token = _writer(db, project, me)
    draft = db.query(WorkspaceDraft).filter_by(user_id=me.id, project_id=project.id, branch=data.branch, path=relative).with_for_update().one_or_none()
    try:
        made = commit_file(token, project.github_full_name, data.branch, relative, data.content, data.base_blob_sha, data.message)
    except GitDenied as exc:
        if draft is not None and exc.status == 409:
            draft.base_blob_sha = None
            db.commit()
        _audit(db, me, project, "commit", "failed", branch=data.branch, path=relative, error_code=str(exc.status), idempotency_key=None)
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    audit = _audit(db, me, project, "commit", "ok", branch=data.branch, path=relative, commit_sha=made["sha"], url=made["url"], idempotency_key=data.idempotency_key)
    return _finish(db, project, me, background, audit, data.branch, made["sha"], relative, [relative])


@router.post("/commits", dependencies=[rate_limit("git_write", 20, 60)])
def commit_many(project_id: UUID, data: MultiCommitIn, background: BackgroundTasks, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, _access = _project(db, project_id, me)
    prior = _prior(db, data.idempotency_key)
    if prior:
        return {"committed": True, "sha": prior.commit_sha, "url": prior.url, "branch": prior.branch, "sync": "pending", "replayed": True}
    files = []
    paths = []
    for item in data.files:
        relative = _guard_path(item.path)
        if len(item.content.encode()) > EDIT_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Files larger than 1 MB can't be edited here.")
        files.append({"path": relative, "content": item.content, "base_blob_sha": item.base_blob_sha})
        paths.append(relative)
    token = _writer(db, project, me)
    try:
        made = commit_files(token, project.github_full_name, data.branch, files, data.message, data.parent_sha)
    except GitDenied as exc:
        _audit(db, me, project, "commit", "failed", branch=data.branch, error_code=str(exc.status))
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    audit = _audit(db, me, project, "commit", "ok", branch=data.branch, commit_sha=made["sha"], url=made["url"], idempotency_key=data.idempotency_key)
    return _finish(db, project, me, background, audit, data.branch, made["sha"], None, paths)


@router.get("/compare")
def compare_branches(project_id: UUID, base: str, head: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, _access = _project(db, project_id, me)
    token = access_token(db, me)
    try:
        can_read(project, token)
        return compare(token, project.github_full_name, base, head)
    except GitDenied as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.post("/pulls", dependencies=[rate_limit("git_write", 10, 60)])
def pull_request(project_id: UUID, data: PullIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, _access = _project(db, project_id, me)
    token = _writer(db, project, me)
    try:
        made = open_pull(token, project.github_full_name, data.title, data.body, data.head, data.base)
    except GitDenied as exc:
        _audit(db, me, project, "open_pr", "failed", branch=data.head, error_code=str(exc.status))
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    audit = _audit(db, me, project, "open_pr", "ok", branch=data.head, url=made["url"])
    from app.services.social import notify
    for member_id in {project.owner_id, *(row.user_id for row in project.collaborators)}:
        if member_id != me.id:
            notify(db, member_id, me, "workspace_pr", str(project.id), f"{me.username} opened a pull request on {project.title}"[:255])
    db.commit()
    return {**made, "audit_id": str(audit.id)}


@router.post("/sync/{job_id}/retry", dependencies=[rate_limit("git_write", 10, 60)])
def retry_sync(project_id: UUID, job_id: UUID, background: BackgroundTasks, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, access = _project(db, project_id, me)
    if not access["files"]:
        raise HTTPException(status_code=403, detail="You can't edit files in this project.")
    job = db.query(WorkspaceSyncJob).filter_by(id=job_id, project_id=project.id).one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Sync job not found")
    if job.status == "ok":
        return {"status": "ok"}
    job.status = "pending"
    job.error = None
    db.commit()
    background.add_task(run_job, job.id)
    return {"status": "pending"}


@router.post("/refresh", dependencies=[rate_limit("git_write", 10, 60)])
def refresh_source(project_id: UUID, data: RefreshIn, background: BackgroundTasks, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    project, access = _project(db, project_id, me)
    if data.kind not in ("history", "source", "analysis"):
        raise HTTPException(status_code=400, detail="Choose history, source, or analysis.")
    if not access["files"]:
        raise HTTPException(status_code=403, detail="You can't refresh this project.")
    if data.kind == "analysis":
        from app.routes.workspace_upload import reanalyse_source
        if not reanalyse_source(db, project):
            raise HTTPException(status_code=404, detail="This project has no stored source to analyse.")
        return {"kind": "analysis", "status": "ok"}
    token = _writer(db, project, me)
    if data.kind == "history":
        from app.services import github_history as history
        if not history.capture(db, project, token, project.github_full_name):
            raise HTTPException(status_code=502, detail="GitHub did not respond. Try again in a moment.")
        db.commit()
        return {"kind": "history", "status": "ok"}
    ref = project.github_tracked_branch or project.github_default_branch or "HEAD"
    job = enqueue(db, project, me.id, None, "refresh", ref, None, None)
    background.add_task(run_job, job.id)
    return {"kind": "source", "status": "pending", "job_id": str(job.id)}
