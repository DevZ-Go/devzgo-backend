"""Centralized badge recalculation for Experience, Skill Evidence, and Assessment families."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session, selectinload

from app.models.badges import (
    AssessmentAchievementRecord, BadgeCriteriaVersion, BadgeDefinition, EarnedBadge, SkillEvidenceRecord,
)
from app.models.career import CareerExperience
from app.models.challenge import ChallengeReview, ChallengeSubmission, TechnicalChallenge
from app.models.project import Project
from app.services.career_duration import (
    EXPERIENCE_MILESTONES, INTERNSHIP_CATEGORIES, NON_EMPLOYMENT_CATEGORIES, PROFESSIONAL_CATEGORIES,
    category_totals, months_in_intervals, merge_intervals, span_from_fields,
)
PLATINUM_DIAMOND_DISABLED = True


SEED_DEFINITIONS = [
    # Experience family
    *[{"stable_key": f"exp_professional_{m}m", "family": "experience", "track": "professional",
       "name": f"{'6 Months' if m == 6 else f'{m // 12} Year' if m % 12 == 0 and m // 12 == 1 else f'{m // 12} Years'} Professional Experience",
       "description": "Merged calendar-month duration across paid professional categories.",
       "icon_key": "medallion", "status": "active",
       "criteria": f"At least {m} months of confirmed full-time, part-time, contract, or freelance experience after merging overlaps.",
       "rules": {"months": m, "categories": list(PROFESSIONAL_CATEGORIES)}}
      for m in EXPERIENCE_MILESTONES],
    *[{"stable_key": f"exp_internship_{m}m", "family": "experience", "track": "internship_coop",
       "name": f"{m // 12 if m >= 12 else m} {'Year' if m == 12 else 'Years' if m > 12 else 'Months'} Internship/Co-op Experience",
       "description": "Internship and co-op experience duration (distinct from professional employment).",
       "icon_key": "medallion", "status": "active",
       "criteria": f"At least {m} months of confirmed internship or co-op experience.",
       "rules": {"months": m, "categories": list(INTERNSHIP_CATEGORIES)}}
      for m in (6, 12, 24)],
    *[{"stable_key": f"exp_freelance_{m}m", "family": "experience", "track": "freelance",
       "name": f"{m // 12} Year{'s' if m // 12 != 1 else ''} Freelance Experience",
       "description": "Freelance category duration after merging overlaps.",
       "icon_key": "medallion", "status": "active",
       "criteria": f"At least {m} months of confirmed freelance experience.",
       "rules": {"months": m, "categories": ["freelance"]}}
      for m in (12, 24, 36)],
    *[{"stable_key": f"exp_volunteer_{m}m", "family": "experience", "track": "volunteer",
       "name": f"{m // 12} Year{'s' if m // 12 != 1 else ''} Volunteer Technical Experience",
       "description": "Volunteer technical experience — not professional employment.",
       "icon_key": "medallion", "status": "active",
       "criteria": f"At least {m} months of confirmed volunteer technical experience.",
       "rules": {"months": m, "categories": ["volunteer"]}}
      for m in (12, 24)],
    # Skill evidence tiers
    {"stable_key": "skill_bronze", "family": "skill_evidence", "track": "bronze",
     "name": "Skill Evidence Bronze", "description": "One qualifying attributable technical artifact.",
     "icon_key": "shield", "status": "active",
     "criteria": "At least one project with contribution-linked or confirmed detected technology.",
     "rules": {"tier": "bronze", "min_artifacts": 1}},
    {"stable_key": "skill_silver", "family": "skill_evidence", "track": "silver",
     "name": "Skill Evidence Silver", "description": "Two distinct artifacts or one artifact plus a reviewed assessment.",
     "icon_key": "shield", "status": "active",
     "criteria": "Two distinct qualifying artifacts with attributable work on at least one; or one artifact plus a relevant reviewed assessment.",
     "rules": {"tier": "silver", "min_artifacts": 2}},
    {"stable_key": "skill_gold", "family": "skill_evidence", "track": "gold",
     "name": "Skill Evidence Gold", "description": "Three artifacts with attributable work and assessment or documented responsibility.",
     "icon_key": "shield", "status": "active",
     "criteria": "Three distinct qualifying artifacts, attributable work on at least two, plus a reviewed assessment or documented responsibility.",
     "rules": {"tier": "gold", "min_artifacts": 3}},
    {"stable_key": "skill_platinum", "family": "skill_evidence", "track": "platinum",
     "name": "Skill Evidence Platinum", "description": "Disabled until criteria and review mechanisms are validated.",
     "icon_key": "shield", "status": "disabled",
     "criteria": "Gold plus repeated substantive evidence and calibrated technical review.",
     "rules": {"tier": "platinum", "disabled": True}},
    {"stable_key": "skill_diamond", "family": "skill_evidence", "track": "diamond",
     "name": "Skill Evidence Diamond", "description": "Disabled until expert-review process is validated.",
     "icon_key": "shield", "status": "disabled",
     "criteria": "Platinum plus independent substantive evidence and expert review.",
     "rules": {"tier": "diamond", "disabled": True}},
    # Assessment participation
    *[{"stable_key": f"assess_participation_{n}", "family": "assessment", "track": "participation",
       "name": f"{'First' if n == 1 else n} Reviewed Assessment{'s' if n != 1 else ''}",
       "description": "Completion milestone — not proof of mastery.",
       "icon_key": "crest", "status": "active",
       "criteria": f"{n} distinct eligible human-reviewed assessment(s).",
       "rules": {"count": n}}
      for n in (1, 3, 5, 10)],
    {"stable_key": "assess_skill_demonstrated", "family": "assessment", "track": "performance",
     "name": "Skill Demonstrated", "description": "At least one released review with meets_expectations or stronger.",
     "icon_key": "crest", "status": "active",
     "criteria": "A released human review with overall meets_expectations or strong on an eligible challenge.",
     "rules": {"min_rating": "meets_expectations"}},
    {"stable_key": "assess_advanced", "family": "assessment", "track": "performance",
     "name": "Advanced Assessment", "description": "Strong result on an advanced difficulty challenge.",
     "icon_key": "crest", "status": "active",
     "criteria": "Released strong ratings on an advanced eligible challenge.",
     "rules": {"difficulty": "advanced", "min_rating": "strong"}},
    {"stable_key": "assess_excellence", "family": "assessment", "track": "performance",
     "name": "Assessment Excellence", "description": "Multiple strong reviewed assessments.",
     "icon_key": "crest", "status": "active",
     "criteria": "At least two distinct eligible challenges with released strong ratings.",
     "rules": {"strong_count": 2}},
    # Premium family — only awarded while Resume Studio entitlement is active (does not affect match ranking).
    {"stable_key": "premium_resume_studio", "family": "premium", "track": "studio",
     "name": "Resume Studio", "description": "Unlocked Resume Studio Premium generate and export.",
     "icon_key": "medallion", "status": "active",
     "criteria": "Active Resume Studio Premium entitlement (admin grant or billing).",
     "rules": {"requires_entitlement": "resume_studio"}},
    {"stable_key": "premium_confirmed_resume", "family": "premium", "track": "studio",
     "name": "Confirmed Career Import", "description": "Confirmed experience from a resume while Premium was active.",
     "icon_key": "medallion", "status": "active",
     "criteria": "At least one confirmed resume-import experience and active Resume Studio Premium.",
     "rules": {"requires_entitlement": "resume_studio", "min_resume_experiences": 1}},
]


def ensure_definitions(db: Session) -> None:
    existing = {d.stable_key: d for d in db.query(BadgeDefinition).all()}
    for spec in SEED_DEFINITIONS:
        row = existing.get(spec["stable_key"])
        if not row:
            row = BadgeDefinition(
                stable_key=spec["stable_key"], family=spec["family"], track=spec["track"],
                name=spec["name"], description=spec["description"], icon_key=spec["icon_key"],
                status=spec["status"],
            )
            db.add(row)
            db.flush()
            db.add(BadgeCriteriaVersion(
                definition_id=row.id, version=1, published_criteria=spec["criteria"], rules=spec["rules"],
            ))
        elif row.status != spec["status"]:
            row.status = spec["status"]
    db.flush()


def _award(db: Session, user_id: UUID, definition: BadgeDefinition, evidence_refs: list, label: str) -> bool:
    """Award if new. Returns True only when a new unlock row is created."""
    if definition.status != "active":
        return False
    version = (
        db.query(BadgeCriteriaVersion)
        .filter(BadgeCriteriaVersion.definition_id == definition.id)
        .order_by(BadgeCriteriaVersion.version.desc())
        .first()
    )
    if not version:
        return False
    existing = (
        db.query(EarnedBadge)
        .filter(
            EarnedBadge.user_id == user_id,
            EarnedBadge.definition_id == definition.id,
            EarnedBadge.criteria_version_id == version.id,
            EarnedBadge.revoked_at.is_(None),
        )
        .first()
    )
    if existing:
        existing.evidence_refs = evidence_refs
        existing.provenance_label = label
        return False
    db.add(EarnedBadge(
        user_id=user_id, definition_id=definition.id, criteria_version_id=version.id,
        evidence_refs=evidence_refs, provenance_label=label, visibility="connections",
    ))
    db.flush()
    return True


def _notify_unlock(db: Session, user_id: UUID, definition: BadgeDefinition) -> None:
    from app.models.user import User
    from app.services.social import notify

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return
    notify(
        db,
        user_id,
        user,
        "achievement",
        f"badge:{definition.stable_key}",
        f"Achievement unlocked: {definition.name}",
        once=True,
        allow_self=True,
    )


def _experience_months_for(db: Session, user_id: UUID, categories: set[str]) -> tuple[Optional[int], str]:
    rows = (
        db.query(CareerExperience)
        .filter(CareerExperience.user_id == user_id, CareerExperience.review_state == "confirmed")
        .all()
    )
    spans = []
    sources = set()
    for row in rows:
        if row.category not in categories:
            continue
        # Never count academic/independent toward professional employment badges.
        if row.category in NON_EMPLOYMENT_CATEGORIES and categories & PROFESSIONAL_CATEGORIES:
            continue
        spans.append(span_from_fields(
            row.start_year, row.start_month, row.end_year, row.end_month, is_current=row.is_current,
        ))
        sources.add("document-supported" if row.source == "resume_import" else "self-reported")
    known = [s for s in spans if s.known]
    if not known:
        return None, "self-reported"
    return months_in_intervals(merge_intervals(known)), (
        "document-supported" if "document-supported" in sources and len(sources) == 1 else
        "mixed" if len(sources) > 1 else "self-reported"
    )


def _qualifying_artifacts(db: Session, user_id: UUID) -> list[dict]:
    projects = db.query(Project).filter(Project.owner_id == user_id).all()
    # Dedupe mirrored GitHub URLs
    seen_repos: set[str] = set()
    unique = []
    for p in projects:
        key = (p.github_url or "").rstrip("/").lower() or str(p.id)
        if key in seen_repos:
            continue
        seen_repos.add(key)
        unique.append(p)
    from app.services.github_history import credit
    from app.services.skill_matrix import detected_names, project_skills

    credited = credit(db, [p for p in unique if p.github_url or p.github_commit_count])
    detected = detected_names(db, unique)
    artifacts = []
    for p in unique:
        skills = project_skills(p, detected)
        commits = credited.get(user_id, {}).get("projects", {}).get(p.id, {}).get("commits", 0)
        if commits > 0 or any(v.get("detected") for v in skills.values()):
            artifacts.append({"project_id": str(p.id), "commits": commits, "detected": True})
    return artifacts


def _eligible_reviews(db: Session, user_id: UUID) -> list[tuple[ChallengeSubmission, ChallengeReview, TechnicalChallenge]]:
    subs = (
        db.query(ChallengeSubmission)
        .options(selectinload(ChallengeSubmission.review))
        .filter(ChallengeSubmission.developer_id == user_id, ChallengeSubmission.status == "reviewed")
        .all()
    )
    out = []
    for sub in subs:
        review = sub.review
        if not review or review.status != "released" or review.released_at is None:
            continue
        challenge = db.query(TechnicalChallenge).filter(TechnicalChallenge.id == sub.challenge_id).first()
        if not challenge:
            continue
        # Anti-farm: recruiter cannot be the same as developer; skip self-authored trivial loops.
        if challenge.recruiter_id == user_id:
            continue
        out.append((sub, review, challenge))
    return out


def _try_award(
    db: Session, user_id: UUID, definition: BadgeDefinition | None, evidence_refs: list, label: str, awarded: list[str],
) -> None:
    if not definition:
        return
    unlocked = _award(db, user_id, definition, evidence_refs, label)
    awarded.append(definition.stable_key)
    if unlocked:
        _notify_unlock(db, user_id, definition)


def recalculate_for_user(db: Session, user_id: UUID) -> dict:
    ensure_definitions(db)
    defs = {d.stable_key: d for d in db.query(BadgeDefinition).all()}
    awarded: list[str] = []

    # Experience milestones
    for key, categories in (
        ("exp_professional_", PROFESSIONAL_CATEGORIES),
        ("exp_internship_", INTERNSHIP_CATEGORIES),
        ("exp_freelance_", {"freelance"}),
        ("exp_volunteer_", {"volunteer"}),
    ):
        months, label = _experience_months_for(db, user_id, categories)
        if months is None:
            continue
        for m in EXPERIENCE_MILESTONES:
            stable = f"{key}{m}m"
            if stable not in defs:
                continue
            if months >= m:
                _try_award(db, user_id, defs[stable], [{"type": "career_duration", "months": months}], label, awarded)

    # Skill evidence
    artifacts = _qualifying_artifacts(db, user_id)
    reviews = _eligible_reviews(db, user_id)
    has_assessment = len(reviews) > 0
    if len(artifacts) >= 1:
        _try_award(db, user_id, defs.get("skill_bronze"), artifacts[:3], "project-supported", awarded)
    if len(artifacts) >= 2 or (len(artifacts) >= 1 and has_assessment):
        _try_award(db, user_id, defs.get("skill_silver"), artifacts[:5], "project-supported", awarded)
    if len(artifacts) >= 3 and sum(1 for a in artifacts if a["commits"] > 0) >= 2 and has_assessment:
        _try_award(db, user_id, defs.get("skill_gold"), artifacts[:8], "project-supported", awarded)
    # Platinum/Diamond intentionally not awarded while disabled.

    # Assessment participation + performance
    for n in (1, 3, 5, 10):
        if len(reviews) >= n:
            key = f"assess_participation_{n}"
            _try_award(
                db, user_id, defs.get(key),
                [{"submission_id": str(s.id)} for s, _, _ in reviews[:n]], "assessment-reviewed", awarded,
            )

    strong = []
    meets = []
    for sub, review, challenge in reviews:
        ratings = list((review.ratings or {}).values())
        if any(r == "strong" for r in ratings):
            strong.append((sub, challenge))
        if any(r in ("meets_expectations", "strong") for r in ratings):
            meets.append((sub, challenge))
        existing = (
            db.query(AssessmentAchievementRecord)
            .filter(AssessmentAchievementRecord.submission_id == sub.id, AssessmentAchievementRecord.track == "participation")
            .first()
        )
        if not existing:
            db.add(AssessmentAchievementRecord(
                user_id=user_id, submission_id=sub.id, challenge_id=challenge.id,
                track="participation", rating_summary=review.ratings, eligible=True,
            ))

    if meets:
        _try_award(
            db, user_id, defs.get("assess_skill_demonstrated"),
            [{"submission_id": str(s.id)} for s, _ in meets[:3]], "assessment-reviewed", awarded,
        )
    if any(c.difficulty == "advanced" for _, c in strong):
        _try_award(
            db, user_id, defs.get("assess_advanced"),
            [{"submission_id": str(s.id)} for s, c in strong if c.difficulty == "advanced"][:3],
            "assessment-reviewed", awarded,
        )
    if len({c.id for _, c in strong}) >= 2:
        _try_award(
            db, user_id, defs.get("assess_excellence"),
            [{"submission_id": str(s.id)} for s, _ in strong[:4]], "assessment-reviewed", awarded,
        )

    # Premium unlocks — entitlement required; never changes match ranking.
    from app.services.entitlements import has_feature
    if has_feature(db, user_id, "resume_studio"):
        _try_award(
            db, user_id, defs.get("premium_resume_studio"),
            [{"type": "entitlement", "feature": "resume_studio"}], "premium-entitlement", awarded,
        )
        resume_exp = (
            db.query(CareerExperience)
            .filter(
                CareerExperience.user_id == user_id,
                CareerExperience.review_state == "confirmed",
                CareerExperience.source == "resume_import",
            )
            .count()
        )
        if resume_exp >= 1:
            _try_award(
                db, user_id, defs.get("premium_confirmed_resume"),
                [{"type": "resume_import", "count": resume_exp}], "premium-resume-import", awarded,
            )

    db.commit()
    return {
        "awarded_keys": sorted(set(awarded)),
        "newly_notified": True,
        "platinum_diamond_disabled": PLATINUM_DIAMOND_DISABLED,
    }


def list_badges_for(db: Session, user_id: UUID, *, include_revoked: bool = False) -> list[dict]:
    ensure_definitions(db)
    q = (
        db.query(EarnedBadge)
        .options(selectinload(EarnedBadge.definition), selectinload(EarnedBadge.criteria_version))
        .filter(EarnedBadge.user_id == user_id)
    )
    if not include_revoked:
        q = q.filter(EarnedBadge.revoked_at.is_(None))
    rows = q.order_by(EarnedBadge.awarded_at.desc()).all()
    return [
        {
            "id": str(r.id),
            "stable_key": r.definition.stable_key,
            "family": r.definition.family,
            "track": r.definition.track,
            "name": r.definition.name,
            "description": r.definition.description,
            "icon_key": r.definition.icon_key,
            "criteria": r.criteria_version.published_criteria,
            "criteria_version": r.criteria_version.version,
            "awarded_at": r.awarded_at.isoformat() if r.awarded_at else None,
            "visibility": r.visibility,
            "provenance_label": r.provenance_label,
            "evidence_refs": r.evidence_refs or [],
            "limitations": (
                "This badge does not certify skill mastery or employability."
                if r.definition.family != "experience"
                else "Duration is self-reported or document-supported and is not independently verified employment."
            ),
        }
        for r in rows
        if r.definition.status == "active"
    ]
