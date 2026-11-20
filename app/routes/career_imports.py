"""Resume import jobs: upload, process, review, confirm — private until approved."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import require_developer
from app.core.feature_flags import resume_import_enabled
from app.db.session import get_db
from app.models.career import (
    CareerCertification,
    CareerEducation,
    CareerExperience,
    ResumeExtractedItem,
    ResumeImportJob,
)
from app.models.user import User
from app.schemas.career import ExtractedItemOut, ImportConfirmIn, ImportJobOut
from app.services import storage
from app.services.resume_extract import build_extracted_items, detect_format, validate_magic

router = APIRouter(prefix="/career/imports", tags=["Career Imports"])

MAX_BYTES = 8 * 1024 * 1024


def _publish_type(item: ResumeExtractedItem, save_as: str | None) -> str:
    if save_as in ("experience", "education", "skill", "certification"):
        return save_as
    return item.item_type


def _enabled():
    if not resume_import_enabled():
        raise HTTPException(status_code=404, detail="Resume import is not enabled")


def _serialize(job: ResumeImportJob) -> ImportJobOut:
    return ImportJobOut(
        id=job.id,
        original_filename=job.original_filename,
        format=job.format,
        status=job.status,
        error=job.error,
        analysis=job.analysis,
        items=[
            ExtractedItemOut(
                id=i.id, item_type=i.item_type, field_path=i.field_path,
                extracted_value=i.extracted_value, normalized_value=i.normalized_value,
                source_location=i.source_location, confidence=i.confidence,
                review_state=i.review_state,  # type: ignore[arg-type]
            )
            for i in job.items
        ],
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


def process_job(db: Session, job: ResumeImportJob, raw: bytes) -> None:
    job.status = "processing"
    job.attempt_count = (job.attempt_count or 0) + 1
    db.flush()
    try:
        items, analysis = build_extracted_items(job.format, raw)
        job.items.clear()
        for item in items:
            job.items.append(ResumeExtractedItem(
                item_type=item["item_type"],
                field_path=item["field_path"],
                extracted_value=item["extracted_value"],
                normalized_value=item.get("normalized_value") or item["extracted_value"],
                source_location=item.get("source_location"),
                confidence=item.get("confidence", "medium"),
                review_state="pending_review",
            ))
        skills = [
            str((item.get("extracted_value") or {}).get("name") or "")
            for item in items if item.get("item_type") == "skill"
        ]
        from app.services.career_evidence import enrich_free_analysis
        job.analysis = enrich_free_analysis(db, job.user_id, analysis, skills)
        job.status = "ready"
        job.error = None
        job.completed_at = datetime.now(timezone.utc)
    except Exception as exc:  # noqa: BLE001 — surface as job failure
        job.status = "failed"
        job.error = str(exc)[:500]
        job.completed_at = datetime.now(timezone.utc)


@router.post("", response_model=ImportJobOut, status_code=201)
async def upload_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    _enabled()
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 8 MB")
    fmt = detect_format(file.filename or "", file.content_type)
    if not fmt:
        raise HTTPException(status_code=400, detail="Supported formats: PDF, DOCX, LinkedIn Positions CSV")
    if not validate_magic(fmt, raw):
        raise HTTPException(status_code=400, detail="File contents do not match the declared type")

    job_id = uuid.uuid4()
    key = f"career-imports/{me.id}/{job_id}/{file.filename or 'resume'}"
    storage.save(key, BytesIO(raw))
    job = ResumeImportJob(
        id=job_id, user_id=me.id, storage_key=key,
        original_filename=(file.filename or "resume")[:255],
        format=fmt, status="queued",
    )
    db.add(job)
    db.flush()
    # Process inline for MVP durability of the row; a worker can reclaim queued/failed jobs later.
    process_job(db, job, raw)
    db.commit()
    job = db.query(ResumeImportJob).options(selectinload(ResumeImportJob.items)).filter(ResumeImportJob.id == job_id).one()
    return _serialize(job)


@router.get("", response_model=List[ImportJobOut])
def list_imports(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _enabled()
    rows = (
        db.query(ResumeImportJob)
        .options(selectinload(ResumeImportJob.items))
        .filter(ResumeImportJob.user_id == me.id)
        .order_by(ResumeImportJob.created_at.desc())
        .limit(20)
        .all()
    )
    return [_serialize(r) for r in rows]


@router.post("/from-portfolio", response_model=ImportJobOut, status_code=201)
def import_from_portfolio(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    """Reuse the PDF already on the portfolio — one resume for portfolio, career, and badges."""
    _enabled()
    from app.services.portfolio_resume import create_import_from_portfolio
    job = create_import_from_portfolio(db, me)
    return _serialize(job)


@router.get("/{job_id}", response_model=ImportJobOut)
def get_import(job_id: uuid.UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _enabled()
    job = (
        db.query(ResumeImportJob)
        .options(selectinload(ResumeImportJob.items))
        .filter(ResumeImportJob.id == job_id, ResumeImportJob.user_id == me.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Import not found")
    return _serialize(job)


@router.post("/{job_id}/confirm", response_model=ImportJobOut)
def confirm_import(
    job_id: uuid.UUID,
    data: ImportConfirmIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    """Publish only confirmed items. Never silently overwrite existing edited career records."""
    _enabled()
    job = (
        db.query(ResumeImportJob)
        .options(selectinload(ResumeImportJob.items))
        .filter(ResumeImportJob.id == job_id, ResumeImportJob.user_id == me.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Import not found")
    if job.status not in ("ready", "confirmed"):
        raise HTTPException(status_code=409, detail="Import is not ready for review")

    by_id = {i.id: i for i in job.items}
    skill_names: list[str] = []
    for review in data.items:
        item = by_id.get(review.item_id)
        if not item:
            continue
        item.review_state = review.review_state
        if review.normalized_value is not None:
            item.normalized_value = review.normalized_value
        if review.review_state != "confirmed":
            continue
        value = dict(review.normalized_value or item.normalized_value or item.extracted_value or {})
        publish_as = _publish_type(item, review.save_as)

        if publish_as == "experience":
            # Do not overwrite an existing manually edited experience with the same org+title.
            existing = (
                db.query(CareerExperience)
                .filter(
                    CareerExperience.user_id == me.id,
                    CareerExperience.organization.ilike(str(value.get("organization") or "")),
                    CareerExperience.title.ilike(str(value.get("title") or "")),
                    CareerExperience.source == "manual",
                )
                .first()
            )
            if existing:
                continue
            db.add(CareerExperience(
                user_id=me.id,
                organization=str(value.get("organization") or "Unknown")[:200],
                title=str(value.get("title") or "Role")[:200],
                category=str(value.get("category") or "full_time"),
                start_year=value.get("start_year"),
                start_month=value.get("start_month"),
                end_year=value.get("end_year"),
                end_month=value.get("end_month"),
                is_current=bool(value.get("is_current")),
                location=value.get("location"),
                description=value.get("description"),
                responsibilities=value.get("responsibilities") or [],
                achievements=value.get("achievements") or [],
                source="resume_import",
                provenance={"import_job_id": str(job.id), "item_id": str(item.id)},
                review_state="confirmed",
                visibility=review.visibility,
                import_job_id=job.id,
            ))
        elif publish_as == "education":
            institution = str(value.get("institution") or "").strip()
            if not institution:
                continue
            existing_edu = (
                db.query(CareerEducation)
                .filter(
                    CareerEducation.user_id == me.id,
                    CareerEducation.institution.ilike(institution),
                    CareerEducation.source == "manual",
                )
                .first()
            )
            if existing_edu:
                continue
            db.add(CareerEducation(
                user_id=me.id,
                institution=institution[:200],
                degree=(str(value.get("degree")).strip()[:200] if value.get("degree") else None),
                field_of_study=(str(value.get("field_of_study")).strip()[:200] if value.get("field_of_study") else None),
                start_year=value.get("start_year"),
                start_month=value.get("start_month"),
                end_year=value.get("end_year"),
                end_month=value.get("end_month"),
                is_current=bool(value.get("is_current")),
                description=value.get("description"),
                source="resume_import",
                review_state="confirmed",
                visibility=review.visibility,
            ))
        elif publish_as == "certification":
            name = str(value.get("name") or value.get("title") or "").strip()
            if not name:
                continue
            existing_cert = (
                db.query(CareerCertification)
                .filter(
                    CareerCertification.user_id == me.id,
                    CareerCertification.name.ilike(name),
                    CareerCertification.source == "manual",
                )
                .first()
            )
            if existing_cert:
                continue
            issuer = value.get("issuer")
            db.add(CareerCertification(
                user_id=me.id,
                name=name[:200],
                issuer=(str(issuer).strip()[:200] if issuer else None),
                year=value.get("year") or value.get("end_year") or value.get("start_year"),
                month=value.get("month") or value.get("end_month") or value.get("start_month"),
                credential_url=(str(value.get("credential_url")).strip()[:500] if value.get("credential_url") else None),
                source="resume_import",
                review_state="confirmed",
                visibility=review.visibility,
            ))
        elif publish_as == "skill":
            name = str(value.get("name") or "").strip()
            if name:
                skill_names.append(name)

    if skill_names:
        from app.services.career_evidence import sync_resume_skills
        sync_resume_skills(db, me.id, skill_names, import_job_id=str(job.id), audience="connections")

    job.status = "confirmed"
    db.commit()
    from app.services.career_evidence import after_career_change
    after_career_change(db, me.id)
    job = db.query(ResumeImportJob).options(selectinload(ResumeImportJob.items)).filter(ResumeImportJob.id == job_id).one()
    return _serialize(job)
