"""Field-level and resource-level career visibility for Talent Intelligence."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.career import CareerFieldVisibility
from app.models.connection import Connection
from app.models.user import User
from app.services.social import audience_open

AUDIENCE_RANK = {"private": 0, "connections": 1, "recruiters": 2, "public": 3}


def _connected(db: Session, owner_id: UUID, viewer_id: Optional[UUID]) -> bool:
    if not viewer_id or viewer_id == owner_id:
        return True
    return (
        db.query(Connection.id)
        .filter(
            Connection.status == "accepted",
            ((Connection.requester_id == owner_id) & (Connection.receiver_id == viewer_id))
            | ((Connection.requester_id == viewer_id) & (Connection.receiver_id == owner_id)),
        )
        .first()
        is not None
    )


def viewer_can_see(
    db: Session,
    *,
    owner: User,
    viewer: Optional[User],
    audience: str,
) -> bool:
    """Whether a viewer may see a career resource at the given audience level."""
    if viewer and viewer.id == owner.id:
        return True
    level = (audience or "private").lower()
    if level == "public":
        return True
    if level == "private":
        return False
    if level == "recruiters":
        return bool(viewer and viewer.is_recruiter)
    if level == "connections":
        if viewer and viewer.is_recruiter:
            # Recruiters see connections-tier career data only when the owner is discoverable
            # and their profile is open — not merely because a resume was uploaded.
            return audience_open(db, owner, viewer.id if viewer else None, "profile_visibility", "public")
        return _connected(db, owner.id, viewer.id if viewer else None)
    return False


def field_audience(
    db: Session,
    user_id: UUID,
    resource_type: str,
    resource_id: UUID,
    field_name: str,
    default: str,
) -> str:
    row = (
        db.query(CareerFieldVisibility)
        .filter(
            CareerFieldVisibility.user_id == user_id,
            CareerFieldVisibility.resource_type == resource_type,
            CareerFieldVisibility.resource_id == resource_id,
            CareerFieldVisibility.field_name == field_name,
        )
        .first()
    )
    return row.audience if row else default


def redact_experience(row: dict, *, include_sensitive: bool) -> dict:
    """Drop private-ish fields when the viewer may see the card but not every field."""
    if include_sensitive:
        return row
    out = dict(row)
    for key in ("description", "responsibilities", "achievements", "location"):
        out[key] = None if key != "responsibilities" and key != "achievements" else []
    return out
