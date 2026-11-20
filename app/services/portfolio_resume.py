"""Use the same PDF stored on the portfolio profile for career import / badge evidence."""

from __future__ import annotations

import uuid
from io import BytesIO

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.models.career import ResumeImportJob
from app.models.profile import UserProfile
from app.models.user import User
from app.services import storage
from app.services.resume_extract import detect_format, validate_magic


def portfolio_storage_key(resume_url: str | None) -> str | None:
    """Map /storage/resumes/<name> → resumes/<name>."""
    if not resume_url:
        return None
    path = resume_url.strip()
    if path.startswith("/storage/"):
        return path[len("/storage/") :]
    if path.startswith("resumes/"):
        return path
    return None


def create_import_from_portfolio(db: Session, me: User) -> ResumeImportJob:
    """Create and process a resume import job from the developer's portfolio PDF."""
    from app.routes.career_imports import process_job

    profile = db.query(UserProfile).filter(UserProfile.user_id == me.id).first()
    key = portfolio_storage_key(profile.resume_url if profile else None)
    if not key:
        raise HTTPException(status_code=404, detail="Upload a PDF resume on your portfolio first")
    try:
        raw = storage.read(key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="Portfolio resume file was not found in storage") from exc

    filename = key.rsplit("/", 1)[-1] or "portfolio-resume.pdf"
    fmt = detect_format(filename, "application/pdf") or "pdf"
    if not validate_magic(fmt, raw):
        raise HTTPException(status_code=400, detail="Portfolio resume is not a valid PDF")

    job_id = uuid.uuid4()
    # Keep a career-imports copy so import jobs stay independent of later portfolio replaces.
    import_key = f"career-imports/{me.id}/{job_id}/{filename}"
    storage.save(import_key, BytesIO(raw))
    job = ResumeImportJob(
        id=job_id,
        user_id=me.id,
        storage_key=import_key,
        original_filename=filename[:255],
        format=fmt,
        status="queued",
    )
    db.add(job)
    db.flush()
    process_job(db, job, raw)
    db.commit()
    return (
        db.query(ResumeImportJob)
        .options(selectinload(ResumeImportJob.items))
        .filter(ResumeImportJob.id == job_id)
        .one()
    )


def sync_after_portfolio_upload(db: Session, me: User, storage_key: str, filename: str) -> ResumeImportJob | None:
    """Best-effort: queue extraction after portfolio PDF upload so badges can use the same file."""
    from app.core.feature_flags import resume_import_enabled
    from app.routes.career_imports import process_job

    if not resume_import_enabled():
        return None
    try:
        raw = storage.read(storage_key)
    except Exception:
        return None
    fmt = detect_format(filename, "application/pdf") or "pdf"
    if not validate_magic(fmt, raw):
        return None
    job_id = uuid.uuid4()
    import_key = f"career-imports/{me.id}/{job_id}/{filename}"
    try:
        storage.save(import_key, BytesIO(raw))
    except Exception:
        return None
    job = ResumeImportJob(
        id=job_id,
        user_id=me.id,
        storage_key=import_key,
        original_filename=(filename or "portfolio-resume.pdf")[:255],
        format=fmt,
        status="queued",
    )
    db.add(job)
    db.flush()
    process_job(db, job, raw)
    return job
