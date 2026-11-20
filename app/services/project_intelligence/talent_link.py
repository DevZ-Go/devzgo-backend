"""Link approved Project Intelligence claims to Talent Intelligence SkillEvidenceRecord.

Never auto-awards years, high-level badges, or verified-professional labels.
Requires contribution attribution before review_state becomes confirmed.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.badges import SkillEvidenceRecord
from app.models.project_intelligence import ProjectEvidenceClaim
from app.models.techstack import TechStack
from app.services.tech_catalog import KNOWN_NAMES


def _resolve_tech_id(db: Session, name: str) -> int | None:
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


def link_framework_claims_as_candidates(
    db: Session,
    *,
    user_id: UUID,
    project_id: UUID,
    attributed: bool = False,
    audience: str = "connections",
) -> int:
    """Create pending/candidate skill evidence from framework/dependency claims for this project owner.

    If attributed is False, review_state stays pending and attribution_status on claims becomes candidate.
    Collaborators are never auto-credited.
    """
    claims = (
        db.query(ProjectEvidenceClaim)
        .filter(
            ProjectEvidenceClaim.project_id == project_id,
            ProjectEvidenceClaim.claim_type.in_(("framework", "dependency", "integration")),
        )
        .all()
    )
    count = 0
    for claim in claims:
        tech_name = (claim.extra or {}).get("tech") or (claim.extra or {}).get("package")
        if not tech_name:
            continue
        tid = _resolve_tech_id(db, str(tech_name))
        if tid is None:
            continue
        existing = (
            db.query(SkillEvidenceRecord)
            .filter(
                SkillEvidenceRecord.user_id == user_id,
                SkillEvidenceRecord.tech_stack_id == tid,
                SkillEvidenceRecord.evidence_class == "detected",
                SkillEvidenceRecord.source_type == "project",
                SkillEvidenceRecord.source_id == str(project_id),
                SkillEvidenceRecord.invalidated.is_(False),
            )
            .first()
        )
        review = "confirmed" if attributed else "pending"
        claim.attribution_status = "attributed" if attributed else "candidate"
        if existing:
            existing.review_state = review
            existing.audience = audience
            existing.source_ref = {
                "claim_id": str(claim.id),
                "support_class": claim.support_class,
                "note": "Project Intelligence candidate evidence; not a verified professional label.",
            }
        else:
            db.add(SkillEvidenceRecord(
                user_id=user_id,
                tech_stack_id=tid,
                evidence_class="detected",
                source_type="project",
                source_id=str(project_id),
                source_ref={
                    "claim_id": str(claim.id),
                    "support_class": claim.support_class,
                    "note": "Project Intelligence candidate evidence; not a verified professional label.",
                },
                review_state=review,
                audience=audience,
            ))
        count += 1
    db.flush()
    return count
