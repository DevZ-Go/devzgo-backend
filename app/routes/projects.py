from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.file import File as FileRecord
from app.models.project import Project
from app.models.techstack import TechStack
from app.models.enums import ProjectCategory, ProjectVisibility
from app.schemas.project import (
    ProjectAnalysisResponse,
    ProjectCreate,
    ProjectFileContentResponse,
    ProjectFileEntry,
    ProjectResponse,
)
from app.core.dependencies import get_current_user, get_current_user_optional
from app.models.user import User
from app.routes.workspace_upload import perform_workspace_zip_upload
from app.services.project_storage import delete_all_project_storage
from app.services.workspace_files import (
    is_image_path,
    is_secret_path,
    language_for_path,
    normalize_relative_path,
    resolve_workspace_file,
    sniff_is_binary,
)

router = APIRouter(prefix="/projects", tags=["Projects"])


# ---------------- CREATE PROJECT ----------------
@router.post("", response_model=ProjectResponse)
def create_project(
    project_data: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    tech_stacks = db.query(TechStack).filter(
        TechStack.id.in_(project_data.tech_stack_ids)
    ).all()

    if len(tech_stacks) != len(project_data.tech_stack_ids):
        raise HTTPException(status_code=400, detail="Invalid tech stack ID provided")

    cat_other = (
        project_data.category_other
        if project_data.category == ProjectCategory.OTHER
        else None
    )
    new_project = Project(
        owner_id=current_user.id,
        title=project_data.title,
        short_description=project_data.short_description,
        full_description=project_data.full_description,
        category=project_data.category,
        category_other=cat_other,
        visibility=project_data.visibility,
        cover_image_url=project_data.cover_image_url,
    )

    new_project.tech_stacks = tech_stacks

    db.add(new_project)
    db.commit()
    db.refresh(new_project)

    return build_project_response(
        new_project, current_user.username, viewer_user_id=current_user.id
    )


# ---------------- LIST PUBLIC PROJECTS ----------------
@router.get("", response_model=List[ProjectResponse])
def list_public_projects(
    tech_stack_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    query = db.query(Project).filter(
        Project.visibility == ProjectVisibility.PUBLIC
    )

    if tech_stack_id:
        query = query.join(Project.tech_stacks).filter(
            TechStack.id == tech_stack_id
        )

    projects = query.all()

    vid = current_user.id if current_user else None
    return [
        build_project_response(p, p.owner.username, viewer_user_id=vid)
        for p in projects
    ]


# ---------------- LIST MY PROJECTS ----------------
# 🔥 IMPORTANT: must come BEFORE /{project_id}
@router.get("/me", response_model=List[ProjectResponse])
def list_my_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    projects = db.query(Project).filter(
        Project.owner_id == current_user.id
    ).all()

    return [
        build_project_response(p, current_user.username, viewer_user_id=current_user.id)
        for p in projects
    ]


# ---------------- LIST TECH STACKS ----------------
# 🔥 IMPORTANT: must come BEFORE /{project_id}
@router.get("/techstacks")
def list_techstacks(db: Session = Depends(get_db)):
    tech_stacks = db.query(TechStack).all()
    return [{"id": tech.id, "name": tech.name} for tech in tech_stacks]


# ---------------- PROJECT ANALYSIS ----------------
@router.get("/{project_id}/analysis", response_model=ProjectAnalysisResponse)
def get_project_analysis(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Return stored language stats and detected vs confirmed tech stacks."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.visibility == ProjectVisibility.PRIVATE:
        if current_user is None or project.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Private project")

    detected_names, detected_ids = _detected_names_for_project(project)
    return ProjectAnalysisResponse(
        project_id=project.id,
        languages=project.language_stats or [],
        detected_tech_stacks=detected_names,
        detected_tech_stack_ids=detected_ids,
        confirmed_tech_stacks=[t.name for t in project.tech_stacks],
        confirmed_tech_stack_ids=[int(t.id) for t in project.tech_stacks],
    )


# ---------------- WORKSPACE ZIP (must be before GET /{project_id}) ----------------
@router.post("/{project_id}/workspace/upload")
def upload_project_workspace(
    project_id: UUID,
    file: UploadFile = File(
        ...,
        description='Multipart field name: "file". Filename must end with .zip.',
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Owner uploads a ZIP; server extracts to storage/project_<id>/ and rebuilds the files table.

    Delegates to perform_workspace_zip_upload() in workspace_upload.py.
    """
    return perform_workspace_zip_upload(project_id, file, db, current_user)


# ---------------- LIST PROJECT FILES (workspace + attachments in DB) ----------------
@router.get("/{project_id}/files", response_model=List[ProjectFileEntry])
def list_project_files(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Return all File rows for this project (sorted by file_path).

    Public project: any caller with optional auth (logged-in or not).
    Private project: only the owner (requires valid Bearer token matching owner).
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.visibility == ProjectVisibility.PRIVATE:
        if current_user is None or project.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Private project")

    rows = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id)
        .order_by(FileRecord.file_path.asc())
        .all()
    )
    return rows


def _require_project_file_access(
    project: Project,
    current_user: Optional[User],
) -> None:
    if project.visibility == ProjectVisibility.PRIVATE:
        if current_user is None or project.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Private project")


def _resolve_indexed_relative_path(
    project_id: UUID,
    path: str,
    db: Session,
) -> str:
    """Normalize path and prefer the indexed files.file_path when present."""
    try:
        relative = normalize_relative_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc

    raw_relative = path.strip().replace("\\", "/").lstrip("/")
    file_row = (
        db.query(FileRecord)
        .filter(
            FileRecord.project_id == project_id,
            FileRecord.file_path == relative,
            FileRecord.is_directory.is_(False),
        )
        .first()
    )
    if file_row is None and raw_relative != relative:
        file_row = (
            db.query(FileRecord)
            .filter(
                FileRecord.project_id == project_id,
                FileRecord.file_path == raw_relative,
                FileRecord.is_directory.is_(False),
            )
            .first()
        )
    if file_row is not None:
        return file_row.file_path
    return relative


# ---------------- RAW WORKSPACE FILE (permissioned; images/media) ----------------
# Registered before /file so the path is unambiguous.
@router.get("/{project_id}/file/raw")
def read_project_file_raw(
    project_id: UUID,
    path: str = Query(..., min_length=1, description="Relative path inside project workspace"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Stream a workspace file with the same visibility rules as JSON preview.
    Used for image previews instead of public /storage/project_* URLs.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    _require_project_file_access(project, current_user)
    relative = _resolve_indexed_relative_path(project_id, path, db)

    is_owner = current_user is not None and project.owner_id == current_user.id
    if is_secret_path(relative) and not is_owner:
        raise HTTPException(
            status_code=403,
            detail="This file may contain secrets and is hidden from public preview.",
        )

    try:
        resolved_file = resolve_workspace_file(project_id, relative)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc

    return FileResponse(
        path=resolved_file,
        filename=Path(relative).name,
        content_disposition_type="inline",
    )


# ---------------- READ ONE PROJECT FILE (JSON preview) ----------------
@router.get("/{project_id}/file", response_model=ProjectFileContentResponse)
def read_project_file(
    project_id: UUID,
    path: str = Query(..., min_length=1, description="Relative path inside project workspace"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Read a single workspace file as a safe JSON preview.

    Public project: readable by anyone (secrets withheld for non-owners).
    Private project: only owner may read.
    Binary files return metadata without content.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    _require_project_file_access(project, current_user)
    relative = _resolve_indexed_relative_path(project_id, path, db)

    try:
        resolved_file = resolve_workspace_file(project_id, relative)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc

    name = Path(relative).name
    extension = Path(relative).suffix.lower()
    size = resolved_file.stat().st_size
    secret = is_secret_path(relative)
    is_owner = current_user is not None and project.owner_id == current_user.id
    image = is_image_path(relative)

    base = ProjectFileContentResponse(
        path=relative,
        name=name,
        extension=extension,
        language=language_for_path(relative),
        size=size,
        is_binary=False,
        is_secret=secret,
        is_image=image,
        content=None,
        message=None,
    )

    if secret and not is_owner:
        base.message = "This file may contain secrets and is hidden from public preview."
        return base

    try:
        sample = resolved_file.read_bytes()[:8192]
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to read file") from exc

    binary = sniff_is_binary(sample, relative)
    base.is_binary = binary

    if binary:
        if image:
            base.message = "Image file — use the raw file endpoint for preview."
        else:
            base.message = "Binary file — content preview is not available."
        return base

    try:
        # Decode full file as UTF-8; reject if clearly invalid for display.
        raw = resolved_file.read_bytes()
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        base.is_binary = True
        base.message = "File is not valid UTF-8 text — content preview is not available."
        return base
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to read file") from exc

    # Cap preview size to keep responses reasonable (2 MiB of text).
    max_preview = 2 * 1024 * 1024
    if len(content) > max_preview:
        content = content[:max_preview]
        base.message = "File truncated for preview (first 2 MiB)."

    base.content = content
    return base


# ---------------- GET SINGLE PROJECT ----------------
@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Get project by id",
)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.visibility == ProjectVisibility.PRIVATE:
        if current_user is None or project.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Private project")

    vid = current_user.id if current_user else None
    return build_project_response(
        project, project.owner.username, viewer_user_id=vid
    )


# ---------------- UPDATE PROJECT ----------------
@router.put(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Update project",
    description="Owner only. Same body shape as create. Requires Bearer JWT (Swagger: Authorize).",
)
def update_project(
    project_id: UUID,
    project_update: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    project.title = project_update.title
    project.short_description = project_update.short_description
    project.full_description = project_update.full_description
    project.category = project_update.category
    project.category_other = (
        project_update.category_other
        if project_update.category == ProjectCategory.OTHER
        else None
    )
    project.visibility = project_update.visibility
    project.cover_image_url = project_update.cover_image_url
    project.demo_video_url = project_update.demo_video_url

    # Replace tech stacks on every update (including [] to clear).
    if project_update.tech_stack_ids:
        tech_stacks = db.query(TechStack).filter(
            TechStack.id.in_(project_update.tech_stack_ids)
        ).all()
        if len(tech_stacks) != len(project_update.tech_stack_ids):
            raise HTTPException(status_code=400, detail="Invalid tech stack ID provided")
        project.tech_stacks = tech_stacks
    else:
        project.tech_stacks = []

    db.commit()
    db.refresh(project)

    return build_project_response(
        project, current_user.username, viewer_user_id=current_user.id
    )


# ---------------- DELETE PROJECT ----------------
@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete project",
    description=(
        "Owner only. Deletes DB rows (`files`, tech links, project) and removes workspace, "
        "attachments, cover image, and demo video from disk. "
        "In Swagger: **Authorize** → paste the JWT string from `POST /auth/login` (field: access_token)."
    ),
    responses={
        204: {"description": "Project and files removed"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Not the project owner"},
        404: {"description": "Project not found"},
    },
)
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Owner-only. Removes workspace on disk, attachment media, cover/demo files, then
    deletes all `files` rows and the project (clears project_tech links).
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if str(project.owner_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    delete_all_project_storage(project)

    db.query(FileRecord).filter(FileRecord.project_id == project_id).delete(
        synchronize_session=False
    )
    project.tech_stacks.clear()
    db.delete(project)
    db.commit()
    return None


# ---------------- HELPER FUNCTION ----------------
def _detected_names_for_project(project: Project) -> tuple[list[str], list[int]]:
    from sqlalchemy.orm import object_session

    raw_ids = project.detected_tech_stack_ids or []
    ids: list[int] = []
    for x in raw_ids:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        return [], []

    db = object_session(project)
    if db is None:
        return [], ids

    rows = db.query(TechStack).filter(TechStack.id.in_(ids)).all()
    by_id = {int(t.id): t.name for t in rows}
    names = [by_id[i] for i in ids if i in by_id]
    return names, [i for i in ids if i in by_id]


def build_project_response(
    project: Project,
    owner_username: str,
    *,
    viewer_user_id: Optional[UUID] = None,
):
    is_owner = (
        viewer_user_id is not None and project.owner_id == viewer_user_id
    )
    detected_names, detected_ids = _detected_names_for_project(project)
    languages = project.language_stats or []

    return ProjectResponse(
        id=project.id,
        owner_id=project.owner_id,
        is_owner=is_owner,
        title=project.title,
        short_description=project.short_description,
        full_description=project.full_description,
        category=project.category,
        category_other=project.category_other,
        visibility=project.visibility,
        cover_image_url=project.cover_image_url,
        demo_video_url=project.demo_video_url,
        created_at=project.created_at,
        updated_at=project.updated_at,
        owner_username=owner_username,
        tech_stacks=[tech.name for tech in project.tech_stacks],
        tech_stack_ids=[tech.id for tech in project.tech_stacks],
        languages=languages,
        detected_tech_stacks=detected_names,
        detected_tech_stack_ids=detected_ids,
    )
