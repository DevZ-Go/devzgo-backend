"""Career profile CRUD, duration summary, and field visibility."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, get_current_user_optional, require_developer
from app.core.feature_flags import career_enabled
from app.db.session import get_db
from app.models.career import (
    CareerCertification, CareerEducation, CareerExperience, CareerExperienceProject,
    CareerFieldVisibility, CareerSkillInterval,
)
from app.models.techstack import TechStack
from app.models.user import User
from app.schemas.career import (
    CareerSummary, CertificationIn, CertificationOut, EducationIn, EducationOut,
    ExperienceIn, ExperienceOut, ExperienceUpdate, FieldVisibilityIn, ReorderIn, SkillIntervalOut,
)
from app.services.career_duration import category_totals, skill_totals, span_from_fields, format_months
from app.services.career_privacy import viewer_can_see
from app.services.entitlements import list_for_user

router = APIRouter(prefix="/career", tags=["Career"])


def _require_career():
    if not career_enabled():
        raise HTTPException(status_code=404, detail="Career profiles are not enabled")


def _owner(db: Session, user_id: UUID) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.is_recruiter:
        raise HTTPException(status_code=404, detail="Developer not found")
    return user


def _serialize_experience(row: CareerExperience) -> ExperienceOut:
    span = span_from_fields(
        row.start_year, row.start_month, row.end_year, row.end_month, is_current=row.is_current,
    )
    duration = format_months(
        (span.end - span.start + 1) if span.known and span.start is not None and span.end is not None else None,
        known=span.known,
    )
    intervals = [
        SkillIntervalOut(
            id=i.id, tech_stack_id=i.tech_stack_id,
            tech_name=i.tech_stack.name if i.tech_stack else None,
            start_year=i.start_year, start_month=i.start_month,
            end_year=i.end_year, end_month=i.end_month,
            review_state=i.review_state,  # type: ignore[arg-type]
        )
        for i in row.skill_intervals
    ]
    return ExperienceOut(
        id=row.id, organization=row.organization, title=row.title, category=row.category,  # type: ignore[arg-type]
        start_year=row.start_year, start_month=row.start_month, end_year=row.end_year, end_month=row.end_month,
        is_current=row.is_current, location=row.location, description=row.description,
        responsibilities=list(row.responsibilities or []), achievements=list(row.achievements or []),
        source=row.source, review_state=row.review_state,  # type: ignore[arg-type]
        visibility=row.visibility,  # type: ignore[arg-type]
        sort_order=row.sort_order, skill_intervals=intervals,
        project_ids=[p.project_id for p in row.project_links],
        duration=duration, created_at=row.created_at,
    )


def _set_intervals(db: Session, experience: CareerExperience, intervals: list) -> None:
    experience.skill_intervals.clear()
    db.flush()
    for item in intervals:
        tech = db.query(TechStack).filter(TechStack.id == item.tech_stack_id).first()
        if not tech:
            raise HTTPException(status_code=400, detail=f"Unknown technology id {item.tech_stack_id}")
        experience.skill_intervals.append(CareerSkillInterval(
            tech_stack_id=item.tech_stack_id,
            start_year=item.start_year, start_month=item.start_month,
            end_year=item.end_year, end_month=item.end_month,
        ))


def _set_projects(experience: CareerExperience, project_ids: list[UUID]) -> None:
    experience.project_links.clear()
    for pid in project_ids:
        experience.project_links.append(CareerExperienceProject(project_id=pid))


@router.get("/me", response_model=CareerSummary)
def my_career(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_career()
    return _summary_for(db, me, me)


@router.get("/users/{user_id}", response_model=CareerSummary)
def public_career(
    user_id: UUID,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    _require_career()
    owner = _owner(db, user_id)
    return _summary_for(db, owner, viewer)


def _summary_for(db: Session, owner: User, viewer: Optional[User]) -> CareerSummary:
    own = viewer is not None and viewer.id == owner.id
    experiences = (
        db.query(CareerExperience)
        .options(selectinload(CareerExperience.skill_intervals).selectinload(CareerSkillInterval.tech_stack),
                 selectinload(CareerExperience.project_links))
        .filter(CareerExperience.user_id == owner.id)
        .order_by(CareerExperience.sort_order, CareerExperience.start_year.desc().nullslast())
        .all()
    )
    visible_exp = []
    for row in experiences:
        if not own and row.review_state != "confirmed":
            continue
        if not own and not viewer_can_see(db, owner=owner, viewer=viewer, audience=row.visibility):
            continue
        visible_exp.append(_serialize_experience(row))

    education = db.query(CareerEducation).filter(CareerEducation.user_id == owner.id).order_by(
        CareerEducation.sort_order).all()
    certifications = db.query(CareerCertification).filter(CareerCertification.user_id == owner.id).order_by(
        CareerCertification.sort_order).all()

    edu_out = []
    for row in education:
        if not own and row.review_state != "confirmed":
            continue
        if not own and not viewer_can_see(db, owner=owner, viewer=viewer, audience=row.visibility):
            continue
        edu_out.append(EducationOut(
            id=row.id, institution=row.institution, degree=row.degree, field_of_study=row.field_of_study,
            start_year=row.start_year, start_month=row.start_month, end_year=row.end_year, end_month=row.end_month,
            is_current=row.is_current, description=row.description, visibility=row.visibility,  # type: ignore[arg-type]
            sort_order=row.sort_order, source=row.source, review_state=row.review_state,  # type: ignore[arg-type]
        ))

    cert_out = []
    for row in certifications:
        if not own and row.review_state != "confirmed":
            continue
        if not own and not viewer_can_see(db, owner=owner, viewer=viewer, audience=row.visibility):
            continue
        cert_out.append(CertificationOut(
            id=row.id, name=row.name, issuer=row.issuer, year=row.year, month=row.month,
            credential_url=row.credential_url, visibility=row.visibility,  # type: ignore[arg-type]
            sort_order=row.sort_order, source=row.source, review_state=row.review_state,  # type: ignore[arg-type]
        ))

    cat_rows = [e.model_dump() for e in visible_exp]
    skill_rows = []
    for exp in visible_exp:
        for interval in exp.skill_intervals:
            skill_rows.append({
                "tech_name": interval.tech_name or str(interval.tech_stack_id),
                "tech_stack_id": interval.tech_stack_id,
                "start_year": interval.start_year, "start_month": interval.start_month,
                "end_year": interval.end_year, "end_month": interval.end_month,
                "is_current": exp.is_current,
            })
    return CareerSummary(
        experiences=visible_exp, education=edu_out, certifications=cert_out,
        category_durations=category_totals(cat_rows),
        skill_durations=skill_totals(skill_rows),
    )


@router.post("/experiences", response_model=ExperienceOut, status_code=201)
def create_experience(data: ExperienceIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_career()
    row = CareerExperience(
        user_id=me.id, organization=data.organization.strip(), title=data.title.strip(),
        category=data.category, start_year=data.start_year, start_month=data.start_month,
        end_year=None if data.is_current else data.end_year,
        end_month=None if data.is_current else data.end_month,
        is_current=data.is_current, location=data.location, description=data.description,
        responsibilities=data.responsibilities, achievements=data.achievements,
        visibility=data.visibility, sort_order=data.sort_order, source="manual", review_state="confirmed",
    )
    db.add(row)
    db.flush()
    _set_intervals(db, row, data.skill_intervals)
    _set_projects(row, data.project_ids)
    db.commit()
    db.refresh(row)
    from app.services.career_evidence import after_career_change
    after_career_change(db, me.id)
    row = db.query(CareerExperience).options(
        selectinload(CareerExperience.skill_intervals).selectinload(CareerSkillInterval.tech_stack),
        selectinload(CareerExperience.project_links),
    ).filter(CareerExperience.id == row.id).one()
    return _serialize_experience(row)


@router.patch("/experiences/{experience_id}", response_model=ExperienceOut)
def update_experience(
    experience_id: UUID, data: ExperienceUpdate,
    db: Session = Depends(get_db), me: User = Depends(require_developer),
):
    _require_career()
    row = db.query(CareerExperience).filter(CareerExperience.id == experience_id, CareerExperience.user_id == me.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Experience not found")
    payload = data.model_dump(exclude_unset=True)
    intervals = payload.pop("skill_intervals", None)
    project_ids = payload.pop("project_ids", None)
    for key, value in payload.items():
        setattr(row, key, value)
    if row.is_current:
        row.end_year = None
        row.end_month = None
    if intervals is not None:
        from app.schemas.career import SkillIntervalIn
        parsed = [SkillIntervalIn.model_validate(i) if isinstance(i, dict) else i for i in intervals]
        _set_intervals(db, row, parsed)
    if project_ids is not None:
        _set_projects(row, project_ids)
    db.commit()
    from app.services.career_evidence import after_career_change
    after_career_change(db, me.id)
    row = db.query(CareerExperience).options(
        selectinload(CareerExperience.skill_intervals).selectinload(CareerSkillInterval.tech_stack),
        selectinload(CareerExperience.project_links),
    ).filter(CareerExperience.id == experience_id).one()
    return _serialize_experience(row)


@router.delete("/experiences/{experience_id}", status_code=204)
def delete_experience(experience_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_career()
    row = db.query(CareerExperience).filter(CareerExperience.id == experience_id, CareerExperience.user_id == me.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Experience not found")
    db.delete(row)
    db.commit()
    from app.services.career_evidence import after_career_change
    after_career_change(db, me.id)


@router.post("/experiences/reorder", response_model=List[ExperienceOut])
def reorder_experiences(data: ReorderIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_career()
    rows = db.query(CareerExperience).filter(CareerExperience.user_id == me.id).all()
    by_id = {r.id: r for r in rows}
    for index, eid in enumerate(data.ids):
        if eid in by_id:
            by_id[eid].sort_order = index
    db.commit()
    return _summary_for(db, me, me).experiences


@router.post("/education", response_model=EducationOut, status_code=201)
def create_education(data: EducationIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_career()
    row = CareerEducation(user_id=me.id, **data.model_dump(), source="manual", review_state="confirmed")
    db.add(row)
    db.commit()
    db.refresh(row)
    return EducationOut(
        id=row.id, **data.model_dump(), source=row.source, review_state=row.review_state,  # type: ignore[arg-type]
    )


@router.patch("/education/{education_id}", response_model=EducationOut)
def update_education(
    education_id: UUID, data: EducationIn,
    db: Session = Depends(get_db), me: User = Depends(require_developer),
):
    _require_career()
    row = db.query(CareerEducation).filter(CareerEducation.id == education_id, CareerEducation.user_id == me.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Education not found")
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return EducationOut(
        id=row.id, institution=row.institution, degree=row.degree, field_of_study=row.field_of_study,
        start_year=row.start_year, start_month=row.start_month, end_year=row.end_year, end_month=row.end_month,
        is_current=row.is_current, description=row.description, visibility=row.visibility,  # type: ignore[arg-type]
        sort_order=row.sort_order, source=row.source, review_state=row.review_state,  # type: ignore[arg-type]
    )


@router.delete("/education/{education_id}", status_code=204)
def delete_education(education_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_career()
    row = db.query(CareerEducation).filter(CareerEducation.id == education_id, CareerEducation.user_id == me.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Education not found")
    db.delete(row)
    db.commit()


@router.post("/certifications", response_model=CertificationOut, status_code=201)
def create_certification(data: CertificationIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_career()
    row = CareerCertification(user_id=me.id, **data.model_dump(), source="manual", review_state="confirmed")
    db.add(row)
    db.commit()
    db.refresh(row)
    return CertificationOut(id=row.id, **data.model_dump(), source=row.source, review_state=row.review_state)  # type: ignore[arg-type]


@router.patch("/certifications/{cert_id}", response_model=CertificationOut)
def update_certification(
    cert_id: UUID, data: CertificationIn,
    db: Session = Depends(get_db), me: User = Depends(require_developer),
):
    _require_career()
    row = db.query(CareerCertification).filter(CareerCertification.id == cert_id, CareerCertification.user_id == me.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Certification not found")
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return CertificationOut(
        id=row.id, name=row.name, issuer=row.issuer, year=row.year, month=row.month,
        credential_url=row.credential_url, visibility=row.visibility,  # type: ignore[arg-type]
        sort_order=row.sort_order, source=row.source, review_state=row.review_state,  # type: ignore[arg-type]
    )


@router.delete("/certifications/{cert_id}", status_code=204)
def delete_certification(cert_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_career()
    row = db.query(CareerCertification).filter(CareerCertification.id == cert_id, CareerCertification.user_id == me.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Certification not found")
    db.delete(row)
    db.commit()


@router.put("/visibility", status_code=204)
def set_field_visibility(data: FieldVisibilityIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_career()
    row = (
        db.query(CareerFieldVisibility)
        .filter(
            CareerFieldVisibility.user_id == me.id,
            CareerFieldVisibility.resource_type == data.resource_type,
            CareerFieldVisibility.resource_id == data.resource_id,
            CareerFieldVisibility.field_name == data.field_name,
        )
        .first()
    )
    if row:
        row.audience = data.audience
    else:
        db.add(CareerFieldVisibility(
            user_id=me.id, resource_type=data.resource_type, resource_id=data.resource_id,
            field_name=data.field_name, audience=data.audience,
        ))
    db.commit()


@router.get("/entitlements")
def my_entitlements(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    return {"features": list_for_user(db, me.id)}


@router.post("/entitlements/grant", status_code=201)
def grant_entitlement(
    data: dict,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """Dev/admin grant for Resume Studio. Prefer admin UI actions; this is a direct helper."""
    from app.models.admin import PlatformAdmin
    from app.services.entitlements import grant, grant_all_developers
    from uuid import UUID as _UUID

    admin = db.query(PlatformAdmin).filter(PlatformAdmin.user_id == me.id).first()
    if not admin:
        raise HTTPException(status_code=403, detail="Only platform admins can grant entitlements")
    feature = str(data.get("feature") or "resume_studio")
    if feature not in ("resume_studio", "project_intelligence"):
        raise HTTPException(status_code=400, detail="Supported features: resume_studio, project_intelligence")
    days = data.get("days")
    if days is not None:
        try:
            days = int(days)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="days must be an integer") from exc
        if days < 1 or days > 3650:
            raise HTTPException(status_code=400, detail="days must be between 1 and 3650")

    if data.get("all_developers"):
        count = grant_all_developers(db, feature=feature, source="admin_grant", granted_by=me.id, days=days)
        db.commit()
        return {"granted": count, "feature": feature, "days": days}

    try:
        target = _UUID(str(data.get("user_id")))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="user_id must be a UUID") from exc
    row = grant(db, target, feature, source="admin_grant", granted_by=me.id, days=days)
    db.commit()
    return {
        "user_id": str(target),
        "feature": feature,
        "source": row.source,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }
