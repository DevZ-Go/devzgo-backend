"""Run analyze / generate jobs end-to-end."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.project_intelligence import ProjectAnalysisJob
from app.services.project_intelligence.analyzer import analyze_tree
from app.services.project_intelligence.jobs import get_or_create_snapshot, resolve_revision
from app.services.project_intelligence.persist import replace_claims, save_insights
from app.services.project_intelligence.preferences import get_project_settings
from app.services.project_intelligence.selectors import select_files
from app.services.project_intelligence.workspace import open_workspace

log = logging.getLogger("project_intelligence")


def process_analyze_job(db: Session, job: ProjectAnalysisJob) -> None:
    project = db.query(Project).filter(Project.id == job.project_id).first()
    if not project:
        job.status = "failed"
        job.error = "Project not found"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    if job.cancel_requested:
        job.status = "cancelled"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    job.status = "analyzing"
    job.progress = {"stage": "analyzing"}
    job.attempt_count = (job.attempt_count or 0) + 1
    db.commit()

    try:
        with open_workspace(project.id) as root:
            if job.cancel_requested:
                job.status = "cancelled"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            selection = select_files(root)
            snapshot = get_or_create_snapshot(
                db,
                project,
                content_hash=selection.content_hash,
                file_count=len(selection.files),
                byte_total=selection.total_bytes,
            )
            job.snapshot_id = snapshot.id
            db.commit()

            bundle = analyze_tree(root)
            if job.cancel_requested:
                job.status = "cancelled"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            revision = resolve_revision(project, bundle.content_hash)
            claim_rows = replace_claims(
                db,
                project_id=project.id,
                snapshot_id=snapshot.id,
                job_id=job.id,
                revision=revision,
                claims=bundle.claims,
            )
            settings = get_project_settings(db, project.id)
            save_insights(
                db,
                project_id=project.id,
                snapshot_id=snapshot.id,
                job_id=job.id,
                title=project.title or "Project",
                bundle=bundle,
                claim_ids=[str(c.id) for c in claim_rows],
                audience=settings.insight_audience or "public",
            )
            job.coverage_pct = bundle.coverage_pct
            job.status = "incomplete" if bundle.truncated else "ready"
            job.progress = {
                "stage": "done",
                "claims": len(claim_rows),
                "coverage_pct": bundle.coverage_pct,
                "truncated": bundle.truncated,
            }
            job.completed_at = datetime.now(timezone.utc)
            job.error = None
            db.commit()
    except FileNotFoundError:
        job.status = "failed"
        job.error = "No stored source to analyse. Upload a ZIP or import from GitHub first."
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("analyze job %s failed", job.id)
        job.status = "failed"
        job.error = str(exc)[:500]
        job.completed_at = datetime.now(timezone.utc)
        db.commit()


def process_generate_job(db: Session, job: ProjectAnalysisJob) -> None:
    from app.services.project_intelligence.ai_generate import run_generate
    from app.services.project_intelligence.quota import release_generation
    from app.models.project_intelligence import PiGenerationRecord

    project = db.query(Project).filter(Project.id == job.project_id).first()
    if not project:
        job.status = "failed"
        job.error = "Project not found"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    if job.cancel_requested:
        job.status = "cancelled"
        job.completed_at = datetime.now(timezone.utc)
        _release_job_reservation(db, job, "cancelled")
        db.commit()
        return

    prior = dict(job.progress or {})
    job.status = "generating"
    job.progress = {**prior, "stage": "generating"}
    job.attempt_count = (job.attempt_count or 0) + 1
    db.commit()

    try:
        parent_id = None
        raw_parent = prior.get("parent_draft_id")
        if raw_parent:
            try:
                parent_id = UUID(str(raw_parent))
            except ValueError:
                parent_id = None
        draft = run_generate(db, project, job, parent_draft_id=parent_id)
        if prior.get("community"):
            from app.services.project_intelligence.ai_generate import approve_draft

            approve_draft(
                db,
                project,
                draft,
                apply_fields={
                    "publish_simple": True,
                    "show_detailed_on_page": bool(draft.technical_explanation),
                },
                user_id=job.requested_by,
            )
        job.status = "ready"
        job.progress = {**prior, "stage": "done", "draft_id": str(draft.id), "community": bool(prior.get("community"))}
        job.completed_at = datetime.now(timezone.utc)
        job.error = None
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("generate job %s failed", job.id)
        job.status = "failed"
        job.error = str(exc)[:500]
        job.completed_at = datetime.now(timezone.utc)
        try:
            rid = prior.get("generation_record_id")
            if rid:
                rec = db.query(PiGenerationRecord).filter(PiGenerationRecord.id == UUID(str(rid))).first()
                if rec and rec.status == "reserved":
                    release_generation(db, rec, error=str(exc))
        except Exception:
            log.exception("failed to release generation reservation for job %s", job.id)
        db.commit()


def _release_job_reservation(db: Session, job: ProjectAnalysisJob, reason: str) -> None:
    from app.services.project_intelligence.quota import release_generation
    from app.models.project_intelligence import PiGenerationRecord

    rid = (job.progress or {}).get("generation_record_id")
    if not rid:
        return
    try:
        rec = db.query(PiGenerationRecord).filter(PiGenerationRecord.id == UUID(str(rid))).first()
        if rec and rec.status == "reserved":
            release_generation(db, rec, error=reason)
    except Exception:
        log.exception("reservation release failed for job %s", job.id)


def process_job(db: Session, job: ProjectAnalysisJob) -> None:
    if job.job_kind == "generate":
        process_generate_job(db, job)
    else:
        process_analyze_job(db, job)


def quick_content_hash(db: Session, project_id: UUID) -> tuple[str, int, int]:
    """Compute selection hash without full analyze (for enqueue)."""
    with open_workspace(project_id) as root:
        selection = select_files(root)
        return selection.content_hash, len(selection.files), selection.total_bytes
