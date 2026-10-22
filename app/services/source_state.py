"""Where a project's files come from and whether the stored copy of them is current.

GitHub is the source of truth for a linked repository; the stored copy (local disk or the bucket)
is a snapshot of one branch at one commit, or of a ZIP upload. The sync state says whether the
last attempt to replace that copy finished, so a failed upload or import is visible instead of
leaving the page silently out of date.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.project import Project

PENDING, OK, FAILED = "pending", "ok", "failed"
GENERIC_FAILURE = "The source could not be processed. Try again, or upload a ZIP."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def mark(db: Session, project_id: UUID, state: str, error: str | None = None) -> None:
    """Record the copy's state in its own commit, so it survives a rolled-back upload."""
    db.query(Project).filter(Project.id == project_id).update(
        {"sync_state": state, "sync_error": error[:300] if error else None, "sync_updated_at": _now()},
        synchronize_session=False,
    )
    db.commit()


def record_snapshot(project: Project, origin: str, ref: str | None = None, commit_sha: str | None = None) -> None:
    """The stored copy now holds a ZIP upload, or one GitHub branch at one commit. Committed by the caller."""
    now = _now()
    project.snapshot_origin = origin
    project.snapshot_ref = ref if origin == "github" else None
    project.snapshot_commit_sha = commit_sha if origin == "github" else None
    project.snapshot_synced_at = now
    project.sync_state, project.sync_error, project.sync_updated_at = OK, None, now


def summary(project: Project, access: dict | None) -> dict:
    team = bool(access and (access.get("owner") or access.get("member")))
    snapshot = None
    if project.snapshot_origin:
        snapshot = {
            "origin": project.snapshot_origin,
            "ref": project.snapshot_ref,
            "commit_sha": project.snapshot_commit_sha,
            "synced_at": project.snapshot_synced_at,
        }
    return {
        "kind": project.source_kind or "zip",
        "repository": project.github_full_name,
        "default_branch": project.github_default_branch,
        "snapshot": snapshot,
        "sync_state": project.sync_state,
        "sync_error": project.sync_error if team else None,
        "sync_updated_at": project.sync_updated_at,
    }
