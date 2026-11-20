"""Best-effort enqueue after ZIP/GitHub install. Never blocks or fails the upload."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.feature_flags import project_intelligence_enabled
from app.models.project import Project
from app.services.project_intelligence.jobs import enqueue_analyze
from app.services.project_intelligence.preferences import analysis_allowed
from app.services.project_intelligence.selectors import select_files

log = logging.getLogger("project_intelligence")


def maybe_enqueue_after_install(db: Session, project: Project, workspace_root, *, requested_by: UUID | None = None) -> None:
    """Call after successful workspace install. Deterministic only; never invokes external AI."""
    if not project_intelligence_enabled():
        return
    try:
        if not analysis_allowed(db, project.id, project.owner_id):
            return
        from app.services.project_intelligence.quota import get_pi_settings
        settings = get_pi_settings(db)
        if not settings.get("auto_analyze_on_upload", True):
            return
        # Deterministic analyze never consumes AI package entitlement.
        selection = select_files(workspace_root)
        job = enqueue_analyze(
            db,
            project,
            requested_by=requested_by or project.owner_id,
            content_hash=selection.content_hash,
            file_count=len(selection.files),
            byte_total=selection.total_bytes,
            force=False,
        )
        db.commit()
        # Prefer async worker; if job is new+queued, try lightweight inline so UX sees progress soon.
        if job.status == "queued":
            from app.services.project_intelligence.pipeline import process_analyze_job
            process_analyze_job(db, job)
    except Exception:  # noqa: BLE001 — upload must succeed even if intelligence fails
        log.exception("project intelligence enqueue failed for %s", project.id)
        try:
            db.rollback()
        except Exception:
            pass
