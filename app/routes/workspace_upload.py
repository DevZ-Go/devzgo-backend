"""
ZIP workspace upload — core logic only (no FastAPI router here).

Why logic lives in this module
------------------------------
Keeps extraction, zip-slip checks, and DB indexing in one testable place.
Routes are declared in app.routes.projects so paths stay under /projects/...
and are registered in the correct order (before GET /projects/{id}).

End-to-end flow (perform_workspace_zip_upload)
----------------------------------------------
1. Validate filename ends with .zip.
2. Confirm project exists and current_user is the owner.
3. Stream the upload to a temp .zip file.
4. DELETE all File rows for this project (clean slate).
5. Delete old storage/project_<uuid>/ folder if present; recreate empty.
6. safe_extract_zip: write files under that folder; skip malicious paths.
7. build_file_records_from_workspace: os.walk → new File rows (dirs + files).
8. commit; delete temp zip; close upload stream.

See perform_workspace_zip_upload() for the orchestration.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.models.file import File as FileRecord
from app.models.project import Project
from app.models.user import User

STORAGE_ROOT = Path("storage")


def workspace_dir_for_project(project_id: UUID) -> Path:
    """Directory on disk where the extracted tree lives: storage/project_<uuid>/."""
    return STORAGE_ROOT / f"project_{project_id}"


def _is_zip_upload(upload: UploadFile) -> bool:
    """Require .zip in the client filename (MIME types are unreliable)."""
    name = (upload.filename or "").strip().lower()
    return name.endswith(".zip")


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """
    Extract archive under dest_dir; block zip-slip (.., absolute paths).
    Each member is resolved and must stay under dest_dir.resolve().
    """
    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            raw_name = member.filename
            if not raw_name:
                continue

            rel = Path(raw_name)
            if rel.is_absolute() or ".." in rel.parts:
                continue

            target = (dest_dir / rel).resolve()
            try:
                target.relative_to(dest_dir)
            except ValueError:
                continue

            is_dir = member.is_dir() if hasattr(member, "is_dir") else raw_name.endswith("/")
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, "r") as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)


def _parent_path_for_relative(rel: Path) -> str | None:
    """Parent folder as posix string, or None at workspace root."""
    parent = rel.parent
    if parent == Path("."):
        return None
    p = parent.as_posix()
    return p if p else None


def build_file_records_from_workspace(
    project_id: UUID, workspace_root: Path
) -> tuple[list[FileRecord], int]:
    """
    os.walk the extracted tree → ORM rows for the files table.
    First pass: directories (is_directory=True). Second: files.
    Returns (rows, count_of_files_only).
    """
    workspace_root = workspace_root.resolve()
    rows: list[FileRecord] = []
    file_count = 0

    for dirpath, _dirnames, _filenames in os.walk(workspace_root):
        rel_dir = Path(dirpath).relative_to(workspace_root)
        if rel_dir == Path("."):
            continue
        posix_dir = rel_dir.as_posix()
        rows.append(
            FileRecord(
                project_id=project_id,
                file_name=rel_dir.name,
                file_path=posix_dir,
                is_directory=True,
                parent_path=_parent_path_for_relative(rel_dir),
            )
        )

    for dirpath, _dirnames, filenames in os.walk(workspace_root):
        rel_parent = Path(dirpath).relative_to(workspace_root)
        for fname in filenames:
            rel_file = rel_parent / fname if rel_parent != Path(".") else Path(fname)
            posix_file = rel_file.as_posix()
            rows.append(
                FileRecord(
                    project_id=project_id,
                    file_name=fname,
                    file_path=posix_file,
                    is_directory=False,
                    parent_path=_parent_path_for_relative(rel_file),
                )
            )
            file_count += 1

    return rows, file_count


def perform_workspace_zip_upload(
    project_id: UUID,
    file: UploadFile,
    db: Session,
    current_user: User,
) -> dict:
    """
    Run the full workspace replacement: extract ZIP, rebuild files table.

    Returns: {"message": str, "total_files": int}

    Side effects: closes file.file, deletes temp zip, commits DB on success.
    """
    if not _is_zip_upload(file):
        raise HTTPException(
            status_code=400,
            detail="Only .zip files are allowed",
        )

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    workspace_root = workspace_dir_for_project(project_id)
    tmp_zip: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_zip = Path(tmp.name)
            shutil.copyfileobj(file.file, tmp)

        db.query(FileRecord).filter(FileRecord.project_id == project_id).delete(
            synchronize_session=False
        )

        if workspace_root.exists():
            shutil.rmtree(workspace_root)
        workspace_root.mkdir(parents=True, exist_ok=True)

        safe_extract_zip(tmp_zip, workspace_root)

        new_rows, total_files = build_file_records_from_workspace(project_id, workspace_root)
        for row in new_rows:
            db.add(row)

        db.commit()

        return {
            "message": "Workspace uploaded successfully",
            "total_files": total_files,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        file.file.close()
        if tmp_zip is not None and tmp_zip.exists():
            tmp_zip.unlink(missing_ok=True)
