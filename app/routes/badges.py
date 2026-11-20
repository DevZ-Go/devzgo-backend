"""Badge listing, detail, visibility, and recalculation."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user_optional, require_developer
from app.core.feature_flags import badges_enabled
from app.db.session import get_db
from app.models.badges import EarnedBadge
from app.models.user import User
from app.services.badge_engine import list_badges_for, recalculate_for_user
from app.services.career_privacy import viewer_can_see

router = APIRouter(prefix="/badges", tags=["Badges"])


class VisibilityIn(BaseModel):
    visibility: str = Field(pattern="^(private|connections|recruiters|public)$")


def _enabled():
    if not badges_enabled():
        raise HTTPException(status_code=404, detail="Badges are not enabled")


@router.post("/me/recalculate")
def recalculate(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _enabled()
    return recalculate_for_user(db, me.id)


@router.get("/me")
def my_badges(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _enabled()
    return {"badges": list_badges_for(db, me.id, include_revoked=False)}


@router.get("/users/{user_id}")
def user_badges(
    user_id: UUID,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    _enabled()
    owner = db.query(User).filter(User.id == user_id).first()
    if not owner or owner.is_recruiter:
        raise HTTPException(status_code=404, detail="Developer not found")
    own = viewer is not None and viewer.id == owner.id
    badges = []
    for badge in list_badges_for(db, user_id):
        if own or viewer_can_see(db, owner=owner, viewer=viewer, audience=badge["visibility"]):
            # Never leak private evidence refs to non-owners.
            if not own:
                badge = {**badge, "evidence_refs": []}
            badges.append(badge)
    return {"badges": badges}


@router.get("/{badge_id}")
def badge_detail(
    badge_id: UUID,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    _enabled()
    row = db.query(EarnedBadge).filter(EarnedBadge.id == badge_id, EarnedBadge.revoked_at.is_(None)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Badge not found")
    owner = db.query(User).filter(User.id == row.user_id).first()
    own = viewer is not None and viewer.id == row.user_id
    if not own and not viewer_can_see(db, owner=owner, viewer=viewer, audience=row.visibility):
        raise HTTPException(status_code=404, detail="Badge not found")
    items = list_badges_for(db, row.user_id)
    detail = next((b for b in items if b["id"] == str(row.id)), None)
    if not detail:
        raise HTTPException(status_code=404, detail="Badge not found")
    if not own:
        detail = {**detail, "evidence_refs": []}
    return detail


@router.patch("/{badge_id}/visibility")
def set_visibility(
    badge_id: UUID,
    data: VisibilityIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    _enabled()
    row = db.query(EarnedBadge).filter(EarnedBadge.id == badge_id, EarnedBadge.user_id == me.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Badge not found")
    row.visibility = data.visibility
    db.commit()
    return {"id": str(row.id), "visibility": row.visibility}
