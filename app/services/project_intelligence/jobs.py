"""Durable Project Intelligence jobs with idempotency and cache reuse."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.project_intelligence import (
    ProjectAnalysisJob,
    ProjectAiDescriptionDraft,
    ProjectEvidenceClaim,
    ProjectPlainInsight,
    ProjectSourceSnapshot,
)
from app.services.project_intelligence.constants import ANALYZER_VERSION, policy_hash


def make_idempotency_key(
    project_id: UUID,
    *,
    revision: str,
    analyzer_version: str = ANALYZER_VERSION,
    policy: str | None = None,
    job_kind: str = "analyze",
) -> str:
    pol = policy or policy_hash()
    raw = f"{project_id}|{revision}|{analyzer_version}|{pol}|{job_kind}"
    return hashlib.sha256(raw.encode()).hexdigest()


def latest_ready_job(db: Session, project_id: UUID, idempotency_key: str) -> ProjectAnalysisJob | None:
    return (
        db.query(ProjectAnalysisJob)
        .filter(
            ProjectAnalysisJob.project_id == project_id,
            ProjectAnalysisJob.idempotency_key == idempotency_key,
            ProjectAnalysisJob.status.in_(("ready", "incomplete")),
        )
        .order_by(ProjectAnalysisJob.created_at.desc())
        .first()
    )


def find_open_job(db: Session, project_id: UUID, job_kind: str = "analyze") -> ProjectAnalysisJob | None:
    return (
        db.query(ProjectAnalysisJob)
        .filter(
            ProjectAnalysisJob.project_id == project_id,
            ProjectAnalysisJob.job_kind == job_kind,
            ProjectAnalysisJob.status.in_(("queued", "analyzing", "generating")),
        )
        .order_by(ProjectAnalysisJob.created_at.desc())
        .first()
    )


def resolve_revision(project: Project, content_hash: str) -> str:
    if project.source_kind == "github" and project.snapshot_commit_sha:
        return project.snapshot_commit_sha[:40]
    return content_hash[:40]


def get_or_create_snapshot(
    db: Session,
    project: Project,
    *,
    content_hash: str,
    file_count: int,
    byte_total: int,
    meta: dict | None = None,
) -> ProjectSourceSnapshot:
    revision = resolve_revision(project, content_hash)
    existing = (
        db.query(ProjectSourceSnapshot)
        .filter(
            ProjectSourceSnapshot.project_id == project.id,
            ProjectSourceSnapshot.revision == revision,
            ProjectSourceSnapshot.content_hash == content_hash,
        )
        .first()
    )
    if existing:
        return existing
    row = ProjectSourceSnapshot(
        project_id=project.id,
        source_kind=project.source_kind or "zip",
        revision=revision,
        content_hash=content_hash,
        file_count=file_count,
        byte_total=byte_total,
        meta=meta or {},
    )
    db.add(row)
    db.flush()
    return row


def enqueue_analyze(
    db: Session,
    project: Project,
    *,
    requested_by: UUID | None,
    content_hash: str,
    file_count: int = 0,
    byte_total: int = 0,
    force: bool = False,
) -> ProjectAnalysisJob:
    revision = resolve_revision(project, content_hash)
    key = make_idempotency_key(project.id, revision=revision)
    if not force:
        cached = latest_ready_job(db, project.id, key)
        if cached:
            return cached
        open_job = (
            db.query(ProjectAnalysisJob)
            .filter(ProjectAnalysisJob.idempotency_key == key, ProjectAnalysisJob.status.in_(("queued", "analyzing")))
            .first()
        )
        if open_job:
            return open_job

    snapshot = get_or_create_snapshot(
        db, project, content_hash=content_hash, file_count=file_count, byte_total=byte_total
    )
    # If force, make a unique key suffix so UniqueConstraint allows a new row
    idem = key if not force else f"{key}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    job = ProjectAnalysisJob(
        project_id=project.id,
        snapshot_id=snapshot.id,
        requested_by=requested_by,
        status="queued",
        job_kind="analyze",
        idempotency_key=idem,
        analyzer_version=ANALYZER_VERSION,
        policy_hash=policy_hash(),
        progress={"stage": "queued"},
    )
    db.add(job)
    db.flush()
    return job


def enqueue_generate(
    db: Session,
    project: Project,
    *,
    requested_by: UUID,
    snapshot_id: UUID | None,
    parent_draft_id: UUID | None = None,
    generation_record_id: UUID | None = None,
    output_selection: dict | None = None,
    max_output_tokens: int | None = None,
    idempotency_key: str | None = None,
) -> ProjectAnalysisJob:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    key = idempotency_key or f"generate:{project.id}:{stamp}"
    progress = {
        "stage": "queued",
        "parent_draft_id": str(parent_draft_id) if parent_draft_id else None,
        "generation_record_id": str(generation_record_id) if generation_record_id else None,
        "output_selection": output_selection or {},
        "max_output_tokens": max_output_tokens,
    }
    job = ProjectAnalysisJob(
        project_id=project.id,
        snapshot_id=snapshot_id,
        requested_by=requested_by,
        status="queued",
        job_kind="generate",
        idempotency_key=key,
        analyzer_version=ANALYZER_VERSION,
        policy_hash=policy_hash(),
        progress=progress,
    )
    db.add(job)
    db.flush()
    return job


def request_cancel(db: Session, job: ProjectAnalysisJob) -> ProjectAnalysisJob:
    if job.status in ("ready", "incomplete", "failed", "cancelled"):
        return job
    job.cancel_requested = True
    if job.status == "queued":
        job.status = "cancelled"
        job.completed_at = datetime.now(timezone.utc)
    db.flush()
    return job


def latest_status(db: Session, project_id: UUID) -> ProjectAnalysisJob | None:
    return (
        db.query(ProjectAnalysisJob)
        .filter(ProjectAnalysisJob.project_id == project_id, ProjectAnalysisJob.job_kind == "analyze")
        .order_by(ProjectAnalysisJob.created_at.desc())
        .first()
    )


def latest_insight(db: Session, project_id: UUID) -> ProjectPlainInsight | None:
    return (
        db.query(ProjectPlainInsight)
        .filter(ProjectPlainInsight.project_id == project_id)
        .order_by(ProjectPlainInsight.created_at.desc())
        .first()
    )


def list_claims(db: Session, project_id: UUID, snapshot_id: UUID | None = None) -> list[ProjectEvidenceClaim]:
    q = db.query(ProjectEvidenceClaim).filter(ProjectEvidenceClaim.project_id == project_id)
    if snapshot_id:
        q = q.filter(ProjectEvidenceClaim.snapshot_id == snapshot_id)
    return q.order_by(ProjectEvidenceClaim.created_at.asc()).all()


def list_drafts(db: Session, project_id: UUID) -> list[ProjectAiDescriptionDraft]:
    return (
        db.query(ProjectAiDescriptionDraft)
        .filter(ProjectAiDescriptionDraft.project_id == project_id)
        .order_by(ProjectAiDescriptionDraft.created_at.desc())
        .all()
    )


def clear_cached_findings(db: Session, project_id: UUID) -> None:
    """Owner withdraw: delete insights and claims (drafts retained until explicit discard)."""
    db.query(ProjectPlainInsight).filter(ProjectPlainInsight.project_id == project_id).delete(synchronize_session=False)
    db.query(ProjectEvidenceClaim).filter(ProjectEvidenceClaim.project_id == project_id).delete(synchronize_session=False)
    db.flush()
