"""One person's unsaved edits for one file on one branch. The text is encrypted at rest."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.user import User
from app.models.workspace import DRAFT_DAYS, WorkspaceDraft
from app.services import crypto
from app.services.file_types import EDIT_MAX_BYTES, blocked_edit_reason
from app.services.workspace_files import normalize_relative_path


def _require_key() -> None:
    if not crypto.configured():
        raise HTTPException(status_code=503, detail="Editing isn't configured on this server.")


def line_ending_of(text: str) -> str:
    return "crlf" if "\r\n" in text else "lf"


def has_bom(text: str) -> bool:
    return text.startswith("\ufeff")


def save(db: Session, user: User, project: Project, branch: str, path: str, content: str,
         base_blob_sha: str | None, base_commit_sha: str | None, line_ending: str | None, bom: bool | None) -> WorkspaceDraft:
    _require_key()
    try:
        relative = normalize_relative_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc
    reason = blocked_edit_reason(relative)
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    if len(content.encode()) > EDIT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Files larger than 1 MB can't be edited here.")
    if "\x00" in content:
        raise HTTPException(status_code=400, detail="Only UTF-8 text files can be edited.")
    row = db.query(WorkspaceDraft).filter_by(user_id=user.id, project_id=project.id, branch=branch, path=relative).one_or_none()
    if row is None:
        row = WorkspaceDraft(user_id=user.id, project_id=project.id, branch=branch, path=relative, revision=0)
        db.add(row)
    row.repo_id = project.github_repo_id
    row.base_blob_sha = base_blob_sha
    row.base_commit_sha = base_commit_sha
    row.content_enc = crypto.encrypt(content)
    row.content_sha256 = crypto.sha256(content)
    row.line_ending = line_ending if line_ending in ("lf", "crlf") else line_ending_of(content)
    row.has_bom = has_bom(content) if bom is None else bom
    row.revision = (row.revision or 0) + 1
    db.commit()
    db.refresh(row)
    return row


def read(row: WorkspaceDraft) -> str:
    return crypto.decrypt(row.content_enc)


def public(row: WorkspaceDraft) -> dict:
    return {
        "id": str(row.id),
        "branch": row.branch,
        "path": row.path,
        "content": read(row),
        "base_blob_sha": row.base_blob_sha,
        "base_commit_sha": row.base_commit_sha,
        "line_ending": row.line_ending,
        "has_bom": row.has_bom,
        "revision": row.revision,
        "updated_at": row.updated_at,
    }


def discard(db: Session, user: User, project_id, branch: str, path: str) -> None:
    relative = normalize_relative_path(path)
    db.query(WorkspaceDraft).filter_by(user_id=user.id, project_id=project_id, branch=branch, path=relative).delete()
    db.commit()


def drop_expired(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=DRAFT_DAYS)
    removed = db.query(WorkspaceDraft).filter(WorkspaceDraft.updated_at < cutoff).delete(synchronize_session=False)
    db.commit()
    return removed
