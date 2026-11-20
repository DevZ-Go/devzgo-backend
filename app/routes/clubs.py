"""Network clubs (student orgs) — membership, roles, policies. Distinct from chat /messages/groups."""

from __future__ import annotations

import re
import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, get_current_user_optional, require_developer
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.clubs import EXEC_ROLES, ClubCustomRole, ClubMember, NetworkClub
from app.models.user import User
from app.services.social import display_name

router = APIRouter(prefix="/groups", tags=["Network clubs"])

ROLE_ALIASES = {
    "host": "president",
    "president": "president",
    "vice_president": "vice_president",
    "vice-president": "vice_president",
    "treasurer": "treasurer",
    "executive": "executive",
    "member": "member",
}
# Frontend still uses "host" for president in some places
FRONT_ROLE = {
    "president": "host",
    "vice_president": "vice_president",
    "treasurer": "treasurer",
    "executive": "executive",
    "member": "member",
}


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or "club"
    return base


def _unique_slug(db: Session, name: str) -> str:
    base = _slugify(name)
    slug = base
    n = 2
    while db.query(NetworkClub.id).filter(NetworkClub.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _member_out(m: ClubMember) -> dict:
    u = m.user
    profile = getattr(u, "profile", None) if u else None
    return {
        "userId": str(m.user_id),
        "name": display_name(u) if u else "Member",
        "username": u.username if u else "unknown",
        "avatarUrl": profile.avatar_url if profile else None,
        "headline": getattr(profile, "headline", None) if profile else None,
        "role": FRONT_ROLE.get(m.role, m.role),
        "customTitle": m.custom_title,
        "joinedAt": m.joined_at.isoformat() if m.joined_at else None,
    }


def _club_out(club: NetworkClub, viewer_id: Optional[UUID] = None) -> dict:
    members = sorted(
        club.members or [],
        key=lambda m: (0 if m.role == "president" else 1, m.joined_at or club.created_at),
    )
    president = next((m for m in members if m.role == "president"), None)
    viewer_member = next((m for m in members if viewer_id and m.user_id == viewer_id), None)
    return {
        "id": str(club.id),
        "slug": club.slug,
        "name": club.name,
        "tagline": club.tagline or "",
        "about": club.about or "",
        "category": club.category or "general",
        "bannerUrl": club.banner_url,
        "avatarUrl": club.avatar_url,
        "hostId": str(president.user_id) if president else (str(club.created_by) if club.created_by else ""),
        "hostName": display_name(president.user) if president and president.user else "",
        "hostUsername": president.user.username if president and president.user else "",
        "hostTitle": (president.custom_title if president else None) or "President",
        "memberCount": len(members),
        "projectCount": 0,
        "announcementCount": 0,
        "isMember": bool(viewer_member),
        "userRole": FRONT_ROLE.get(viewer_member.role, viewer_member.role) if viewer_member else None,
        "userCustomTitle": viewer_member.custom_title if viewer_member else None,
        "members": [_member_out(m) for m in members],
        "tags": list(club.tags or []),
        "policies": club.policies or "",
        "customRoles": [{"id": str(r.id), "title": r.title} for r in (club.custom_roles or [])],
        "createdAt": club.created_at.isoformat() if club.created_at else None,
    }


def _load_club(db: Session, club_id_or_slug: str) -> NetworkClub:
    q = (
        db.query(NetworkClub)
        .options(
            selectinload(NetworkClub.members).selectinload(ClubMember.user).selectinload(User.profile),
            selectinload(NetworkClub.custom_roles),
        )
    )
    try:
        cid = UUID(club_id_or_slug)
        club = q.filter(NetworkClub.id == cid).first()
    except ValueError:
        club = q.filter(NetworkClub.slug == club_id_or_slug).first()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    return club


def _require_exec(club: NetworkClub, user_id: UUID) -> ClubMember:
    m = next((x for x in club.members if x.user_id == user_id), None)
    if not m or m.role not in EXEC_ROLES:
        raise HTTPException(status_code=403, detail="Only club executives can do that")
    return m


def _require_president(club: NetworkClub, user_id: UUID) -> ClubMember:
    m = next((x for x in club.members if x.user_id == user_id), None)
    if not m or m.role != "president":
        raise HTTPException(status_code=403, detail="Only the club president can do that")
    return m


class ClubCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    tagline: str = Field(default="", max_length=280)
    about: str = Field(default="", max_length=8000)
    category: str = Field(default="general", max_length=40)
    tags: list[str] = Field(default_factory=list)
    policies: str = Field(default="", max_length=12000)


class ClubPatchIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    tagline: Optional[str] = Field(default=None, max_length=280)
    about: Optional[str] = Field(default=None, max_length=8000)
    category: Optional[str] = Field(default=None, max_length=40)
    tags: Optional[list[str]] = None
    policies: Optional[str] = Field(default=None, max_length=12000)
    bannerUrl: Optional[str] = None
    avatarUrl: Optional[str] = None


class MemberRoleIn(BaseModel):
    role: str
    customTitle: Optional[str] = Field(default=None, max_length=120)


class CustomRoleIn(BaseModel):
    title: str = Field(min_length=2, max_length=80)


@router.get("")
def list_groups(
    q: Optional[str] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    query = db.query(NetworkClub).options(
        selectinload(NetworkClub.members).selectinload(ClubMember.user).selectinload(User.profile),
        selectinload(NetworkClub.custom_roles),
    )
    if category and category != "all":
        query = query.filter(NetworkClub.category == category)
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(NetworkClub.name.ilike(term), NetworkClub.tagline.ilike(term), NetworkClub.about.ilike(term))
        )
    clubs = query.order_by(NetworkClub.created_at.desc()).limit(100).all()
    return [_club_out(c, viewer.id if viewer else None) for c in clubs]


@router.get("/mine")
def my_groups(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    memberships = (
        db.query(ClubMember)
        .options(
            selectinload(ClubMember.club)
            .selectinload(NetworkClub.members)
            .selectinload(ClubMember.user)
            .selectinload(User.profile),
            selectinload(ClubMember.club).selectinload(NetworkClub.custom_roles),
        )
        .filter(ClubMember.user_id == me.id)
        .order_by(ClubMember.joined_at.desc())
        .all()
    )
    out = []
    for m in memberships:
        if not m.club:
            continue
        row = _club_out(m.club, me.id)
        row["membershipRole"] = FRONT_ROLE.get(m.role, m.role)
        row["membershipTitle"] = m.custom_title
        out.append(row)
    return out


@router.get("/users/{user_id}/clubs")
def clubs_for_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    memberships = (
        db.query(ClubMember)
        .options(
            selectinload(ClubMember.club)
            .selectinload(NetworkClub.members)
            .selectinload(ClubMember.user)
            .selectinload(User.profile),
            selectinload(ClubMember.club).selectinload(NetworkClub.custom_roles),
        )
        .filter(ClubMember.user_id == user_id)
        .order_by(ClubMember.joined_at.desc())
        .all()
    )
    out = []
    for m in memberships:
        if not m.club:
            continue
        row = _club_out(m.club, viewer.id if viewer else None)
        row["membershipRole"] = FRONT_ROLE.get(m.role, m.role)
        row["membershipTitle"] = m.custom_title
        out.append(row)
    return out


@router.get("/{club_id}")
def get_group(
    club_id: str,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    club = _load_club(db, club_id)
    return _club_out(club, viewer.id if viewer else None)


@router.post("", dependencies=[rate_limit("club-create", 6, 600)])
def create_group(data: ClubCreateIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    club = NetworkClub(
        id=uuid.uuid4(),
        slug=_unique_slug(db, data.name),
        name=data.name.strip(),
        tagline=(data.tagline or "").strip(),
        about=(data.about or "").strip(),
        category=(data.category or "general").strip()[:40],
        tags=[t.strip() for t in (data.tags or []) if t.strip()][:20],
        policies=(data.policies or "").strip(),
        created_by=me.id,
    )
    db.add(club)
    db.flush()
    db.add(
        ClubMember(
            club_id=club.id,
            user_id=me.id,
            role="president",
            custom_title="President",
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="You are already president of another club. Transfer that presidency before creating a new club as president.",
        )
    return _club_out(_load_club(db, str(club.id)), me.id)


@router.patch("/{club_id}")
def patch_group(
    club_id: str,
    data: ClubPatchIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_exec(club, me.id)
    if data.name is not None:
        club.name = data.name.strip()
    if data.tagline is not None:
        club.tagline = data.tagline.strip()
    if data.about is not None:
        club.about = data.about.strip()
    if data.category is not None:
        club.category = data.category.strip()[:40]
    if data.tags is not None:
        club.tags = [t.strip() for t in data.tags if t.strip()][:20]
    if data.policies is not None:
        club.policies = data.policies.strip()
    if data.bannerUrl is not None:
        club.banner_url = data.bannerUrl or None
    if data.avatarUrl is not None:
        club.avatar_url = data.avatarUrl or None
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)


@router.post("/{club_id}/join", dependencies=[rate_limit("club-join", 20, 60)])
def join_group(club_id: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    club = _load_club(db, club_id)
    existing = next((m for m in club.members if m.user_id == me.id), None)
    if existing:
        return _club_out(club, me.id)
    db.add(ClubMember(club_id=club.id, user_id=me.id, role="member", custom_title="Member"))
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)


@router.post("/{club_id}/leave")
def leave_group(club_id: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    club = _load_club(db, club_id)
    membership = next((m for m in club.members if m.user_id == me.id), None)
    if not membership:
        return _club_out(club, me.id)
    if membership.role == "president":
        raise HTTPException(
            status_code=400,
            detail="Transfer the presidency to someone else before leaving the club.",
        )
    db.delete(membership)
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)


@router.put("/{club_id}/members/{target_user_id}/role")
def update_member_role(
    club_id: str,
    target_user_id: UUID,
    data: MemberRoleIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    actor = _require_president(club, me.id)
    role = ROLE_ALIASES.get((data.role or "").strip().lower())
    if not role:
        raise HTTPException(status_code=400, detail="Invalid role")
    target = next((m for m in club.members if m.user_id == target_user_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.user_id == actor.user_id and role != "president":
        raise HTTPException(status_code=400, detail="Assign a new president before stepping down.")

    title = (data.customTitle or "").strip() or None
    defaults = {
        "president": "President",
        "vice_president": "Vice President",
        "treasurer": "Treasurer",
        "executive": "Executive",
        "member": "Member",
    }

    if role == "president":
        # Demote current president(s) in this club, then promote target.
        for m in club.members:
            if m.role == "president" and m.user_id != target.user_id:
                m.role = "executive"
                if not m.custom_title or m.custom_title.lower() == "president":
                    m.custom_title = "Executive"
        target.role = "president"
        target.custom_title = title or "President"
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="That member is already president of another club.",
            )
    else:
        target.role = role
        target.custom_title = title or defaults[role]
        db.commit()

    return _club_out(_load_club(db, str(club.id)), me.id)


@router.post("/{club_id}/custom-roles")
def add_custom_role(
    club_id: str,
    data: CustomRoleIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_exec(club, me.id)
    title = data.title.strip()
    row = ClubCustomRole(id=uuid.uuid4(), club_id=club.id, title=title)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That custom role already exists")
    return {"id": str(row.id), "title": row.title}


@router.delete("/{club_id}/custom-roles/{role_id}", status_code=204)
def delete_custom_role(
    club_id: str,
    role_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_exec(club, me.id)
    row = db.query(ClubCustomRole).filter_by(id=role_id, club_id=club.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Custom role not found")
    db.delete(row)
    db.commit()
    return None
