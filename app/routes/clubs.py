"""Network clubs (student orgs) — membership, roles, policies. Distinct from chat /messages/groups."""

from __future__ import annotations

import re
import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile  # Query used by list_groups
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from datetime import datetime, timezone

from app.core.dependencies import get_current_user, get_current_user_optional, require_developer
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.club_features import ClubAnnouncement, ClubEvent
from app.models.clubs import EXEC_ROLES, ClubCustomRole, ClubMember, NetworkClub
from app.models.message import Message
from app.models.user import User
from app.realtime.broadcaster import queue_event
from app.services import storage
from app.services.social import display_name, notify

BANNER_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
PRIVATE_TAG_MARKER = "__private__"


def _is_image_upload(file: UploadFile, ext: str) -> bool:
    """Accept image/* or empty content-type when the extension is a known image (croppers often omit MIME)."""
    if ext not in BANNER_EXTS:
        return False
    content = (file.content_type or "").strip().lower()
    return (not content) or content.startswith("image/")


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


def _member_out(m: ClubMember, *, include_email: bool = False) -> dict:
    u = m.user
    profile = getattr(u, "profile", None) if u else None
    out = {
        "userId": str(m.user_id),
        "name": display_name(u) if u else "Member",
        "username": u.username if u else "unknown",
        "avatarUrl": profile.avatar_url if profile else None,
        "headline": getattr(profile, "headline", None) if profile else None,
        "role": FRONT_ROLE.get(m.role, m.role),
        "customTitle": m.custom_title,
        "joinedAt": m.joined_at.isoformat() if m.joined_at else None,
        "emailBroadcastOptIn": bool(getattr(m, "email_broadcast_opt_in", True)),
    }
    if include_email and u and getattr(u, "email", None):
        out["email"] = u.email
    return out


def _is_private(club: NetworkClub) -> bool:
    return PRIVATE_TAG_MARKER in (club.tags or [])


def _clean_tags(tags: list[str] | None, *, private: bool = False) -> list[str]:
    cleaned = [t.strip() for t in (tags or []) if t.strip() and t.strip() != PRIVATE_TAG_MARKER][:20]
    if private:
        cleaned.append(PRIVATE_TAG_MARKER)
    return cleaned


def _club_out(club: NetworkClub, viewer_id: Optional[UUID] = None, *, db: Optional[Session] = None) -> dict:
    all_rows = list(club.members or [])
    active_members = sorted(
        [m for m in all_rows if m.role != "pending"],
        key=lambda m: (0 if m.role == "president" else 1, m.joined_at or club.created_at),
    )
    pending_rows = sorted(
        [m for m in all_rows if m.role == "pending"],
        key=lambda m: m.joined_at or club.created_at,
    )
    president = next((m for m in active_members if m.role == "president"), None)
    viewer_member = next((m for m in active_members if viewer_id and m.user_id == viewer_id), None)
    viewer_pending = next((m for m in pending_rows if viewer_id and m.user_id == viewer_id), None)
    viewer_is_exec = bool(viewer_member and viewer_member.role in EXEC_ROLES)
    private = _is_private(club)
    visible_tags = [t for t in (club.tags or []) if t != PRIVATE_TAG_MARKER]
    announcement_count = 0
    event_count = 0
    if db is not None:
        try:
            announcement_count = (
                db.query(ClubAnnouncement)
                .filter(ClubAnnouncement.club_id == club.id, ClubAnnouncement.status == "published")
                .count()
            )
            event_count = (
                db.query(ClubEvent)
                .filter(ClubEvent.club_id == club.id, ClubEvent.status == "published")
                .count()
            )
        except Exception:
            # Schema still migrating on a shared DB — don't take down the club list.
            db.rollback()
            announcement_count = 0
            event_count = 0
    return {
        "id": str(club.id),
        "slug": club.slug,
        "name": club.name,
        "tagline": club.tagline or "",
        "about": club.about or "",
        "category": club.category or "general",
        "visibility": "private" if private else "public",
        "bannerUrl": club.banner_url,
        "avatarUrl": club.avatar_url,
        "pendingAvatarUrl": getattr(club, "pending_avatar_url", None) if viewer_is_exec else None,
        "pendingAvatarApprovals": list(getattr(club, "pending_avatar_approvals", None) or [])
        if viewer_is_exec
        else [],
        "pendingAvatarNeeded": _logo_approvals_needed(club) if viewer_is_exec else 0,
        "hostId": str(president.user_id) if president else (str(club.created_by) if club.created_by else ""),
        "hostName": display_name(president.user) if president and president.user else "",
        "hostUsername": president.user.username if president and president.user else "",
        "hostTitle": (president.custom_title if president else None) or "President",
        "memberCount": len(active_members),
        "projectCount": 0,
        "announcementCount": announcement_count,
        "eventCount": event_count,
        "institution": getattr(club, "institution", None),
        "joinPolicy": getattr(club, "join_policy", None) or "open",
        "contactSocials": dict(getattr(club, "contact_socials", None) or {}),
        "purpose": getattr(club, "purpose", None),
        "fundsVisibility": getattr(club, "funds_visibility", None) or "members",
        "verified": bool(getattr(club, "verified_at", None))
        or (getattr(club, "verification_status", None) == "verified"),
        "verifiedAt": club.verified_at.isoformat() if getattr(club, "verified_at", None) else None,
        "verificationStatus": getattr(club, "verification_status", None) or (
            "verified" if getattr(club, "verified_at", None) else "none"
        ),
        "verificationNote": getattr(club, "verification_note", None),
        "isMember": bool(viewer_member),
        "hasPendingRequest": bool(viewer_pending),
        "userRole": FRONT_ROLE.get(viewer_member.role, viewer_member.role) if viewer_member else None,
        "userCustomTitle": viewer_member.custom_title if viewer_member else None,
        "hideOnProfile": bool(getattr(viewer_member, "hide_on_profile", True)) if viewer_member else None,
        "emailBroadcastOptIn": bool(getattr(viewer_member, "email_broadcast_opt_in", True))
        if viewer_member
        else None,
        "members": [_member_out(m, include_email=viewer_is_exec) for m in active_members],
        "pendingRequests": [_member_out(m, include_email=viewer_is_exec) for m in pending_rows] if viewer_is_exec else [],
        "tags": visible_tags,
        "policies": club.policies or "",
        "customRoles": [{"id": str(r.id), "title": r.title} for r in (club.custom_roles or [])],
        "createdAt": club.created_at.isoformat() if club.created_at else None,
    }


def _logo_approvals_needed(club: NetworkClub) -> int:
    exec_count = sum(1 for m in (club.members or []) if m.role in EXEC_ROLES)
    return min(3, max(exec_count, 1))


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


def _require_role_manager(club: NetworkClub, user_id: UUID) -> ClubMember:
    """President manages roles; if the club has no president yet, the founder (created_by) may."""
    has_president = any(m.role == "president" for m in (club.members or []))
    m = next((x for x in club.members if x.user_id == user_id), None)
    if m and m.role == "president":
        return m
    if not has_president and club.created_by and club.created_by == user_id and m:
        return m
    raise HTTPException(status_code=403, detail="Only the club president can do that")


class ClubCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    tagline: str = Field(default="", max_length=280)
    about: str = Field(default="", max_length=8000)
    category: str = Field(default="general", max_length=40)
    visibility: str = Field(default="public", max_length=16)
    tags: list[str] = Field(default_factory=list)
    policies: str = Field(default="", max_length=12000)
    bannerUrl: Optional[str] = None
    avatarUrl: Optional[str] = None
    hostRole: Optional[str] = Field(default=None, max_length=40)
    hostTitle: Optional[str] = Field(default=None, max_length=120)
    institution: Optional[str] = Field(default=None, max_length=200)
    joinPolicy: str = Field(default="open", max_length=20)
    contactSocials: dict = Field(default_factory=dict)
    purpose: Optional[str] = Field(default=None, max_length=4000)
    fundsVisibility: str = Field(default="members", max_length=20)


class ClubPatchIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    tagline: Optional[str] = Field(default=None, max_length=280)
    about: Optional[str] = Field(default=None, max_length=8000)
    category: Optional[str] = Field(default=None, max_length=40)
    visibility: Optional[str] = Field(default=None, max_length=16)
    tags: Optional[list[str]] = None
    policies: Optional[str] = Field(default=None, max_length=12000)
    bannerUrl: Optional[str] = None
    avatarUrl: Optional[str] = None
    institution: Optional[str] = Field(default=None, max_length=200)
    joinPolicy: Optional[str] = Field(default=None, max_length=20)
    contactSocials: Optional[dict] = None
    purpose: Optional[str] = Field(default=None, max_length=4000)
    fundsVisibility: Optional[str] = Field(default=None, max_length=20)


class MemberRoleIn(BaseModel):
    role: str
    customTitle: Optional[str] = Field(default=None, max_length=120)
    confirmation: Optional[str] = Field(default=None, max_length=200)


class CustomRoleIn(BaseModel):
    title: str = Field(min_length=2, max_length=80)


@router.get("")
def list_groups(
    q: Optional[str] = None,
    category: Optional[str] = None,
    institution: Optional[str] = None,
    verified: Optional[bool] = None,
    joined: Optional[bool] = None,
    openToJoin: Optional[bool] = None,
    upcomingEvents: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    query = db.query(NetworkClub).options(
        selectinload(NetworkClub.members).selectinload(ClubMember.user).selectinload(User.profile),
        selectinload(NetworkClub.custom_roles),
    )
    if category and category != "all":
        query = query.filter(NetworkClub.category == category)
    if institution and institution.strip():
        query = query.filter(NetworkClub.institution.ilike(f"%{institution.strip()}%"))
    if verified is True:
        query = query.filter(NetworkClub.verified_at.isnot(None))
    elif verified is False:
        query = query.filter(NetworkClub.verified_at.is_(None))
    if openToJoin is True:
        query = query.filter(NetworkClub.join_policy == "open")
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(NetworkClub.name.ilike(term), NetworkClub.tagline.ilike(term), NetworkClub.about.ilike(term))
        )
    if upcomingEvents is True:
        now = datetime.now(timezone.utc)
        query = query.filter(
            NetworkClub.id.in_(
                db.query(ClubEvent.club_id).filter(
                    ClubEvent.status == "published",
                    ClubEvent.starts_at >= now,
                )
            )
        )
    if joined is True and viewer:
        query = query.filter(
            NetworkClub.id.in_(db.query(ClubMember.club_id).filter(ClubMember.user_id == viewer.id))
        )
    elif joined is False and viewer:
        query = query.filter(
            ~NetworkClub.id.in_(db.query(ClubMember.club_id).filter(ClubMember.user_id == viewer.id))
        )
    try:
        total = query.count()
        clubs = (
            query.order_by(NetworkClub.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        items = [_club_out(c, viewer.id if viewer else None, db=db) for c in clubs]
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Club directory is temporarily unavailable (schema update pending). Retry shortly.",
        ) from exc
    return {
        "items": items,
        "page": page,
        "limit": limit,
        "total": total,
    }


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
        .filter(ClubMember.user_id == me.id, ClubMember.role != "pending")
        .order_by(ClubMember.joined_at.desc())
        .all()
    )
    out = []
    for m in memberships:
        if not m.club:
            continue
        row = _club_out(m.club, me.id, db=db)
        row["membershipRole"] = FRONT_ROLE.get(m.role, m.role)
        row["membershipTitle"] = m.custom_title
        row["membershipJoinedAt"] = m.joined_at.isoformat() if m.joined_at else None
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
        .filter(ClubMember.user_id == user_id, ClubMember.role != "pending")
        .order_by(ClubMember.joined_at.desc())
        .all()
    )
    is_self = bool(viewer and viewer.id == user_id)
    out = []
    for m in memberships:
        if not m.club:
            continue
        if not is_self and getattr(m, "hide_on_profile", True):
            continue
        row = _club_out(m.club, viewer.id if viewer else None, db=db)
        row["membershipRole"] = FRONT_ROLE.get(m.role, m.role)
        row["membershipTitle"] = m.custom_title
        row["membershipJoinedAt"] = m.joined_at.isoformat() if m.joined_at else None
        out.append(row)
    return out


@router.get("/{club_id}")
def get_group(
    club_id: str,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    club = _load_club(db, club_id)
    return _club_out(club, viewer.id if viewer else None, db=db)


@router.post("", dependencies=[rate_limit("club-create", 6, 600)])
def create_group(data: ClubCreateIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    # One presidency per person: if they already lead another club, join this one as Founder (executive).
    already_president = (
        db.query(ClubMember.club_id)
        .filter(ClubMember.user_id == me.id, ClubMember.role == "president")
        .first()
        is not None
    )
    requested_role = ROLE_ALIASES.get((data.hostRole or "").strip().lower())
    if requested_role:
        role = "executive" if (requested_role == "president" and already_president) else requested_role
    else:
        role = "executive" if already_president else "president"
    default_title = {
        "president": "President",
        "vice_president": "Vice President",
        "treasurer": "Treasurer",
        "executive": "Founder" if already_president else "Executive Member",
        "member": "Member",
    }.get(role, "Founder")
    title = (data.hostTitle or "").strip() or default_title

    join_policy = (data.joinPolicy or "open").strip().lower()
    if join_policy not in ("open", "request", "invite"):
        join_policy = "open"
    club = NetworkClub(
        id=uuid.uuid4(),
        slug=_unique_slug(db, data.name),
        name=data.name.strip(),
        tagline=(data.tagline or "").strip(),
        about=(data.about or "").strip(),
        category=(data.category or "general").strip()[:40],
        tags=_clean_tags(data.tags, private=(data.visibility or "").strip().lower() == "private"),
        policies=(data.policies or "").strip(),
        banner_url=(data.bannerUrl or None),
        avatar_url=(data.avatarUrl or None),
        institution=(data.institution or None),
        join_policy=join_policy,
        contact_socials=dict(data.contactSocials or {}),
        purpose=(data.purpose or None),
        funds_visibility=(
            data.fundsVisibility
            if (data.fundsVisibility or "").strip().lower() in ("members", "executives")
            else "members"
        ),
        created_by=me.id,
        verification_status="none",
    )
    db.add(club)
    db.flush()
    db.add(
        ClubMember(
            club_id=club.id,
            user_id=me.id,
            role=role,
            custom_title=title,
            email_broadcast_opt_in=True,
        )
    )
    from app.models.club_features import DEFAULT_PERMS, ClubRolePermission

    for r in ("vice_president", "treasurer", "executive", "member"):
        db.add(
            ClubRolePermission(
                id=uuid.uuid4(),
                club_id=club.id,
                role=r,
                permissions=dict(DEFAULT_PERMS),
            )
        )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Could not create this club (presidency conflict). Transfer an existing presidency or try again.",
        )
    return _club_out(_load_club(db, str(club.id)), me.id, db=db)


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
    if data.tags is not None or data.visibility is not None:
        is_priv = (
            (data.visibility or "").strip().lower() == "private"
            if data.visibility is not None
            else _is_private(club)
        )
        base_tags = data.tags if data.tags is not None else [t for t in (club.tags or []) if t != PRIVATE_TAG_MARKER]
        club.tags = _clean_tags(base_tags, private=is_priv)
    if data.policies is not None:
        club.policies = data.policies.strip()
    if data.bannerUrl is not None:
        club.banner_url = data.bannerUrl or None
    if data.avatarUrl is not None:
        club.avatar_url = data.avatarUrl or None
    if data.institution is not None:
        club.institution = data.institution.strip() or None
    if data.joinPolicy is not None:
        jp = data.joinPolicy.strip().lower()
        if jp not in ("open", "request", "invite"):
            raise HTTPException(status_code=400, detail="Invalid joinPolicy")
        club.join_policy = jp
    if data.contactSocials is not None:
        club.contact_socials = dict(data.contactSocials)
    if data.purpose is not None:
        club.purpose = data.purpose.strip() or None
    if data.fundsVisibility is not None:
        fv = data.fundsVisibility.strip().lower()
        if fv not in ("members", "executives"):
            raise HTTPException(status_code=400, detail="fundsVisibility must be members or executives")
        club.funds_visibility = fv
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id, db=db)


@router.post("/{club_id}/join", dependencies=[rate_limit("club-join", 20, 60)])
def join_group(club_id: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    club = _load_club(db, club_id)
    existing = next((m for m in club.members if m.user_id == me.id), None)
    if existing:
        return _club_out(club, me.id, db=db)

    policy = (getattr(club, "join_policy", None) or "open").lower()
    if policy == "invite":
        raise HTTPException(status_code=403, detail="This club is invite-only")

    if _is_private(club) or policy == "request":
        db.add(ClubMember(club_id=club.id, user_id=me.id, role="pending", custom_title="Pending Request"))
        requester = display_name(me)
        exec_members = [m for m in club.members if m.role in EXEC_ROLES and m.user_id != me.id]
        msg_text = (
            f"Club Join Request: {requester} (@{me.username}) requested to enter the private club "
            f"\"{club.name}\". Review and approve in the club's Members tab: /network/groups/{club.slug}"
        )
        for ex in exec_members:
            msg = Message(
                id=uuid.uuid4(),
                sender_id=me.id,
                receiver_id=ex.user_id,
                content=msg_text,
                read=False,
            )
            db.add(msg)
            notify(
                db,
                ex.user_id,
                "message",
                f"{requester} requested to join private club \"{club.name}\"",
                related_id=me.id,
                actor_id=me.id,
            )
            queue_event(
                db,
                ex.user_id,
                "message",
                {"sender_id": str(me.id), "club_id": str(club.id), "preview": msg_text[:140]},
            )
        db.commit()
        return _club_out(_load_club(db, str(club.id)), me.id, db=db)

    db.add(
        ClubMember(
            club_id=club.id,
            user_id=me.id,
            role="member",
            custom_title="Member",
            email_broadcast_opt_in=True,
        )
    )
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id, db=db)


@router.post("/{club_id}/requests/{target_user_id}/approve")
def approve_join_request(
    club_id: str,
    target_user_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_exec(club, me.id)
    req = next((m for m in club.members if m.user_id == target_user_id and m.role == "pending"), None)
    if not req:
        raise HTTPException(status_code=404, detail="Pending join request not found")
    req.role = "member"
    req.custom_title = "Member"
    welcome = (
        f"Your request to join \"{club.name}\" has been approved! "
        f"Enter the channel here: /network/groups/{club.slug}"
    )
    db.add(
        Message(
            id=uuid.uuid4(),
            sender_id=me.id,
            receiver_id=target_user_id,
            content=welcome,
            read=False,
        )
    )
    notify(
        db,
        target_user_id,
        "message",
        f"Your request to join \"{club.name}\" was approved!",
        related_id=me.id,
        actor_id=me.id,
    )
    queue_event(
        db,
        target_user_id,
        "message",
        {"sender_id": str(me.id), "club_id": str(club.id), "preview": welcome[:140]},
    )
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)


@router.post("/{club_id}/requests/{target_user_id}/decline")
def decline_join_request(
    club_id: str,
    target_user_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_exec(club, me.id)
    req = next((m for m in club.members if m.user_id == target_user_id and m.role == "pending"), None)
    if not req:
        raise HTTPException(status_code=404, detail="Pending join request not found")
    db.delete(req)
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
    actor = _require_role_manager(club, me.id)
    role = ROLE_ALIASES.get((data.role or "").strip().lower())
    if not role:
        raise HTTPException(status_code=400, detail="Invalid role")
    target = next((m for m in club.members if m.user_id == target_user_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Member not found")
    if (
        actor.role == "president"
        and target.user_id == actor.user_id
        and role != "president"
    ):
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
        confirmation = (getattr(data, "confirmation", None) or "").strip().lower()
        if confirmation != (club.name or "").strip().lower():
            raise HTTPException(
                status_code=400,
                detail="Type the exact club name to confirm presidency transfer",
            )
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


@router.post("/{club_id}/banner", dependencies=[rate_limit("club-banner", 20, 60)])
def upload_club_banner(
    club_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    """Cover banner updates immediately — no executive approval required."""
    club = _load_club(db, club_id)
    _require_exec(club, me.id)
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if not _is_image_upload(file, ext):
        raise HTTPException(status_code=400, detail="Use a PNG, JPG, GIF, or WebP image")
    file.file.seek(0, 2)
    too_big = file.file.tell() > 8 * 1024 * 1024
    file.file.seek(0)
    if too_big:
        raise HTTPException(status_code=413, detail="Banner is larger than 8 MB")
    name = f"{club.id}_{uuid.uuid4().hex}.{ext}"
    storage.save(f"club-banners/{name}", file.file)
    club.banner_url = f"/storage/club-banners/{name}"
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)


@router.post("/{club_id}/avatar", dependencies=[rate_limit("club-avatar", 20, 60)])
def upload_club_avatar(
    club_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    """First logo publishes immediately; later logo changes need up to 3 executive approvals."""
    club = _load_club(db, club_id)
    _require_exec(club, me.id)
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if not _is_image_upload(file, ext):
        raise HTTPException(status_code=400, detail="Use a PNG, JPG, GIF, or WebP image")
    file.file.seek(0, 2)
    too_big = file.file.tell() > 5 * 1024 * 1024
    file.file.seek(0)
    if too_big:
        raise HTTPException(status_code=413, detail="Logo is larger than 5 MB")
    name = f"{club.id}_{uuid.uuid4().hex}.{ext}"
    storage.save(f"club-avatars/{name}", file.file)
    url = f"/storage/club-avatars/{name}"
    # Initial logo (required for every club) goes live immediately
    if not club.avatar_url:
        club.avatar_url = url
        club.pending_avatar_url = None
        club.pending_avatar_approvals = []
    else:
        needed = _logo_approvals_needed(club)
        club.pending_avatar_url = url
        club.pending_avatar_approvals = [str(me.id)]
        if len(club.pending_avatar_approvals) >= needed:
            club.avatar_url = url
            club.pending_avatar_url = None
            club.pending_avatar_approvals = []
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)


@router.post("/{club_id}/avatar/approve", dependencies=[rate_limit("club-avatar-approve", 30, 60)])
def approve_club_avatar(
    club_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_exec(club, me.id)
    pending = getattr(club, "pending_avatar_url", None)
    if not pending:
        raise HTTPException(status_code=400, detail="No pending logo to approve")
    approvals = list(getattr(club, "pending_avatar_approvals", None) or [])
    uid = str(me.id)
    if uid not in approvals:
        approvals.append(uid)
    club.pending_avatar_approvals = approvals
    needed = _logo_approvals_needed(club)
    if len(approvals) >= needed:
        club.avatar_url = pending
        club.pending_avatar_url = None
        club.pending_avatar_approvals = []
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)


@router.delete("/{club_id}/avatar/pending", dependencies=[rate_limit("club-avatar-cancel", 20, 60)])
def cancel_pending_avatar(
    club_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_president(club, me.id)
    club.pending_avatar_url = None
    club.pending_avatar_approvals = []
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)
