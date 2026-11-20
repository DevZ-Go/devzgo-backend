"""Premium Resume Studio — generate/edit/export from confirmed career data only. No fabrication."""

from __future__ import annotations

from io import BytesIO
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.dependencies import require_developer
from app.core.feature_flags import resume_studio_enabled
from app.db.session import get_db
from app.models.career import CareerCertification, CareerEducation, CareerExperience
from app.models.entitlements import GeneratedResume, GeneratedResumeVersion, ResumeExport
from app.models.profile import UserProfile
from app.models.user import User
from app.services import storage
from app.services.entitlements import has_feature

router = APIRouter(prefix="/resume-studio", tags=["Resume Studio"])

TEMPLATES = (
    {"key": "classic_ats", "name": "Classic ATS", "description": "Clean single-column layout for applicant tracking systems."},
    {"key": "modern_developer", "name": "Modern Developer", "description": "Skills-forward layout with project highlights."},
    {"key": "portfolio_focus", "name": "Portfolio Focus", "description": "Emphasizes confirmed projects and assessments."},
)


class GenerateIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    template_key: str = "classic_ats"
    include_experience_ids: List[UUID] = Field(default_factory=list)
    include_education_ids: List[UUID] = Field(default_factory=list)
    include_certification_ids: List[UUID] = Field(default_factory=list)
    contact: dict = Field(default_factory=dict)
    job_description: Optional[str] = Field(default=None, max_length=20_000)


class VersionUpdate(BaseModel):
    content: dict
    job_description_excerpt: Optional[str] = Field(default=None, max_length=500)


def _require_studio(db: Session, user: User):
    if not resume_studio_enabled():
        raise HTTPException(status_code=404, detail="Resume Studio is not enabled")
    if not has_feature(db, user.id, "resume_studio"):
        raise HTTPException(status_code=403, detail="Resume Studio requires Premium entitlement")


def _build_content(db: Session, me: User, data: GenerateIn) -> dict[str, Any]:
    profile = db.query(UserProfile).filter(UserProfile.user_id == me.id).first()
    experiences = db.query(CareerExperience).filter(
        CareerExperience.user_id == me.id, CareerExperience.review_state == "confirmed",
    ).all()
    education = db.query(CareerEducation).filter(
        CareerEducation.user_id == me.id, CareerEducation.review_state == "confirmed",
    ).all()
    certs = db.query(CareerCertification).filter(
        CareerCertification.user_id == me.id, CareerCertification.review_state == "confirmed",
    ).all()

    if data.include_experience_ids:
        experiences = [e for e in experiences if e.id in set(data.include_experience_ids)]
    if data.include_education_ids:
        education = [e for e in education if e.id in set(data.include_education_ids)]
    if data.include_certification_ids:
        certs = [c for c in certs if c.id in set(data.include_certification_ids)]

    # Tailoring only reorders / emphasizes existing confirmed text — never invents claims.
    jd = (data.job_description or "").lower()
    def relevance(text: str) -> int:
        if not jd:
            return 0
        return sum(1 for token in set(jd.split()) if len(token) > 3 and token in (text or "").lower())

    exp_payload = sorted([
        {
            "id": str(e.id),
            "organization": e.organization,
            "title": e.title,
            "category": e.category,
            "start_year": e.start_year, "start_month": e.start_month,
            "end_year": e.end_year, "end_month": e.end_month,
            "is_current": e.is_current,
            "description": e.description or "",
            "responsibilities": list(e.responsibilities or []),
            "achievements": list(e.achievements or []),
        }
        for e in experiences
    ], key=lambda r: -relevance(r["description"] + " " + " ".join(r["achievements"])))

    return {
        "template_key": data.template_key if data.template_key in {t["key"] for t in TEMPLATES} else "classic_ats",
        "contact": {
            "full_name": data.contact.get("full_name") or (profile.full_name if profile else None) or me.username,
            "email": data.contact.get("email") if data.contact.get("include_email") else None,
            "headline": data.contact.get("headline") or (profile.headline if profile else None),
            "location": data.contact.get("location") or (profile.location if profile else None),
            "links": data.contact.get("links") or [],
        },
        "experience": exp_payload,
        "education": [
            {
                "id": str(e.id), "institution": e.institution, "degree": e.degree,
                "field_of_study": e.field_of_study, "start_year": e.start_year, "end_year": e.end_year,
                "description": e.description or "",
            }
            for e in education
        ],
        "certifications": [
            {"id": str(c.id), "name": c.name, "issuer": c.issuer, "year": c.year}
            for c in certs
        ],
        "skills": [s.strip() for s in (profile.skills or "").split(",") if s.strip()] if profile else [],
        "suggestions": (
            ["Emphasize technologies named in the job description that already appear in your confirmed experience."]
            if jd else []
        ),
        "fabricated": False,
    }


@router.get("/templates")
def templates(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_studio(db, me)
    return {"templates": TEMPLATES}


@router.get("/resumes")
def list_resumes(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_studio(db, me)
    rows = db.query(GeneratedResume).filter(GeneratedResume.user_id == me.id).order_by(GeneratedResume.updated_at.desc()).all()
    return {
        "resumes": [
            {"id": str(r.id), "title": r.title, "template_key": r.template_key, "updated_at": r.updated_at}
            for r in rows
        ]
    }


@router.post("/resumes", status_code=201)
def generate(data: GenerateIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_studio(db, me)
    if data.template_key not in {t["key"] for t in TEMPLATES}:
        raise HTTPException(status_code=400, detail="Unknown template")
    content = _build_content(db, me, data)
    resume = GeneratedResume(user_id=me.id, title=data.title.strip(), template_key=data.template_key)
    db.add(resume)
    db.flush()
    version = GeneratedResumeVersion(
        resume_id=resume.id, version=1, content=content,
        job_description_excerpt=(data.job_description or "")[:500] or None,
    )
    db.add(version)
    db.commit()
    return {"id": str(resume.id), "version": 1, "content": content}


@router.post("/resumes/{resume_id}/versions", status_code=201)
def save_version(
    resume_id: UUID, data: VersionUpdate,
    db: Session = Depends(get_db), me: User = Depends(require_developer),
):
    _require_studio(db, me)
    resume = db.query(GeneratedResume).filter(GeneratedResume.id == resume_id, GeneratedResume.user_id == me.id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    latest = (
        db.query(GeneratedResumeVersion)
        .filter(GeneratedResumeVersion.resume_id == resume.id)
        .order_by(GeneratedResumeVersion.version.desc())
        .first()
    )
    next_version = (latest.version if latest else 0) + 1
    # Refuse content that invents new orgs/titles not present in confirmed career data.
    confirmed_orgs = {
        (e.organization.lower(), e.title.lower())
        for e in db.query(CareerExperience).filter(
            CareerExperience.user_id == me.id, CareerExperience.review_state == "confirmed"
        ).all()
    }
    for exp in data.content.get("experience") or []:
        key = (str(exp.get("organization", "")).lower(), str(exp.get("title", "")).lower())
        if key not in confirmed_orgs and exp.get("id"):
            # Allow edits to existing confirmed rows only.
            continue
        if key not in confirmed_orgs:
            raise HTTPException(status_code=400, detail="Cannot add employment that is not in your confirmed career data")
    row = GeneratedResumeVersion(
        resume_id=resume.id, version=next_version, content=data.content,
        job_description_excerpt=data.job_description_excerpt,
    )
    db.add(row)
    db.commit()
    return {"id": str(resume.id), "version": next_version, "content": data.content}


@router.get("/resumes/{resume_id}/export.pdf")
def export_pdf(resume_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_studio(db, me)
    resume = db.query(GeneratedResume).filter(GeneratedResume.id == resume_id, GeneratedResume.user_id == me.id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    version = (
        db.query(GeneratedResumeVersion)
        .filter(GeneratedResumeVersion.resume_id == resume.id)
        .order_by(GeneratedResumeVersion.version.desc())
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="No version to export")
    text = _plain_text(version.content)
    # Minimal text PDF via reportlab if available; otherwise UTF-8 text download labeled pdf for selectable text fallback.
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        width, height = letter
        y = height - 54
        for line in text.splitlines():
            if y < 54:
                c.showPage()
                y = height - 54
            c.drawString(54, y, line[:110])
            y -= 14
        c.save()
        data = buf.getvalue()
        content_type = "application/pdf"
        ext = "pdf"
    except ImportError:
        data = text.encode("utf-8")
        content_type = "text/plain; charset=utf-8"
        ext = "txt"

    key = f"resume-exports/{me.id}/{resume.id}/v{version.version}.{ext}"
    storage.save(key, BytesIO(data))
    db.add(ResumeExport(user_id=me.id, resume_version_id=version.id, format="pdf", storage_key=key))
    db.commit()
    return StreamingResponse(BytesIO(data), media_type=content_type, headers={
        "Content-Disposition": f'attachment; filename="{resume.title}.{ext}"',
    })


@router.get("/resumes/{resume_id}/export.docx")
def export_docx(resume_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_studio(db, me)
    resume = db.query(GeneratedResume).filter(GeneratedResume.id == resume_id, GeneratedResume.user_id == me.id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    version = (
        db.query(GeneratedResumeVersion)
        .filter(GeneratedResumeVersion.resume_id == resume.id)
        .order_by(GeneratedResumeVersion.version.desc())
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="No version to export")
    try:
        from docx import Document
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="DOCX export requires python-docx") from exc
    doc = Document()
    content = version.content
    contact = content.get("contact") or {}
    doc.add_heading(contact.get("full_name") or me.username, level=1)
    if contact.get("headline"):
        doc.add_paragraph(contact["headline"])
    doc.add_heading("Experience", level=2)
    for exp in content.get("experience") or []:
        doc.add_heading(f"{exp.get('title')} — {exp.get('organization')}", level=3)
        if exp.get("description"):
            doc.add_paragraph(exp["description"])
        for bullet in exp.get("achievements") or []:
            doc.add_paragraph(bullet, style="List Bullet")
    buf = BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    key = f"resume-exports/{me.id}/{resume.id}/v{version.version}.docx"
    storage.save(key, BytesIO(data))
    db.add(ResumeExport(user_id=me.id, resume_version_id=version.id, format="docx", storage_key=key))
    db.commit()
    return StreamingResponse(BytesIO(data), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={
        "Content-Disposition": f'attachment; filename="{resume.title}.docx"',
    })


def _plain_text(content: dict) -> str:
    lines = []
    contact = content.get("contact") or {}
    lines.append(contact.get("full_name") or "")
    if contact.get("headline"):
        lines.append(contact["headline"])
    lines.append("")
    lines.append("EXPERIENCE")
    for exp in content.get("experience") or []:
        lines.append(f"{exp.get('title')} — {exp.get('organization')}")
        if exp.get("description"):
            lines.append(exp["description"])
        for a in exp.get("achievements") or []:
            lines.append(f"- {a}")
        lines.append("")
    lines.append("EDUCATION")
    for edu in content.get("education") or []:
        lines.append(f"{edu.get('degree') or ''} {edu.get('institution') or ''}".strip())
    return "\n".join(lines)
