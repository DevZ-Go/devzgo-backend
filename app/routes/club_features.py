"""Club events, announcements, polls, funds, galleries, permissions, verification."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, get_current_user_optional, require_developer
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.club_features import (
    DEFAULT_PERMS,
    PRESIDENT_PERMS,
    ClubAnnouncement,
    ClubEvent,
    ClubEventMedia,
    ClubFundEntry,
    ClubPoll,
    ClubPollOption,
    ClubPollVote,
    ClubRolePermission,
)
from app.models.club_showcase import ClubShowcase
from app.models.clubs import EXEC_ROLES, ClubMember, NetworkClub
from app.models.user import User
from app.routes.clubs import _club_out, _load_club, _require_president
from app.services import storage
from app.services.social import display_name

router = APIRouter(prefix="/groups", tags=["Network club features"])

BANNER_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})


def _normalize_media(items: list) -> list:
    out = []
    for item in items or []:
        if isinstance(item, str) and item.strip():
            url = item.strip()
            kind = "video" if any(x in url.lower() for x in ("youtube", "youtu.be", "vimeo", ".mp4", ".webm")) else "image"
            out.append({"url": url, "type": kind, "source": "url"})
        elif isinstance(item, dict) and item.get("url"):
            out.append(
                {
                    "url": str(item["url"]),
                    "type": item.get("type") or "image",
                    "source": item.get("source") or "upload",
                }
            )
        if len(out) >= 12:
            break
    return out


def _member(club: NetworkClub, user_id: UUID) -> Optional[ClubMember]:
    return next((m for m in (club.members or []) if m.user_id == user_id), None)


def _has_perm(db: Session, club: NetworkClub, user_id: UUID, perm: str) -> bool:
    """President has all perms. Other roles only get what the role matrix assigns."""
    m = _member(club, user_id)
    if not m:
        return False
    if m.role == "president":
        return True
    row = (
        db.query(ClubRolePermission)
        .filter(ClubRolePermission.club_id == club.id, ClubRolePermission.role == m.role)
        .first()
    )
    if row and isinstance(row.permissions, dict):
        return bool(row.permissions.get(perm))
    return False


def _require_perm(db: Session, club: NetworkClub, user_id: UUID, perm: str) -> ClubMember:
    m = _member(club, user_id)
    if not m or not _has_perm(db, club, user_id, perm):
        raise HTTPException(status_code=403, detail=f"Missing permission: {perm}")
    return m


def _require_member(club: NetworkClub, user_id: UUID) -> ClubMember:
    m = _member(club, user_id)
    if not m:
        raise HTTPException(status_code=403, detail="Members only")
    return m


def _academic_from_date(dt: datetime) -> tuple[str, str]:
    month = dt.month
    year = dt.year
    if month >= 9:
        return "Fall", str(year)
    if month >= 5:
        return "Spring/Summer", str(year)
    return "Winter", str(year)


def _effective_poll_status(poll: ClubPoll) -> str:
    if poll.status == "closed":
        return "closed"
    if poll.closes_at and poll.closes_at <= datetime.now(timezone.utc):
        return "closed"
    return poll.status or "open"


def _announcement_out(a: ClubAnnouncement, author: Optional[User] = None) -> dict:
    profile = getattr(author, "profile", None) if author else None
    return {
        "id": str(a.id),
        "groupId": str(a.club_id),
        "authorId": str(a.author_user_id) if a.author_user_id else "",
        "authorName": display_name(author) if author else "Member",
        "authorUsername": author.username if author else "unknown",
        "authorAvatar": profile.avatar_url if profile else None,
        "authorRole": "executive",
        "authorTitle": None,
        "badge": a.badge or "General Update",
        "title": a.title,
        "content": a.body or "",
        "body": a.body or "",
        "mediaUrls": list(a.media_urls or []),
        "pinned": bool(a.pinned),
        "status": a.status,
        "audience": getattr(a, "audience", None) or "members",
        "eventId": str(a.event_id) if a.event_id else None,
        "createdAt": a.created_at.isoformat() if a.created_at else None,
        "likesCount": 0,
        "isLiked": False,
        "commentsCount": 0,
        "comments": [],
    }


def _event_out(e: ClubEvent) -> dict:
    return {
        "id": str(e.id),
        "clubId": str(e.club_id),
        "title": e.title,
        "description": e.description or "",
        "location": e.location,
        "startsAt": e.starts_at.isoformat() if e.starts_at else None,
        "endsAt": e.ends_at.isoformat() if e.ends_at else None,
        "timezone": getattr(e, "timezone", None),
        "modality": getattr(e, "modality", None) or "in_person",
        "venueName": getattr(e, "venue_name", None),
        "venueAddress": getattr(e, "venue_address", None),
        "roomNumber": getattr(e, "room_number", None),
        "meetingUrl": getattr(e, "meeting_url", None),
        "meetingPlatform": getattr(e, "meeting_platform", None),
        "posterUrl": getattr(e, "poster_url", None),
        "posterFormat": getattr(e, "poster_format", None),
        "posterOrientation": getattr(e, "poster_orientation", None),
        "rsvpUrl": getattr(e, "rsvp_url", None),
        "attractions": getattr(e, "attractions", None),
        "status": e.status,
        "academicTerm": e.academic_term,
        "academicYear": e.academic_year,
        "createdAt": e.created_at.isoformat() if e.created_at else None,
    }


def _poll_out(poll: ClubPoll, viewer_id: Optional[UUID] = None) -> dict:
    status = _effective_poll_status(poll)
    if status == "closed" and poll.status != "closed":
        poll.status = "closed"
    my_vote = None
    if viewer_id:
        v = next((x for x in (poll.votes or []) if x.user_id == viewer_id), None)
        if v:
            my_vote = str(v.option_id)
    total = len(poll.votes or [])
    options = []
    for o in sorted(poll.options or [], key=lambda x: x.sort_order or "0"):
        votes = sum(1 for v in (poll.votes or []) if v.option_id == o.id)
        options.append(
            {
                "id": str(o.id),
                "label": o.label,
                "votes": votes,
                "percent": round((votes / total) * 100) if total else 0,
            }
        )
    return {
        "id": str(poll.id),
        "clubId": str(poll.club_id),
        "announcementId": str(poll.announcement_id) if poll.announcement_id else None,
        "question": poll.question,
        "status": status,
        "closesAt": poll.closes_at.isoformat() if getattr(poll, "closes_at", None) else None,
        "allowVoteChange": bool(getattr(poll, "allow_vote_change", True)),
        "options": options,
        "totalVotes": total,
        "myVoteOptionId": my_vote,
        "myOptionId": my_vote,
        "createdAt": poll.created_at.isoformat() if poll.created_at else None,
    }


# ── Permissions ──────────────────────────────────────────────────────────────

class PermissionsIn(BaseModel):
    role: str
    permissions: dict[str, bool]


@router.get("/{club_id}/my-permissions")
def my_permissions(club_id: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    club = _load_club(db, club_id)
    m = _require_member(club, me.id)
    return {k: _has_perm(db, club, me.id, k) for k in DEFAULT_PERMS.keys()} | {
        "role": m.role,
        "isPresident": m.role == "president",
    }


@router.get("/{club_id}/permissions")
def get_permissions(club_id: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    club = _load_club(db, club_id)
    _require_president(club, me.id)
    rows = db.query(ClubRolePermission).filter(ClubRolePermission.club_id == club.id).all()
    by_role = {r.role: {**DEFAULT_PERMS, **(r.permissions or {})} for r in rows}
    by_role["president"] = dict(PRESIDENT_PERMS)
    for role in ("vice_president", "treasurer", "executive", "member"):
        by_role.setdefault(role, dict(DEFAULT_PERMS))
    return by_role


@router.put("/{club_id}/permissions")
def put_permissions(
    club_id: str,
    data: PermissionsIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_president(club, me.id)
    role = (data.role or "").strip().lower()
    if role == "president":
        raise HTTPException(status_code=400, detail="President always has full permissions")
    if role not in ("vice_president", "treasurer", "executive", "member"):
        raise HTTPException(status_code=400, detail="Invalid role")
    perms = {k: bool(data.permissions.get(k, False)) for k in DEFAULT_PERMS}
    row = (
        db.query(ClubRolePermission)
        .filter(ClubRolePermission.club_id == club.id, ClubRolePermission.role == role)
        .first()
    )
    if not row:
        row = ClubRolePermission(id=uuid.uuid4(), club_id=club.id, role=role, permissions=perms)
        db.add(row)
    else:
        row.permissions = perms
    db.commit()
    return {role: perms}


# ── Presidency transfer ──────────────────────────────────────────────────────

class TransferIn(BaseModel):
    targetUserId: UUID
    confirmation: str = Field(min_length=1, max_length=200)


@router.post("/{club_id}/transfer-presidency")
def transfer_presidency(
    club_id: str,
    data: TransferIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    actor = _require_president(club, me.id)
    if (data.confirmation or "").strip().lower() != (club.name or "").strip().lower():
        raise HTTPException(
            status_code=400,
            detail="Type the exact club name to confirm presidency transfer",
        )
    target = _member(club, data.targetUserId)
    if not target:
        raise HTTPException(status_code=404, detail="Target must be a club member")
    if target.user_id == actor.user_id:
        return _club_out(club, me.id)
    actor.role = "executive"
    if not actor.custom_title or actor.custom_title.lower() == "president":
        actor.custom_title = "Executive"
    target.role = "president"
    target.custom_title = "President"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Target already presidents another club")
    return _club_out(_load_club(db, str(club.id)), me.id)


# ── Announcements ────────────────────────────────────────────────────────────

class AnnouncementIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=20000)
    body: Optional[str] = Field(default=None, max_length=20000)
    badge: str = Field(default="General Update", max_length=60)
    pinned: bool = False
    status: str = Field(default="published", max_length=20)
    audience: str = Field(default="members", max_length=20)  # public | members
    eventId: Optional[UUID] = None
    mediaUrls: list = Field(default_factory=list)
    pollQuestion: Optional[str] = Field(default=None, max_length=400)
    pollOptions: list[str] = Field(default_factory=list)
    pollClosesAt: Optional[datetime] = None
    pollDurationDays: Optional[int] = Field(default=None, ge=1, le=90)


@router.get("/{club_id}/announcements")
def list_announcements(
    club_id: str,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    club = _load_club(db, club_id)
    q = db.query(ClubAnnouncement).filter(ClubAnnouncement.club_id == club.id)
    member = _member(club, viewer.id) if viewer else None
    if not member:
        q = q.filter(
            ClubAnnouncement.status == "published",
            ClubAnnouncement.audience == "public",
        )
    else:
        q = q.filter(ClubAnnouncement.status == "published")
    rows = q.order_by(ClubAnnouncement.pinned.desc(), ClubAnnouncement.created_at.desc()).limit(100).all()
    authors = {
        u.id: u
        for u in db.query(User)
        .options(selectinload(User.profile))
        .filter(User.id.in_([r.author_user_id for r in rows if r.author_user_id]))
        .all()
    } if rows else {}
    return [_announcement_out(a, authors.get(a.author_user_id) if a.author_user_id else None) for a in rows]


@router.post("/{club_id}/announcements", dependencies=[rate_limit("club-announce", 30, 60)])
def create_announcement(
    club_id: str,
    data: AnnouncementIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "announcements")
    body = (data.body if data.body is not None else data.content) or ""
    audience = (data.audience or "members").strip().lower()
    if audience not in ("public", "members"):
        audience = "members"
    row = ClubAnnouncement(
        id=uuid.uuid4(),
        club_id=club.id,
        title=data.title.strip(),
        body=body.strip(),
        badge=(data.badge or "General Update").strip()[:60],
        pinned=bool(data.pinned),
        status=(data.status or "published").strip()[:20],
        audience=audience,
        event_id=data.eventId,
        media_urls=_normalize_media(data.mediaUrls),
        author_user_id=me.id,
    )
    db.add(row)
    db.flush()
    if data.pollQuestion and data.pollOptions:
        _require_perm(db, club, me.id, "polls")
        from datetime import timedelta

        closes = data.pollClosesAt or (
            datetime.now(timezone.utc) + timedelta(days=data.pollDurationDays or 5)
        )
        poll = ClubPoll(
            id=uuid.uuid4(),
            club_id=club.id,
            announcement_id=row.id,
            question=data.pollQuestion.strip(),
            closes_at=closes,
            allow_vote_change=True,
            created_by_user_id=me.id,
        )
        db.add(poll)
        db.flush()
        for i, label in enumerate(data.pollOptions[:8]):
            if not label.strip():
                continue
            db.add(
                ClubPollOption(
                    id=uuid.uuid4(),
                    poll_id=poll.id,
                    label=label.strip()[:200],
                    sort_order=str(i),
                )
            )
    db.commit()
    author = db.query(User).options(selectinload(User.profile)).filter(User.id == me.id).first()
    return _announcement_out(row, author)


@router.patch("/{club_id}/announcements/{announcement_id}")
def patch_announcement(
    club_id: str,
    announcement_id: UUID,
    data: AnnouncementIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "announcements")
    row = db.query(ClubAnnouncement).filter_by(id=announcement_id, club_id=club.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Announcement not found")
    body = (data.body if data.body is not None else data.content) or ""
    row.title = data.title.strip()
    row.body = body.strip()
    row.badge = (data.badge or row.badge).strip()[:60]
    row.pinned = bool(data.pinned)
    row.status = (data.status or row.status).strip()[:20]
    if data.eventId is not None:
        row.event_id = data.eventId
    if data.mediaUrls is not None:
        row.media_urls = _normalize_media(data.mediaUrls)
    db.commit()
    author = (
        db.query(User).options(selectinload(User.profile)).filter(User.id == row.author_user_id).first()
        if row.author_user_id
        else None
    )
    return _announcement_out(row, author)


@router.delete("/{club_id}/announcements/{announcement_id}", status_code=204)
def delete_announcement(
    club_id: str,
    announcement_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "announcements")
    row = db.query(ClubAnnouncement).filter_by(id=announcement_id, club_id=club.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Announcement not found")
    db.delete(row)
    db.commit()
    return None


# ── Events ───────────────────────────────────────────────────────────────────

class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)
    location: Optional[str] = Field(default=None, max_length=280)
    startsAt: datetime
    endsAt: Optional[datetime] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    modality: str = Field(default="in_person", max_length=20)
    venueName: Optional[str] = Field(default=None, max_length=200)
    venueAddress: Optional[str] = None
    roomNumber: Optional[str] = Field(default=None, max_length=80)
    meetingUrl: Optional[str] = None
    meetingPlatform: Optional[str] = Field(default=None, max_length=80)
    posterUrl: Optional[str] = None
    posterFormat: Optional[str] = Field(default=None, max_length=20)
    posterOrientation: Optional[str] = Field(default=None, max_length=20)
    rsvpUrl: Optional[str] = None
    attractions: Optional[str] = Field(default=None, max_length=1000)
    status: str = Field(default="published", max_length=20)
    academicTerm: Optional[str] = Field(default=None, max_length=40)
    academicYear: Optional[str] = Field(default=None, max_length=20)
    notifyEmail: bool = False


def _apply_event_fields(row: ClubEvent, data: EventIn) -> None:
    modality = (data.modality or "in_person").strip().lower()
    if modality not in ("in_person", "virtual", "hybrid"):
        modality = "in_person"
    status = (data.status or row.status or "draft").strip().lower()
    if status not in ("draft", "published", "cancelled"):
        status = "draft"
    term, year = _academic_from_date(data.startsAt)
    row.title = data.title.strip()
    row.description = (data.description or "").strip()
    row.location = data.location
    row.starts_at = data.startsAt
    row.ends_at = data.endsAt
    row.timezone = (data.timezone or None)
    row.modality = modality
    row.venue_name = data.venueName
    row.venue_address = data.venueAddress
    row.room_number = (data.roomNumber or "").strip() or None
    row.meeting_url = data.meetingUrl
    row.meeting_platform = data.meetingPlatform
    row.poster_url = data.posterUrl
    row.poster_format = data.posterFormat
    row.poster_orientation = data.posterOrientation
    row.rsvp_url = data.rsvpUrl
    row.attractions = (data.attractions or "").strip() or None
    row.status = status
    row.academic_term = data.academicTerm or term
    row.academic_year = data.academicYear or year


def _queue_event_email(db: Session, club, event: ClubEvent, actor: User) -> int:
    from app.services import email_service

    members = [
        m
        for m in (club.members or [])
        if bool(getattr(m, "email_broadcast_opt_in", True))
    ]
    when = event.starts_at.strftime("%b %d, %Y %H:%M UTC") if event.starts_at else "TBA"
    place_bits = []
    if event.modality != "virtual":
        if event.venue_name:
            place_bits.append(event.venue_name)
        if event.room_number:
            place_bits.append(f"Room {event.room_number}")
        if event.venue_address:
            place_bits.append(event.venue_address)
    else:
        place_bits.append(event.meeting_platform or event.meeting_url or "Virtual")
    place = " · ".join(place_bits) if place_bits else "TBA"
    body = (
        f"{event.title}\n\n"
        f"{event.description or ''}\n\n"
        f"When: {when}\n"
        f"Where: {place}\n"
    )
    if event.attractions:
        body += f"\nHighlights: {event.attractions}\n"
    subject = f"New event: {event.title}"
    queued = 0
    for m in members:
        user = m.user
        if not user or not getattr(user, "email", None):
            continue
        email_service.enqueue(
            db,
            event_type="club_broadcast",
            template="club_broadcast",
            to_email=user.email,
            context={
                "username": user.username or "member",
                "club_name": club.name,
                "subject": subject,
                "body": body,
                "club_slug": club.slug,
            },
            idempotency_key=f"club_event:{club.id}:{event.id}:{user.id}",
            user_id=user.id,
        )
        queued += 1
    return queued


@router.get("/{club_id}/events")
def list_events(
    club_id: str,
    when: Optional[str] = Query(default=None, pattern="^(upcoming|past|all)$"),
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    club = _load_club(db, club_id)
    q = db.query(ClubEvent).filter(ClubEvent.club_id == club.id)
    member = _member(club, viewer.id) if viewer else None
    if not member:
        q = q.filter(ClubEvent.status == "published")
    now = datetime.now(timezone.utc)
    if when == "upcoming":
        q = q.filter(ClubEvent.starts_at >= now, ClubEvent.status != "cancelled")
        rows = q.order_by(ClubEvent.starts_at.asc()).limit(100).all()
    elif when == "past":
        q = q.filter(ClubEvent.starts_at < now)
        rows = q.order_by(ClubEvent.starts_at.desc()).limit(100).all()
    else:
        rows = q.order_by(ClubEvent.starts_at.desc()).limit(100).all()
    return [_event_out(e) for e in rows]


@router.post("/{club_id}/events", dependencies=[rate_limit("club-events", 30, 60)])
def create_event(
    club_id: str,
    data: EventIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "events")
    row = ClubEvent(id=uuid.uuid4(), club_id=club.id, created_by_user_id=me.id)
    _apply_event_fields(row, data)
    db.add(row)
    db.commit()
    out = _event_out(row)
    if data.notifyEmail and row.status == "published":
        try:
            _require_perm(db, club, me.id, "broadcast_email")
            out["emailsQueued"] = _queue_event_email(db, club, row, me)
            db.commit()
        except HTTPException:
            out["emailsQueued"] = 0
            out["emailSkipped"] = "Missing broadcast_email permission"
    return out


@router.patch("/{club_id}/events/{event_id}")
def patch_event(
    club_id: str,
    event_id: UUID,
    data: EventIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "events")
    row = db.query(ClubEvent).filter_by(id=event_id, club_id=club.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    _apply_event_fields(row, data)
    db.commit()
    return _event_out(row)


@router.post("/{club_id}/events/{event_id}/poster", dependencies=[rate_limit("club-event-poster", 20, 60)])
def upload_event_poster(
    club_id: str,
    event_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "events")
    row = db.query(ClubEvent).filter_by(id=event_id, club_id=club.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in BANNER_EXTS or not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Use a PNG, JPG, GIF, or WebP image")
    file.file.seek(0, 2)
    too_big = file.file.tell() > 12 * 1024 * 1024
    file.file.seek(0)
    if too_big:
        raise HTTPException(status_code=413, detail="Poster is larger than 12 MB")
    name = f"{club.id}_{row.id}_{uuid.uuid4().hex}.{ext}"
    storage.save(f"club-event-posters/{name}", file.file)
    row.poster_url = f"/storage/club-event-posters/{name}"
    db.commit()
    return _event_out(row)


@router.post("/{club_id}/events/{event_id}/cancel")
def cancel_event(
    club_id: str,
    event_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "events")
    row = db.query(ClubEvent).filter_by(id=event_id, club_id=club.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    row.status = "cancelled"
    db.commit()
    return _event_out(row)


# ── Polls ────────────────────────────────────────────────────────────────────

class PollIn(BaseModel):
    question: str = Field(min_length=1, max_length=400)
    options: list[str] = Field(min_length=2, max_length=8)
    announcementId: Optional[UUID] = None
    closesAt: Optional[datetime] = None
    durationDays: Optional[int] = Field(default=None, ge=1, le=90)
    allowVoteChange: bool = True


class VoteIn(BaseModel):
    optionId: UUID


@router.post("/{club_id}/polls", dependencies=[rate_limit("club-polls", 30, 60)])
def create_poll(
    club_id: str,
    data: PollIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "polls")
    from datetime import timedelta

    closes = data.closesAt or (datetime.now(timezone.utc) + timedelta(days=data.durationDays or 5))
    poll = ClubPoll(
        id=uuid.uuid4(),
        club_id=club.id,
        announcement_id=data.announcementId,
        question=data.question.strip(),
        closes_at=closes,
        allow_vote_change=bool(data.allowVoteChange),
        created_by_user_id=me.id,
    )
    db.add(poll)
    db.flush()
    for i, label in enumerate(data.options):
        if not label.strip():
            continue
        db.add(ClubPollOption(id=uuid.uuid4(), poll_id=poll.id, label=label.strip()[:200], sort_order=str(i)))
    db.commit()
    poll = (
        db.query(ClubPoll)
        .options(selectinload(ClubPoll.options), selectinload(ClubPoll.votes))
        .filter(ClubPoll.id == poll.id)
        .first()
    )
    return _poll_out(poll, me.id)


@router.get("/{club_id}/polls")
def list_polls(
    club_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_member(club, me.id)
    rows = (
        db.query(ClubPoll)
        .options(selectinload(ClubPoll.options), selectinload(ClubPoll.votes))
        .filter(ClubPoll.club_id == club.id)
        .order_by(ClubPoll.created_at.desc())
        .limit(50)
        .all()
    )
    return [_poll_out(p, me.id) for p in rows]


@router.post("/{club_id}/polls/{poll_id}/vote", dependencies=[rate_limit("club-vote", 60, 60)])
def vote_poll(
    club_id: str,
    poll_id: UUID,
    data: VoteIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_member(club, me.id)
    poll = (
        db.query(ClubPoll)
        .options(selectinload(ClubPoll.options), selectinload(ClubPoll.votes))
        .filter(ClubPoll.id == poll_id, ClubPoll.club_id == club.id)
        .first()
    )
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    status = _effective_poll_status(poll)
    if status != "open":
        poll.status = "closed"
        db.commit()
        raise HTTPException(status_code=400, detail="Poll is closed")
    if not any(o.id == data.optionId for o in poll.options):
        raise HTTPException(status_code=400, detail="Invalid option")
    existing = next((v for v in poll.votes if v.user_id == me.id), None)
    if existing:
        if not bool(getattr(poll, "allow_vote_change", True)):
            raise HTTPException(status_code=409, detail="Vote already cast")
        existing.option_id = data.optionId
    else:
        db.add(ClubPollVote(id=uuid.uuid4(), poll_id=poll.id, option_id=data.optionId, user_id=me.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Already voted")
    poll = (
        db.query(ClubPoll)
        .options(selectinload(ClubPoll.options), selectinload(ClubPoll.votes))
        .filter(ClubPoll.id == poll.id)
        .first()
    )
    return _poll_out(poll, me.id)


@router.post("/{club_id}/polls/{poll_id}/close")
def close_poll(
    club_id: str,
    poll_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "polls")
    poll = (
        db.query(ClubPoll)
        .options(selectinload(ClubPoll.options), selectinload(ClubPoll.votes))
        .filter(ClubPoll.id == poll_id, ClubPoll.club_id == club.id)
        .first()
    )
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    poll.status = "closed"
    db.commit()
    return _poll_out(poll, me.id)


# ── Funds ────────────────────────────────────────────────────────────────────

class FundIn(BaseModel):
    amount: Decimal
    currency: str = Field(default="CAD", max_length=8)
    note: str = Field(default="", max_length=2000)
    entryType: str = Field(default="expense", max_length=20)
    publicDisclosure: bool = False


@router.get("/{club_id}/funds")
def list_funds(club_id: str, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    club = _load_club(db, club_id)
    m = _require_member(club, me.id)
    visibility = (getattr(club, "funds_visibility", None) or "members").lower()
    if visibility == "executives" and m.role not in EXEC_ROLES and m.role != "president":
        raise HTTPException(status_code=403, detail="Funds are visible to executives only")
    rows = (
        db.query(ClubFundEntry)
        .filter(ClubFundEntry.club_id == club.id)
        .order_by(ClubFundEntry.created_at.desc())
        .limit(200)
        .all()
    )
    income = sum((r.amount for r in rows if r.entry_type == "income"), Decimal("0"))
    expense = sum((r.amount for r in rows if r.entry_type == "expense"), Decimal("0"))
    return {
        "summary": {"income": float(income), "expense": float(expense), "balance": float(income - expense)},
        "entries": [
            {
                "id": str(r.id),
                "amount": float(r.amount),
                "currency": r.currency,
                "note": r.note,
                "entryType": r.entry_type,
                "publicDisclosure": bool(r.public_disclosure),
                "createdAt": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.post("/{club_id}/funds", dependencies=[rate_limit("club-funds", 30, 60)])
def add_fund(
    club_id: str,
    data: FundIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "funds")
    entry_type = (data.entryType or "expense").strip().lower()
    if entry_type not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="entryType must be income or expense")
    row = ClubFundEntry(
        id=uuid.uuid4(),
        club_id=club.id,
        amount=data.amount,
        currency=(data.currency or "CAD").upper()[:8],
        note=(data.note or "").strip(),
        entry_type=entry_type,
        public_disclosure=bool(data.publicDisclosure),
        created_by_user_id=me.id,
    )
    db.add(row)
    db.commit()
    return {
        "id": str(row.id),
        "amount": float(row.amount),
        "currency": row.currency,
        "note": row.note,
        "entryType": row.entry_type,
        "publicDisclosure": bool(row.public_disclosure),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


# ── Event galleries ──────────────────────────────────────────────────────────

@router.get("/{club_id}/gallery")
def list_gallery(
    club_id: str,
    term: Optional[str] = None,
    year: Optional[str] = None,
    eventId: Optional[UUID] = None,
    mediaType: Optional[str] = None,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    club = _load_club(db, club_id)
    q = db.query(ClubEventMedia).filter(ClubEventMedia.club_id == club.id)
    if term:
        q = q.filter(ClubEventMedia.academic_term == term)
    if year:
        q = q.filter(ClubEventMedia.academic_year == year)
    if eventId:
        q = q.filter(ClubEventMedia.event_id == eventId)
    if mediaType in ("image", "video"):
        q = q.filter(ClubEventMedia.media_type == mediaType)
    rows = q.order_by(ClubEventMedia.created_at.desc()).limit(200).all()
    return [
        {
            "id": str(r.id),
            "eventId": str(r.event_id),
            "url": r.url,
            "mediaType": r.media_type,
            "academicTerm": r.academic_term,
            "academicYear": r.academic_year,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/{club_id}/events/{event_id}/media", dependencies=[rate_limit("club-gallery", 60, 60)])
def upload_event_media(
    club_id: str,
    event_id: UUID,
    file: UploadFile = File(...),
    academicTerm: Optional[str] = None,
    academicYear: Optional[str] = None,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "galleries")
    event = db.query(ClubEvent).filter_by(id=event_id, club_id=club.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    content = file.content_type or ""
    is_image = ext in BANNER_EXTS and content.startswith("image/")
    is_video = ext in ("mp4", "webm", "mov") and content.startswith("video/")
    if not is_image and not is_video:
        raise HTTPException(status_code=400, detail="Upload an image or mp4/webm/mov video")
    file.file.seek(0, 2)
    # Keep originals high quality; images up to 20 MB, videos 80 MB
    limit = 20 * 1024 * 1024 if is_image else 80 * 1024 * 1024
    too_big = file.file.tell() > limit
    file.file.seek(0)
    if too_big:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB images / 80 MB video)")
    name = f"{club.id}_{event.id}_{uuid.uuid4().hex}.{ext}"
    storage.save(f"club-galleries/{name}", file.file)
    row = ClubEventMedia(
        id=uuid.uuid4(),
        event_id=event.id,
        club_id=club.id,
        url=f"/storage/club-galleries/{name}",
        media_type="image" if is_image else "video",
        academic_term=(academicTerm or event.academic_term),
        academic_year=(academicYear or event.academic_year),
        uploaded_by_user_id=me.id,
    )
    db.add(row)
    db.commit()
    return {
        "id": str(row.id),
        "eventId": str(row.event_id),
        "url": row.url,
        "mediaType": row.media_type,
        "academicTerm": row.academic_term,
        "academicYear": row.academic_year,
    }


# ── Verification (admin) ─────────────────────────────────────────────────────

class VerifyRejectIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/{club_id}/verify")
def verify_club(club_id: str, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    from app.services.project_intelligence.visitor_ai import is_platform_admin

    if not is_platform_admin(db, me):
        raise HTTPException(status_code=403, detail="Platform admin only")
    club = _load_club(db, club_id)
    club.verified_at = datetime.now(timezone.utc)
    club.verified_by = me.id
    club.verification_status = "verified"
    club.verification_note = None
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)


@router.post("/{club_id}/verify/reject")
def reject_verify(
    club_id: str,
    data: VerifyRejectIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    from app.services.project_intelligence.visitor_ai import is_platform_admin

    if not is_platform_admin(db, me):
        raise HTTPException(status_code=403, detail="Platform admin only")
    club = _load_club(db, club_id)
    club.verified_at = None
    club.verified_by = me.id
    club.verification_status = "rejected"
    club.verification_note = (data.note or "").strip() or None
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)


@router.delete("/{club_id}/verify")
def revoke_verify(club_id: str, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    from app.services.project_intelligence.visitor_ai import is_platform_admin

    if not is_platform_admin(db, me):
        raise HTTPException(status_code=403, detail="Platform admin only")
    club = _load_club(db, club_id)
    club.verified_at = None
    club.verified_by = None
    club.verification_status = "none"
    club.verification_note = None
    db.commit()
    return _club_out(_load_club(db, str(club.id)), me.id)


class BroadcastEmailIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)


@router.post("/{club_id}/broadcast-email", dependencies=[rate_limit("club-broadcast", 5, 3600)])
def broadcast_email(
    club_id: str,
    data: BroadcastEmailIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    from app.services import email_service

    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "broadcast_email")
    opted = [
        m
        for m in (club.members or [])
        if bool(getattr(m, "email_broadcast_opt_in", True))
    ]
    users = {
        u.id: u
        for u in db.query(User)
        .filter(User.id.in_([m.user_id for m in opted]))
        .all()
    } if opted else {}
    queued = 0
    for m in opted:
        user = users.get(m.user_id)
        if not user or not user.email:
            continue
        email_service.enqueue(
            db,
            event_type="club_broadcast",
            template="club_broadcast",
            to_email=user.email,
            context={
                "username": user.username or "member",
                "club_name": club.name,
                "subject": data.subject.strip(),
                "body": data.body.strip(),
                "club_slug": club.slug,
            },
            idempotency_key=f"club_broadcast:{club.id}:{user.id}:{uuid.uuid4().hex}",
            user_id=user.id,
            priority=40,
        )
        queued += 1
    db.commit()
    return {"queued": queued}


class EmailOptInIn(BaseModel):
    emailBroadcastOptIn: bool = True


@router.patch("/{club_id}/membership/email-opt-in")
def patch_email_opt_in(
    club_id: str,
    data: EmailOptInIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    m = _require_member(club, me.id)
    m.email_broadcast_opt_in = bool(data.emailBroadcastOptIn)
    db.commit()
    return {"emailBroadcastOptIn": bool(m.email_broadcast_opt_in)}


@router.get("/me/club-email-preferences")
def my_club_email_preferences(
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    rows = (
        db.query(ClubMember, NetworkClub)
        .join(NetworkClub, NetworkClub.id == ClubMember.club_id)
        .filter(ClubMember.user_id == me.id)
        .order_by(NetworkClub.name.asc())
        .all()
    )
    return [
        {
            "clubId": str(club.id),
            "clubName": club.name,
            "clubSlug": club.slug,
            "emailBroadcastOptIn": bool(getattr(m, "email_broadcast_opt_in", True)),
        }
        for m, club in rows
    ]


@router.post("/{club_id}/announcements/media", dependencies=[rate_limit("club-ann-media", 30, 60)])
def upload_announcement_media(
    club_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_perm(db, club, me.id, "announcements")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    content = file.content_type or ""
    is_image = ext in BANNER_EXTS and content.startswith("image/")
    is_video = ext in ("mp4", "webm", "mov") and content.startswith("video/")
    if not is_image and not is_video:
        raise HTTPException(status_code=400, detail="Upload an image or mp4/webm/mov video")
    file.file.seek(0, 2)
    limit = 8 * 1024 * 1024 if is_image else 80 * 1024 * 1024
    too_big = file.file.tell() > limit
    file.file.seek(0)
    if too_big:
        raise HTTPException(status_code=413, detail="File too large")
    name = f"{club.id}_{uuid.uuid4().hex}.{ext}"
    storage.save(f"club-announcement-media/{name}", file.file)
    return {
        "url": f"/storage/club-announcement-media/{name}",
        "type": "image" if is_image else "video",
        "source": "upload",
    }


# ── Membership profile privacy ───────────────────────────────────────────────

class PrivacyIn(BaseModel):
    hideOnProfile: bool = True


@router.patch("/{club_id}/membership/privacy")
def patch_membership_privacy(
    club_id: str,
    data: PrivacyIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    m = _require_member(club, me.id)
    m.hide_on_profile = bool(data.hideOnProfile)
    db.commit()
    return {"hideOnProfile": bool(m.hide_on_profile)}


# ── Project showcases ────────────────────────────────────────────────────────

class ShowcaseIn(BaseModel):
    projectId: Optional[UUID] = None
    title: str = Field(min_length=1, max_length=200)
    tagline: str = Field(default="", max_length=280)
    description: str = Field(default="", max_length=20000)
    howWeBuiltIt: Optional[str] = None
    challengesAccomplishments: Optional[str] = None
    kind: str = Field(default="software", max_length=40)
    subCategory: Optional[str] = Field(default=None, max_length=120)
    coverImageUrl: Optional[str] = None
    techStacks: list[str] = Field(default_factory=list)
    githubUrl: Optional[str] = None
    liveUrl: Optional[str] = None
    hardwareCadUrl: Optional[str] = None
    embeddedSpecs: Optional[dict] = None


def _showcase_out(row: ClubShowcase, owner: Optional[User] = None) -> dict:
    profile = getattr(owner, "profile", None) if owner else None
    return {
        "id": str(row.id),
        "groupId": str(row.club_id),
        "projectId": str(row.project_id) if row.project_id else None,
        "title": row.title,
        "tagline": row.tagline or "",
        "description": row.description or "",
        "howWeBuiltIt": row.how_we_built,
        "challengesAccomplishments": row.challenges,
        "kind": row.kind,
        "subCategory": row.sub_category,
        "coverImageUrl": row.cover_url,
        "techStacks": list(row.tech_stacks or []),
        "ownerId": str(row.owner_user_id) if row.owner_user_id else "",
        "ownerName": display_name(owner) if owner else "Member",
        "ownerUsername": owner.username if owner else "unknown",
        "ownerAvatar": profile.avatar_url if profile else None,
        "githubUrl": row.github_url,
        "liveUrl": row.live_url,
        "hardwareCadUrl": row.hardware_cad_url,
        "embeddedSpecs": row.embedded_specs,
        "likesCount": 0,
        "isLiked": False,
        "commentsCount": 0,
        "comments": [],
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/{club_id}/projects/cover", dependencies=[rate_limit("club-showcase-cover", 20, 60)])
def upload_showcase_cover(
    club_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_member(club, me.id)
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in BANNER_EXTS or not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Use a PNG, JPG, GIF, or WebP image")
    file.file.seek(0, 2)
    too_big = file.file.tell() > 8 * 1024 * 1024
    file.file.seek(0)
    if too_big:
        raise HTTPException(status_code=413, detail="Cover is larger than 8 MB")
    name = f"{club.id}_{uuid.uuid4().hex}.{ext}"
    storage.save(f"club-showcase-covers/{name}", file.file)
    return {"url": f"/storage/club-showcase-covers/{name}"}


@router.get("/{club_id}/projects")
def list_showcases(
    club_id: str,
    kind: Optional[str] = None,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    club = _load_club(db, club_id)
    q = db.query(ClubShowcase).filter(ClubShowcase.club_id == club.id)
    if kind and kind != "all":
        q = q.filter(ClubShowcase.kind == kind)
    rows = q.order_by(ClubShowcase.created_at.desc()).limit(100).all()
    owners = {
        u.id: u
        for u in db.query(User)
        .options(selectinload(User.profile))
        .filter(User.id.in_([r.owner_user_id for r in rows if r.owner_user_id]))
        .all()
    } if rows else {}
    return [_showcase_out(r, owners.get(r.owner_user_id) if r.owner_user_id else None) for r in rows]


@router.post("/{club_id}/projects", dependencies=[rate_limit("club-showcase", 20, 60)])
def create_showcase(
    club_id: str,
    data: ShowcaseIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    club = _load_club(db, club_id)
    _require_member(club, me.id)
    kind = (data.kind or "software").strip()
    if kind not in ("software", "embedded_hardware"):
        kind = "software"
    row = ClubShowcase(
        id=uuid.uuid4(),
        club_id=club.id,
        project_id=data.projectId,
        title=data.title.strip(),
        tagline=(data.tagline or "").strip(),
        description=(data.description or "").strip(),
        how_we_built=(data.howWeBuiltIt or None),
        challenges=(data.challengesAccomplishments or None),
        kind=kind,
        sub_category=(data.subCategory or None),
        cover_url=(data.coverImageUrl or None),
        tech_stacks=[t.strip() for t in (data.techStacks or []) if t.strip()][:24],
        github_url=(data.githubUrl or None),
        live_url=(data.liveUrl or None),
        hardware_cad_url=(data.hardwareCadUrl or None),
        embedded_specs=data.embeddedSpecs,
        owner_user_id=me.id,
    )
    db.add(row)
    db.commit()
    owner = db.query(User).options(selectinload(User.profile)).filter(User.id == me.id).first()
    return _showcase_out(row, owner)
