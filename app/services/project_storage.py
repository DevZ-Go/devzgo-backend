"""
Delete stored assets for a project (workspace extract, original ZIP, attachments, cover, demo video).

Used when removing a project so Postgres and storage stay in sync. Keys match how routes
write files: covers/..., videos/..., project_<uuid>/, sources/<uuid>.zip, attachments/<uuid>/.
"""

from __future__ import annotations

from app.models.project import Project
from app.services import storage


def _key_from_url(url: str | None) -> str | None:
    if not url or not url.startswith("/storage/"):
        return None
    key = url.removeprefix("/storage/").lstrip("/")
    return key if key and ".." not in key.split("/") else None


def delete_all_project_storage(project: Project) -> None:
    """Safe to call if some objects are missing. Does not touch the database."""
    pid = project.id
    keys = [f"project_{pid}/", f"attachments/{pid}/", f"sources/{pid}.zip", f"covers/{pid}_", f"videos/{pid}_"]
    keys += [k for k in map(_key_from_url, (project.cover_image_url, project.demo_video_url)) if k]
    for key in keys:
        storage.delete_prefix(key)
