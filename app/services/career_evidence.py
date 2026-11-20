"""Sync SkillEvidenceRecord rows from confirmed career and resume data."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.badges import SkillEvidenceRecord
from app.models.career import CareerExperience, CareerSkillInterval
from app.models.techstack import TechStack
from app.services.tech_catalog import KNOWN_NAMES


def _upsert(
    db: Session,
    *,
    user_id: UUID,
    tech_stack_id: int,
    evidence_class: str,
    source_type: str,
    source_id: str | None,
    audience: str = "connections",
    source_ref: dict | None = None,
) -> None:
    existing = (
        db.query(SkillEvidenceRecord)
        .filter(
            SkillEvidenceRecord.user_id == user_id,
            SkillEvidenceRecord.tech_stack_id == tech_stack_id,
            SkillEvidenceRecord.evidence_class == evidence_class,
            SkillEvidenceRecord.source_type == source_type,
            SkillEvidenceRecord.source_id == source_id,
            SkillEvidenceRecord.invalidated.is_(False),
        )
        .first()
    )
    if existing:
        existing.review_state = "confirmed"
        existing.audience = audience
        if source_ref:
            existing.source_ref = source_ref
        return
    db.add(SkillEvidenceRecord(
        user_id=user_id,
        tech_stack_id=tech_stack_id,
        evidence_class=evidence_class,
        source_type=source_type,
        source_id=source_id,
        source_ref=source_ref or {},
        review_state="confirmed",
        audience=audience,
    ))


def sync_experience_skills(db: Session, experience: CareerExperience) -> int:
    """Record work_history_claim evidence for each confirmed skill interval."""
    count = 0
    for interval in experience.skill_intervals or []:
        if interval.review_state not in (None, "confirmed"):
            continue
        _upsert(
            db,
            user_id=experience.user_id,
            tech_stack_id=interval.tech_stack_id,
            evidence_class="work_history_claim",
            source_type="career",
            source_id=str(experience.id),
            audience=experience.visibility or "connections",
            source_ref={"experience_id": str(experience.id), "interval_id": str(interval.id)},
        )
        count += 1
    return count


def resolve_tech_id(db: Session, name: str) -> int | None:
    clean = (name or "").strip()
    if not clean:
        return None
    canonical = KNOWN_NAMES.get(clean.lower(), clean)
    row = db.query(TechStack).filter(TechStack.name.ilike(canonical)).first()
    if row:
        return int(row.id)
    row = TechStack(name=canonical)
    db.add(row)
    db.flush()
    return int(row.id)


def sync_resume_skills(db: Session, user_id: UUID, skill_names: list[str], *, import_job_id: str, audience: str = "private") -> int:
    """Resume skill mentions are resume_claim — not independent verification."""
    count = 0
    for name in skill_names:
        tid = resolve_tech_id(db, name)
        if tid is None:
            continue
        _upsert(
            db,
            user_id=user_id,
            tech_stack_id=tid,
            evidence_class="resume_claim",
            source_type="resume",
            source_id=import_job_id,
            audience=audience,
            source_ref={"import_job_id": import_job_id, "name": name},
        )
        count += 1
    return count


def enrich_free_analysis(db: Session, user_id: UUID, analysis: dict, skill_names: list[str]) -> dict:
    """Compare extracted skills to the developer's profile skills — no employability score."""
    from app.models.profile import UserProfile

    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    profile_skills = {
        s.strip().lower()
        for s in (profile.skills or "").split(",")
        if s.strip()
    } if profile else set()
    extracted = {s.strip().lower() for s in skill_names if s.strip()}
    out = dict(analysis or {})
    out["skills_on_resume_not_in_profile"] = sorted(extracted - profile_skills)
    out["skills_in_profile_not_on_resume"] = sorted(profile_skills - extracted)
    out["no_universal_resume_score"] = True
    return out


def after_career_change(db: Session, user_id: UUID) -> dict:
    """Sync evidence and recalculate badges after confirmed career mutations."""
    experiences = (
        db.query(CareerExperience)
        .filter(CareerExperience.user_id == user_id, CareerExperience.review_state == "confirmed")
        .all()
    )
    for exp in experiences:
        # ensure intervals are loaded
        _ = list(exp.skill_intervals)
        sync_experience_skills(db, exp)
    from app.services.badge_engine import recalculate_for_user
    db.flush()
    return recalculate_for_user(db, user_id)
