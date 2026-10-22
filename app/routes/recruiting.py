"""Recruiter workspace: candidate evaluation, private shortlists, and contact requests.

Everything a recruiter sees about a candidate goes through the same privacy rules as the rest of the app.
Shortlists and notes are visible to their owner only; other people get 404, not 403, so ids reveal nothing.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, require_recruiter
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.message import Message
from app.models.monthly_wrap import MonthlyWrap
from app.models.network import NetworkActivity
from app.models.recruiting import RecruitmentRequest, Shortlist, ShortlistEntry
from app.models.user import User
from app.realtime.broadcaster import broadcaster
from app.schemas.recruiting import (
    CandidateView,
    ContactState,
    DashboardResponse,
    EntryCreate,
    EntryResponse,
    EntryUpdate,
    Membership,
    ProjectEvidence,
    PublishedWrap,
    RequestCreate,
    RequestResponse,
    ShortlistCreate,
    ShortlistDetail,
    ShortlistResponse,
    ShortlistUpdate,
)
from app.services.social import audience_open, display_name, may_message, notify, profile_open_to, project_stats, user_summary
from app.services.talent import joined_for, joined_project, projects_for, source_project_ids, standing_for

router = APIRouter(prefix="/recruiting", tags=["Recruiting"])

DAILY_REQUESTS = 20
DECLINE_COOLDOWN = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: str | None) -> str | None:
    value = " ".join((value or "").split()) if value is not None else None
    return value or None


def _candidate(db: Session, candidate_id: UUID) -> User:
    user = db.query(User).options(selectinload(User.profile)).filter(User.id == candidate_id).first()
    if not user or user.is_recruiter:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return user


# ---------------- candidate evaluation ----------------
def _published_wraps(db: Session, user: User, viewer_id: UUID) -> list[dict]:
    """Only months the person chose to post, and only if their activity is open to this viewer."""
    if not audience_open(db, user, viewer_id, "activity_visibility", "public"):
        return []
    rows = (
        db.query(MonthlyWrap, NetworkActivity)
        .join(NetworkActivity, NetworkActivity.id == MonthlyWrap.shared_activity_id)
        .filter(MonthlyWrap.user_id == user.id)
        .order_by(MonthlyWrap.month.desc())
        .limit(12)
        .all()
    )
    return [{
        "month": wrap.month.strftime("%Y-%m"),
        "title": post.title,
        "summary": post.content,
        "persona": (wrap.persona or {}).get("label"),
        "published_at": post.created_at,
    } for wrap, post in rows]


def contact_state(db: Session, recruiter: User, candidate: User) -> dict:
    pair = db.query(RecruitmentRequest).filter_by(recruiter_id=recruiter.id, candidate_id=candidate.id)
    if pair.filter_by(status="accepted").first():
        return {"allowed": False, "accepted": True, "reason": "They accepted your request. Continue in Messages."}
    if pending := pair.filter_by(status="pending").first():
        return {"allowed": False, "pending_request_id": pending.id, "reason": "Your request is waiting for an answer"}
    declined = pair.filter_by(status="declined").order_by(RecruitmentRequest.responded_at.desc()).first()
    if declined and declined.responded_at and declined.responded_at > _now() - DECLINE_COOLDOWN:
        again = (declined.responded_at + DECLINE_COOLDOWN).strftime("%b %d")
        return {"allowed": False, "reason": f"They declined recently. You can ask again after {again}."}
    if not profile_open_to(db, candidate.id, recruiter.id):
        return {"allowed": False, "reason": "This profile is only open to their connections"}
    open_to_work = bool(candidate.profile and candidate.profile.open_to_work)
    if not (open_to_work or may_message(db, recruiter.id, candidate)):
        return {"allowed": False, "reason": "They only take messages from connections and are not marked open to work"}
    return {"allowed": True}


@router.get("/candidates/{candidate_id}", response_model=CandidateView)
def candidate_view(candidate_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    from app.routes.profiles import _build_profile_response

    user = _candidate(db, candidate_id)
    projects = projects_for(db, user.id, me.id)
    with_source = source_project_ids(db, projects)
    stats = project_stats(db, [p.id for p in projects], me.id)
    evidence = [ProjectEvidence(
        id=p.id,
        title=p.title,
        short_description=p.short_description,
        cover_image_url=p.cover_image_url,
        tech_stacks=[t.name for t in p.tech_stacks],
        languages=[{"name": r["name"], "percentage": float(r.get("percentage") or 0)}
                   for r in (p.language_stats or []) if isinstance(r, dict) and r.get("name")][:6],
        category=str(p.category.value if hasattr(p.category, "value") else p.category),
        complexity=p.complexity,
        github_url=p.github_url,
        has_source=p.id in with_source,
        commits=p.github_commit_count,
        likes_count=stats[p.id]["likes_count"],
        updated_at=p.updated_at,
    ) for p in projects]
    evidence.sort(key=lambda e: (e.has_source, e.commits or 0, e.likes_count), reverse=True)
    joined = joined_for(db, user.id, me.id)
    joined_stats = project_stats(db, [p.id for p, _ in joined], me.id)
    memberships = (
        db.query(ShortlistEntry, Shortlist)
        .join(Shortlist, Shortlist.id == ShortlistEntry.shortlist_id)
        .filter(Shortlist.owner_id == me.id, ShortlistEntry.candidate_id == user.id)
        .order_by(Shortlist.name)
        .all()
    )
    return CandidateView(
        profile=_build_profile_response(user, me.id, db),
        standing=standing_for(db, user, me.id),
        projects=evidence,
        collaborations=[joined_project(p, m, joined_stats[p.id]) for p, m in joined],
        wraps=[PublishedWrap(**w) for w in _published_wraps(db, user, me.id)],
        shortlists=[Membership(shortlist_id=s.id, shortlist_name=s.name, entry_id=e.id, status=e.status, note=e.note)
                    for e, s in memberships],
        contact=ContactState(**contact_state(db, me, user)),
    )


# ---------------- shortlists ----------------
def _entry_view(entry: ShortlistEntry) -> EntryResponse:
    return EntryResponse(id=entry.id, shortlist_id=entry.shortlist_id, candidate=user_summary(entry.candidate),
                         status=entry.status, note=entry.note, created_at=entry.created_at, updated_at=entry.updated_at)


def _list_view(shortlist: Shortlist) -> dict:
    statuses = Counter(e.status for e in shortlist.entries)
    return dict(id=shortlist.id, name=shortlist.name, description=shortlist.description, created_at=shortlist.created_at,
                updated_at=shortlist.updated_at, count=len(shortlist.entries), statuses=dict(statuses))


def _own_shortlist(db: Session, shortlist_id: UUID, me: User) -> Shortlist:
    shortlist = db.query(Shortlist).filter_by(id=shortlist_id, owner_id=me.id).first()
    if not shortlist:
        raise HTTPException(status_code=404, detail="Shortlist not found")
    return shortlist


def _own_entry(db: Session, shortlist_id: UUID, entry_id: UUID, me: User) -> ShortlistEntry:
    shortlist = _own_shortlist(db, shortlist_id, me)
    entry = db.query(ShortlistEntry).filter_by(id=entry_id, shortlist_id=shortlist.id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Candidate is not on this shortlist")
    return entry


@router.get("/shortlists", response_model=List[ShortlistResponse])
def my_shortlists(db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    rows = (db.query(Shortlist).options(selectinload(Shortlist.entries))
            .filter_by(owner_id=me.id).order_by(Shortlist.updated_at.desc()).all())
    return [_list_view(s) for s in rows]


@router.post("/shortlists", response_model=ShortlistResponse, status_code=status.HTTP_201_CREATED)
def create_shortlist(data: ShortlistCreate, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    name = _clean(data.name)
    if not name:
        raise HTTPException(status_code=400, detail="Name the shortlist")
    if db.query(Shortlist.id).filter(Shortlist.owner_id == me.id).count() >= 50:
        raise HTTPException(status_code=400, detail="You can keep up to 50 shortlists")
    shortlist = Shortlist(owner_id=me.id, name=name, description=_clean(data.description))
    db.add(shortlist)
    db.commit()
    db.refresh(shortlist)
    return _list_view(shortlist)


@router.get("/shortlists/{shortlist_id}", response_model=ShortlistDetail)
def shortlist_detail(shortlist_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    shortlist = _own_shortlist(db, shortlist_id, me)
    return ShortlistDetail(**_list_view(shortlist), entries=[_entry_view(e) for e in shortlist.entries])


@router.patch("/shortlists/{shortlist_id}", response_model=ShortlistResponse)
def update_shortlist(shortlist_id: UUID, data: ShortlistUpdate, db: Session = Depends(get_db),
                     me: User = Depends(require_recruiter)):
    shortlist = _own_shortlist(db, shortlist_id, me)
    if "name" in data.model_fields_set:
        shortlist.name = _clean(data.name) or shortlist.name
    if "description" in data.model_fields_set:
        shortlist.description = _clean(data.description)
    db.commit()
    db.refresh(shortlist)
    return _list_view(shortlist)


@router.delete("/shortlists/{shortlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shortlist(shortlist_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    db.delete(_own_shortlist(db, shortlist_id, me))
    db.commit()


@router.post("/shortlists/{shortlist_id}/entries", response_model=EntryResponse, status_code=status.HTTP_201_CREATED)
def add_to_shortlist(shortlist_id: UUID, data: EntryCreate, db: Session = Depends(get_db),
                     me: User = Depends(require_recruiter)):
    shortlist = _own_shortlist(db, shortlist_id, me)
    candidate = _candidate(db, data.candidate_id)
    if len(shortlist.entries) >= 500:
        raise HTTPException(status_code=400, detail="A shortlist holds up to 500 candidates")
    entry = ShortlistEntry(shortlist_id=shortlist.id, candidate_id=candidate.id, status=data.status,
                           note=(data.note or "").strip() or None)
    db.add(entry)
    shortlist.updated_at = _now()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This candidate is already on that shortlist")
    db.refresh(entry)
    return _entry_view(entry)


@router.patch("/shortlists/{shortlist_id}/entries/{entry_id}", response_model=EntryResponse)
def update_entry(shortlist_id: UUID, entry_id: UUID, data: EntryUpdate, db: Session = Depends(get_db),
                 me: User = Depends(require_recruiter)):
    entry = _own_entry(db, shortlist_id, entry_id, me)
    if data.status is not None:
        entry.status = data.status
    if "note" in data.model_fields_set:
        entry.note = (data.note or "").strip() or None
    entry.shortlist.updated_at = _now()
    db.commit()
    db.refresh(entry)
    return _entry_view(entry)


@router.delete("/shortlists/{shortlist_id}/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_entry(shortlist_id: UUID, entry_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    db.delete(_own_entry(db, shortlist_id, entry_id, me))
    db.commit()


# ---------------- contact requests ----------------
def _request_view(r: RecruitmentRequest) -> RequestResponse:
    return RequestResponse(
        id=r.id, recruiter=user_summary(r.recruiter), recruiter_company=r.recruiter.profile.company if r.recruiter.profile else None,
        candidate=user_summary(r.candidate), role_title=r.role_title, work_type=r.work_type, message=r.message,
        status=r.status, created_at=r.created_at, responded_at=r.responded_at,
    )


@router.post("/requests", response_model=RequestResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[rate_limit("recruitment_request", 5, 600)])
def send_request(data: RequestCreate, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    candidate = _candidate(db, data.candidate_id)
    since = _now() - timedelta(days=1)
    sent_today = db.query(RecruitmentRequest.id).filter(
        RecruitmentRequest.recruiter_id == me.id, RecruitmentRequest.created_at >= since).count()
    if sent_today >= DAILY_REQUESTS:
        raise HTTPException(status_code=429, detail=f"You can send {DAILY_REQUESTS} contact requests a day")
    state = contact_state(db, me, candidate)
    if not state["allowed"]:
        raise HTTPException(status_code=409 if state.get("pending_request_id") or state.get("accepted") else 403,
                            detail=state["reason"])
    req = RecruitmentRequest(recruiter_id=me.id, candidate_id=candidate.id, role_title=_clean(data.role_title),
                             work_type=data.work_type, message=data.message.strip(), status="pending")
    db.add(req)
    db.flush()
    company = me.profile.company if me.profile and me.profile.company else "a company"
    notify(db, candidate.id, me, "recruitment_request", str(req.id),
           f"{display_name(me)} ({company}) would like to talk about a {req.role_title} role.")
    db.commit()
    db.refresh(req)
    return _request_view(req)


@router.get("/requests/sent", response_model=List[RequestResponse])
def sent_requests(db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    rows = db.query(RecruitmentRequest).filter_by(recruiter_id=me.id).order_by(RecruitmentRequest.created_at.desc()).limit(200).all()
    return [_request_view(r) for r in rows]


@router.get("/requests/incoming", response_model=List[RequestResponse])
def incoming_requests(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    rows = (db.query(RecruitmentRequest).filter(RecruitmentRequest.candidate_id == me.id,
                                                RecruitmentRequest.status != "withdrawn")
            .order_by(RecruitmentRequest.created_at.desc()).limit(100).all())
    return [_request_view(r) for r in rows]


def _candidates_pending(db: Session, request_id: UUID, me: User) -> RecruitmentRequest:
    req = db.query(RecruitmentRequest).filter_by(id=request_id, candidate_id=me.id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="This request was already answered")
    return req


@router.put("/requests/{request_id}/accept", response_model=RequestResponse)
def accept_request(request_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    from app.routes.messages import _message_response

    req = _candidates_pending(db, request_id, me)
    req.status, req.responded_at = "accepted", _now()
    opener = Message(sender_id=req.recruiter_id, receiver_id=me.id, read=True,
                     content=f"Recruitment request: {req.role_title}\n\n{req.message}")
    db.add(opener)
    notify(db, req.recruiter_id, me, "recruitment_accepted", str(me.id),
           f"{display_name(me)} accepted your request about the {req.role_title} role. You can message them now.")
    db.commit()
    db.refresh(opener)
    broadcaster.publish(req.recruiter_id, {
        "type": "message.new", "message": _message_response(opener, req.recruiter_id).model_dump(mode="json"),
    })
    db.refresh(req)
    return _request_view(req)


@router.put("/requests/{request_id}/decline", response_model=RequestResponse)
def decline_request(request_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    req = _candidates_pending(db, request_id, me)
    req.status, req.responded_at = "declined", _now()
    notify(db, req.recruiter_id, me, "recruitment_declined", str(req.id),
           f"{display_name(me)} declined your request about the {req.role_title} role.")
    db.commit()
    db.refresh(req)
    return _request_view(req)


@router.delete("/requests/{request_id}", response_model=RequestResponse)
def withdraw_request(request_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    req = db.query(RecruitmentRequest).filter_by(id=request_id, recruiter_id=me.id, status="pending").first()
    if not req:
        raise HTTPException(status_code=404, detail="No pending request to withdraw")
    req.status, req.responded_at = "withdrawn", _now()
    db.commit()
    db.refresh(req)
    return _request_view(req)


# ---------------- dashboard ----------------
@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    entries = (db.query(ShortlistEntry).join(Shortlist, Shortlist.id == ShortlistEntry.shortlist_id)
               .filter(Shortlist.owner_id == me.id).order_by(ShortlistEntry.updated_at.desc()).all())
    requests = dict(db.query(RecruitmentRequest.status, func.count()).filter(
        RecruitmentRequest.recruiter_id == me.id).group_by(RecruitmentRequest.status).all())
    recent_requests = (db.query(RecruitmentRequest).filter_by(recruiter_id=me.id)
                       .order_by(RecruitmentRequest.created_at.desc()).limit(5).all())
    return DashboardResponse(
        shortlists=db.query(Shortlist.id).filter(Shortlist.owner_id == me.id).count(),
        candidates=len({e.candidate_id for e in entries}),
        statuses=dict(Counter(e.status for e in entries)),
        requests=requests,
        recent_entries=[_entry_view(e) for e in entries[:6]],
        recent_requests=[_request_view(r) for r in recent_requests],
    )
