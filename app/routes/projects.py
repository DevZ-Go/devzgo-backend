from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.file import File as FileRecord
from app.models.project import Project
from app.models.techstack import TechStack
from app.models.enums import ProjectVisibility
from app.schemas.project import ProjectCreate, ProjectFileEntry, ProjectResponse
from app.core.dependencies import get_current_user, get_current_user_optional
from app.models.user import User
from app.routes.workspace_upload import perform_workspace_zip_upload
from app.services.project_storage import delete_all_project_storage

router = APIRouter(prefix="/projects", tags=["Projects"])
STORAGE_ROOT = Path("storage")


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

    new_project = Project(
        owner_id=current_user.id,
        title=project_data.title,
        short_description=project_data.short_description,
        full_description=project_data.full_description,
        category=project_data.category,
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


# ---------------- READ ONE PROJECT FILE ----------------
@router.get("/{project_id}/file")
def read_project_file(
    project_id: UUID,
    path: str = Query(..., min_length=1, description="Relative path inside project workspace"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Read a single file from storage/project_<project_id>/ using a safe relative path.

    Public project: readable by anyone.
    Private project: only owner may read.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.visibility == ProjectVisibility.PRIVATE:
        if current_user is None or project.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Private project")

    # Keep behavior stable across clients: handle leading "./", "\" separators, and accidental spaces.
    raw_relative = path.strip().replace("\\", "/").lstrip("/")
    relative_parts = [p for p in raw_relative.split("/") if p not in ("", ".")]
    if not relative_parts or ".." in relative_parts:
        raise HTTPException(status_code=400, detail="Invalid file path")
    relative = "/".join(relative_parts)

    # Prefer DB path if provided path differs only by client normalization.
    file_row = (
        db.query(FileRecord)
        .filter(
            FileRecord.project_id == project_id,
            FileRecord.file_path == relative,
            FileRecord.is_directory.is_(False),
        )
        .first()
    )
    if file_row is None:
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
        relative = file_row.file_path

    workspace_root = STORAGE_ROOT / f"project_{project_id}"
    full_path = workspace_root / relative
    try:
        resolved_root = workspace_root.resolve()
        resolved_file = full_path.resolve()
        resolved_file.relative_to(resolved_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")

    # If the DB row exists but storage path changed shape, try a second normalized fallback.
    if (not resolved_file.exists() or not resolved_file.is_file()) and file_row is not None:
        fallback_relative = file_row.file_path.strip().replace("\\", "/").lstrip("./")
        resolved_file = (workspace_root / fallback_relative).resolve()

    if not resolved_file.exists() or not resolved_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        content = resolved_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to read file")

    return {"content": content}


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
    project.visibility = project_update.visibility
    project.cover_image_url = project_update.cover_image_url
    project.demo_video_url = project_update.demo_video_url

    if project_update.tech_stack_ids:
        tech_stacks = db.query(TechStack).filter(
            TechStack.id.in_(project_update.tech_stack_ids)
        ).all()
        project.tech_stacks = tech_stacks

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
def build_project_response(
    project: Project,
    owner_username: str,
    *,
    viewer_user_id: Optional[UUID] = None,
):
    is_owner = (
        viewer_user_id is not None and project.owner_id == viewer_user_id
    )
    return ProjectResponse(
        id=project.id,
        owner_id=project.owner_id,
        is_owner=is_owner,
        title=project.title,
        short_description=project.short_description,
        full_description=project.full_description,
        category=project.category,
        visibility=project.visibility,
        cover_image_url=project.cover_image_url,
        demo_video_url=project.demo_video_url,
        created_at=project.created_at,
        updated_at=project.updated_at,
        owner_username=owner_username,
        tech_stacks=[tech.name for tech in project.tech_stacks],
        tech_stack_ids=[tech.id for tech in project.tech_stacks],
    )