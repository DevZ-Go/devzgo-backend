"""
ZIP workspace upload — orchestration (no FastAPI router here).

Extraction: app.services.workspace_extract
Analysis:   app.services.project_analysis
Routes:     app.routes.projects
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.models.file import File as FileRecord
from app.models.project import Project
from app.models.user import User
from app.services import source_facts, source_state, storage
from app.services.file_types import classify
from app.services.social import project_access
from app.services.project_analysis import (
    AnalysisResult,
    analyze_workspace,
    resolve_tech_stacks_from_names,
)
from app.services.workspace_extract import (
    MAX_EXTRACTED_BYTES,
    MAX_FILE_COUNT,
    MAX_UPLOAD_BYTES,
    safe_extract_zip,
)
from app.services.workspace_files import STORAGE_ROOT, workspace_dir_for_project


def _is_zip_upload(upload: UploadFile) -> bool:
    """Require .zip in the client filename (MIME types are unreliable)."""
    name = (upload.filename or "").strip().lower()
    return name.endswith(".zip")


def _stream_upload_to_temp(upload: UploadFile, max_bytes: int) -> Path:
    """Write upload to a temp file; raise 400 if larger than max_bytes."""
    chunk_size = 1024 * 1024
    total = 0
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        while True:
            chunk = upload.file.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                tmp.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"ZIP upload exceeds maximum size of "
                        f"{max_bytes // (1024 * 1024)} MiB"
                    ),
                )
            tmp.write(chunk)
    return tmp_path


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

    for dirpath, _dirnames, _filenames in os.walk(workspace_root, followlinks=False):
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

    for dirpath, _dirnames, filenames in os.walk(workspace_root, followlinks=False):
        rel_parent = Path(dirpath).relative_to(workspace_root)
        for fname in filenames:
            abs_file = Path(dirpath) / fname
            if abs_file.is_symlink():
                continue
            rel_file = rel_parent / fname if rel_parent != Path(".") else Path(fname)
            posix_file = rel_file.as_posix()
            size = abs_file.stat().st_size
            with open(abs_file, "rb") as handle:
                head = handle.read(4096)
            rows.append(
                FileRecord(
                    project_id=project_id,
                    file_name=fname,
                    file_path=posix_file,
                    is_directory=False,
                    parent_path=_parent_path_for_relative(rel_file),
                    size_bytes=size,
                    kind=classify(posix_file, head).kind,
                )
            )
            file_count += 1

    return rows, file_count


def apply_analysis_to_project(
    db: Session, project: Project, workspace_root: Path, result: AnalysisResult | None = None
) -> dict:
    """
    Run ProjectAnalysisService-equivalent analysis and persist results.

    - Always updates language_stats + detected_tech_stack_ids.
    - Only auto-fills confirmed tech_stacks when the project currently has none
      (first upload). Re-uploads do not wipe user-confirmed stacks.
    """
    result = result or analyze_workspace(workspace_root)
    linked, linked_names, linked_ids = resolve_tech_stacks_from_names(
        db, result.tech_names
    )

    project.language_stats = result.languages
    project.detected_tech_stack_ids = linked_ids

    had_confirmed = bool(project.tech_stacks)
    if not had_confirmed and linked:
        project.tech_stacks = linked

    from app.models.user import User
    from app.services.skills import sync_skills_from_projects
    owner = db.query(User).filter(User.id == project.owner_id).first()
    if owner:
        sync_skills_from_projects(db, owner)

    return {
        "languages": result.languages,
        "detected_tech_stacks": linked_names,
        "detected_tech_stack_ids": linked_ids,
        "applied_to_confirmed": not had_confirmed and bool(linked),
    }


def perform_workspace_zip_upload(
    project_id: UUID,
    file: UploadFile,
    db: Session,
    current_user: User,
) -> dict:
    """
    Run the full workspace replacement: extract ZIP, rebuild files table, analyze.

    Returns upload metadata including languages and detected tech stacks.
    Does not overwrite confirmed tech_stacks when the project already has some.
    """
    if not _is_zip_upload(file):
        raise HTTPException(
            status_code=400,
            detail="Only .zip files are allowed",
        )

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project_access(db, project, current_user.id)["files"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    tmp_zip: Path | None = None
    try:
        tmp_zip = _stream_upload_to_temp(file, MAX_UPLOAD_BYTES)
        return install_workspace_zip(db, project, tmp_zip)
    finally:
        file.file.close()
        if tmp_zip is not None and tmp_zip.exists():
            tmp_zip.unlink(missing_ok=True)


def _lift_single_root(workspace_root: Path) -> None:
    """Archives such as GitHub's wrap everything in one folder; move its contents up a level."""
    entries = list(workspace_root.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        return
    wrapper = entries[0].rename(workspace_root / f".lift_{entries[0].name}")
    for child in wrapper.iterdir():
        child.rename(workspace_root / child.name)
    wrapper.rmdir()


def _record_facts(project: Project, facts: dict) -> None:
    project.source_facts = facts
    project.source_analyzed_at = datetime.now(timezone.utc)
    project.source_facts_version = source_facts.FACTS_VERSION


def reanalyse_source(db: Session, project: Project) -> bool:
    """Record source facts again from the stored copy of the last upload or import.

    Files, languages and confirmed tech stay as they are. False when no stored copy exists.
    """
    project_id = project.id
    with tempfile.TemporaryDirectory(prefix="reanalyse_") as tmp:
        zip_path: Path | None = None
        if storage.is_s3():
            try:
                data = storage.read(f"sources/{project_id}.zip")
            except FileNotFoundError:
                return False
            zip_path = Path(tmp) / "source.zip"
            zip_path.write_bytes(data)
            root = Path(tmp) / "w"
            root.mkdir()
            safe_extract_zip(zip_path, root, skip_symlinks=True)
            entries = list(root.iterdir())
            if len(entries) == 1 and entries[0].is_dir() and not db.query(FileRecord.id).filter(
                    FileRecord.project_id == project_id, FileRecord.file_path == entries[0].name).first():
                _lift_single_root(root)
        else:
            root = workspace_dir_for_project(project_id)
            if not root.is_dir():
                return False
        result = analyze_workspace(root)
        facts = source_facts.collect(root, result, zip_path)
    project = db.query(Project).filter(Project.id == project_id).one()
    _record_facts(project, facts)
    db.commit()
    return True


def _restore_local(final: Path, previous: Path, incoming: Path, swapped: bool) -> None:
    """Put the previous tree back when a swap did not finish. A failed extract never touches the live tree."""
    if incoming.exists():
        shutil.rmtree(incoming, ignore_errors=True)
    if not previous.exists():
        return
    if swapped or not final.exists():
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        if not final.exists():
            previous.rename(final)


def install_workspace_zip(
    db: Session,
    project: Project,
    zip_path: Path,
    from_github: bool = False,
    ref: str | None = None,
    commit_sha: str | None = None,
) -> dict:
    """Replace the project's workspace with the archive, rebuild the files table, analyse, and commit.

    The new tree is extracted beside the current one and swapped in only after extraction succeeds.
    A failed ZIP leaves the previous files in place. Sync state is committed on its own, so a
    rolled-back upload still shows as failed.
    """
    project_id = project.id
    source_state.mark(db, project_id, source_state.PENDING)
    in_bucket = storage.is_s3()
    final = workspace_dir_for_project(project_id)
    incoming = final.with_name(final.name + ".incoming")
    previous = final.with_name(final.name + ".previous")
    workspace_root = Path(tempfile.mkdtemp(prefix="workspace_")) / "w" if in_bucket else incoming
    swapped = False

    try:
        if in_bucket:
            workspace_root.mkdir(parents=True, exist_ok=True)
        else:
            if incoming.exists():
                shutil.rmtree(incoming)
            incoming.parent.mkdir(parents=True, exist_ok=True)
            incoming.mkdir()

        safe_extract_zip(zip_path, workspace_root, skip_symlinks=from_github)
        if from_github:
            _lift_single_root(workspace_root)

        result = analyze_workspace(workspace_root)
        facts = source_facts.collect(workspace_root, result, zip_path)
        new_rows, total_files = build_file_records_from_workspace(project_id, workspace_root)

        if in_bucket:
            storage.delete_prefix(f"project_{project_id}/")
            storage.upload_dir(workspace_root, f"project_{project_id}")
            with open(zip_path, "rb") as original:
                storage.save(f"sources/{project_id}.zip", original)
        else:
            if previous.exists():
                shutil.rmtree(previous)
            if final.exists():
                final.rename(previous)
            incoming.rename(final)
            swapped = True
            workspace_root = final

        db.query(FileRecord).filter(FileRecord.project_id == project_id).delete(synchronize_session=False)
        db.add_all(new_rows)
        project = db.query(Project).filter(Project.id == project_id).one()
        analysis = apply_analysis_to_project(db, project, workspace_root, result)
        _record_facts(project, facts)
        source_state.record_snapshot(project, "github" if from_github else "zip", ref=ref, commit_sha=commit_sha)
        db.commit()
        if not in_bucket and previous.exists():
            shutil.rmtree(previous, ignore_errors=True)

        return {
            "message": "Workspace uploaded successfully",
            "total_files": total_files,
            "detected_tech_stacks": analysis["detected_tech_stacks"],
            "detected_tech_stack_ids": analysis["detected_tech_stack_ids"],
            "languages": analysis["languages"],
            "applied_to_confirmed": analysis["applied_to_confirmed"],
        }
    except HTTPException as exc:
        db.rollback()
        if not in_bucket:
            _restore_local(final, previous, incoming, swapped)
        detail = exc.detail if isinstance(exc.detail, str) else source_state.GENERIC_FAILURE
        source_state.mark(db, project_id, source_state.FAILED, detail)
        raise
    except Exception:
        db.rollback()
        if not in_bucket:
            _restore_local(final, previous, incoming, swapped)
        source_state.mark(db, project_id, source_state.FAILED, source_state.GENERIC_FAILURE)
        raise
    finally:
        if in_bucket:
            shutil.rmtree(workspace_root.parent, ignore_errors=True)


__all__ = [
    "MAX_UPLOAD_BYTES",
    "MAX_EXTRACTED_BYTES",
    "MAX_FILE_COUNT",
    "STORAGE_ROOT",
    "workspace_dir_for_project",
    "safe_extract_zip",
    "perform_workspace_zip_upload",
    "install_workspace_zip",
    "apply_analysis_to_project",
    "build_file_records_from_workspace",
]
