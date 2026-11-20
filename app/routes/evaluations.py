"""Recruiter evaluation records + purpose acknowledgement (Ontario safeguards)."""

from __future__ import annotations

from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_developer, require_recruiter
from app.db.session import get_db
from app.models.entitlements import CandidateEvaluation, ConsentRecord
from app.models.user import User

router = APIRouter(tags=["Evaluations"])

ENGINE_VERSION = "jd_match_v1_explainable"


class EvaluationIn(BaseModel):
    candidate_id: UUID
    purpose: Literal["general_discovery", "opportunity_evaluation"]
    acknowledgement: bool
    role_title: Optional[str] = Field(default=None, max_length=160)
    requirements: dict = Field(default_factory=dict)
    authorized_evidence: List[str] = Field(default_factory=list)
    result_summary: dict = Field(default_factory=dict)


class ConsentIn(BaseModel):
    purpose: str = Field(min_length=1, max_length=80)
    scope: dict = Field(default_factory=dict)


@router.post("/recruiting/evaluations", status_code=201)
def create_evaluation(data: EvaluationIn, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    if not data.acknowledgement:
        raise HTTPException(
            status_code=400,
            detail=(
                "Acknowledge that DevZ-Go provides evidence-based decision support only. "
                "Humans remain responsible for hiring decisions. A checkbox does not establish legal compliance."
            ),
        )
    candidate = db.query(User).filter(User.id == data.candidate_id, User.account_type == "developer").first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    row = CandidateEvaluation(
        recruiter_id=me.id,
        candidate_id=data.candidate_id,
        purpose=data.purpose,
        acknowledgement=True,
        role_title=data.role_title,
        requirements=data.requirements,
        authorized_evidence=data.authorized_evidence,
        result_summary=data.result_summary,
        matching_engine_version=ENGINE_VERSION,
    )
    db.add(row)
    db.commit()
    return {
        "id": str(row.id),
        "matching_engine_version": ENGINE_VERSION,
        "notice": (
            "This record supports transparency and human oversight. It is not an automatic rejection, "
            "talent score, or certified legal compliance artifact."
        ),
    }


@router.get("/recruiting/evaluations")
def list_evaluations(db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    rows = (
        db.query(CandidateEvaluation)
        .filter(CandidateEvaluation.recruiter_id == me.id)
        .order_by(CandidateEvaluation.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "evaluations": [
            {
                "id": str(r.id),
                "candidate_id": str(r.candidate_id),
                "purpose": r.purpose,
                "role_title": r.role_title,
                "matching_engine_version": r.matching_engine_version,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@router.get("/career/transparency")
def candidate_transparency(me: User = Depends(get_current_user)):
    """What evidence is analyzed and how to correct inaccuracies."""
    return {
        "analyzed": [
            "Confirmed work experience you chose to share",
            "Public or authorized project technologies and contribution-linked evidence",
            "Assessment results you chose to share",
            "Visible badges you enabled for recruiters or the public",
        ],
        "not_analyzed_as_worth": [
            "Overall intelligence",
            "Employability as a person",
            "School prestige as a proxy for ability",
            "Protected characteristics",
        ],
        "audiences": ["private", "connections", "authorized recruiters", "public"],
        "matching": (
            "Recruiters confirm requirements; the system labels each as supported, partial, unknown, or unmet "
            "from evidence they are allowed to see. Numeric coverage is disabled by default."
        ),
        "corrections": (
            "Edit career records, revoke badge visibility, withdraw shared assessment ratings, "
            "or change profile/resume visibility in Settings."
        ),
        "disclaimer": (
            "DevZ-Go does not automatically reject applicants or make hiring decisions. "
            "Employers remain responsible for applicable disclosure obligations."
        ),
    }


@router.post("/career/consents", status_code=201)
def grant_consent(data: ConsentIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    row = ConsentRecord(user_id=me.id, purpose=data.purpose, scope=data.scope)
    db.add(row)
    db.commit()
    return {"id": str(row.id), "purpose": row.purpose, "granted_at": row.granted_at}


@router.post("/career/consents/{consent_id}/revoke", status_code=204)
def revoke_consent(consent_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    from datetime import datetime, timezone
    row = db.query(ConsentRecord).filter(ConsentRecord.id == consent_id, ConsentRecord.user_id == me.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Consent not found")
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
