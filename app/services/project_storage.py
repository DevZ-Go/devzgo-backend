"""
Delete on-disk assets for a project (workspace extract, attachments folder, cover, demo video).

Used when removing a project so Postgres and the filesystem stay in sync. Paths match how
routes write files: /storage/covers/..., /storage/videos/..., storage/project_<uuid>/,
storage/attachments/<uuid>/.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.models.project import Project

STORAGE = Path("storage")


def _file_under_storage_from_url(url: str | None) -> Path | None:
    if not url or not url.startswith("/storage/"):
        return None
    rel = url.removeprefix("/storage/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    path = STORAGE / rel
    try:
        path.resolve().relative_to(STORAGE.resolve())
    except ValueError:
        return None
    return path


def _unlink_if_file(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
    except OSError:
        pass


def delete_all_project_storage(project: Project) -> None:
    """
    Remove workspace extract, attachments folder, cover/demo files referenced by URLs,
    and any other media files named `{project_id}_*` under storage/covers and storage/videos.

    Safe to call if some paths are missing. Does not touch the database.
    """
    pid = project.id
    pid_str = str(pid)

    workspace_dir = STORAGE / f"project_{pid}"
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir, ignore_errors=True)

    attachments_dir = STORAGE / "attachments" / pid_str
    if attachments_dir.exists():
        shutil.rmtree(attachments_dir, ignore_errors=True)

    for url in (project.cover_image_url, project.demo_video_url):
        fp = _file_under_storage_from_url(url)
        if fp is not None:
            _unlink_if_file(fp)

    covers = STORAGE / "covers"
    videos = STORAGE / "videos"
    if covers.is_dir():
        for f in covers.glob(f"{pid_str}_*"):
            _unlink_if_file(f)
    if videos.is_dir():
        for f in videos.glob(f"{pid_str}_*"):
            _unlink_if_file(f)
