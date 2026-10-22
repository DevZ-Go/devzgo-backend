"""Metadata, a tokenised byte stream, and an archive listing. Access is checked before any bytes are read."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user_optional
from app.db.session import get_db
from app.models.project import Project
from app.models.user import User
from app.routes.projects import _require_project_file_access, _resolve_indexed_relative_path
from app.services.file_delivery import list_archive, meta_payload, read_stream_token, serve_stored_file
from app.services.workspace_files import is_secret_path

router = APIRouter(prefix="/projects", tags=["File preview"])


def _open(db: Session, project_id: UUID, path: str, user: User | None) -> tuple[Project, dict, str]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    access = _require_project_file_access(db, project, user)
    relative = _resolve_indexed_relative_path(project_id, path, db)
    if is_secret_path(relative) and not access["secrets"]:
        raise HTTPException(status_code=403, detail="This file may contain secrets and is hidden from public preview.")
    return project, access, relative


@router.get("/{project_id}/files/meta")
def file_meta(
    project_id: UUID,
    path: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    _project, _access, relative = _open(db, project_id, path, current_user)
    return meta_payload(project_id, relative, current_user.id if current_user else None, is_secret_path(relative))


@router.get("/{project_id}/files/archive")
def file_archive(
    project_id: UUID,
    path: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    _project, _access, relative = _open(db, project_id, path, current_user)
    return list_archive(project_id, relative)


@router.get("/{project_id}/files/stream")
def file_stream(
    project_id: UUID,
    request: Request,
    path: str = Query(..., min_length=1),
    token: str | None = None,
    download: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    user = current_user
    if token:
        claims = read_stream_token(token)
        if claims.get("pid") != str(project_id) or bool(claims.get("dl")) != download:
            raise HTTPException(status_code=401, detail="This preview link has expired.")
        if claims.get("sub") not in (None, "anon"):
            user = db.query(User).filter(User.id == claims["sub"]).one_or_none()
        _project, _access, relative = _open(db, project_id, path, user)
        if claims.get("path") != relative:
            raise HTTPException(status_code=401, detail="This preview link has expired.")
    else:
        _project, _access, relative = _open(db, project_id, path, user)
    return serve_stored_file(project_id, relative, request.headers.get("range"), download)
