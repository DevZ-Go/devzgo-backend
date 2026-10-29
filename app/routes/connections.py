"""Connections: request, then the recipient explicitly accepts or declines. Withdraw/remove delete the row."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, not_, or_
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user
from app.core.errors import Conflict
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.connection import Connection
from app.models.profile import UserProfile
from app.models.user import User
from app.realtime.broadcaster import queue_event
from app.schemas.network import (
    ConnectionPage,
    ConnectionRequestCreate,
    ConnectionResponse,
    ConnectionStatusResponse,
    ConnectionUserSummary,
)
from app.services.pagination import paginate
from app.services.safety import blocked_between
from app.services.social import accepts_connection_requests, display_name, notify, user_summary

router = APIRouter(prefix="/connections", tags=["Connections"])

DECLINE_COOLDOWN = timedelta(days=7)
_EAGER = (selectinload(Connection.requester).selectinload(User.profile),
          selectinload(Connection.receiver).selectinload(User.profile))


def _mine(me: UUID):
    return or_(Connection.requester_id == me, Connection.receiver_id == me)


def _pair(db: Session, a: UUID, b: UUID) -> Optional[Connection]:
    return db.query(Connection).filter(or_(
        and_(Connection.requester_id == a, Connection.receiver_id == b),
        and_(Connection.requester_id == b, Connection.receiver_id == a),
    )).first()


def _cooling_down(c: Connection) -> bool:
    return c.status == "declined" and c.updated_at and datetime.now(timezone.utc) - c.updated_at < DECLINE_COOLDOWN


def _view(c: Connection, me: UUID, mutual: int = 0) -> ConnectionResponse:
    """The requester never learns about a decline: they keep seeing 'pending' during the cooldown."""
    status = "pending" if c.status == "declined" and c.requester_id == me else c.status
    partner = c.receiver if c.requester_id == me else c.requester
    return ConnectionResponse(id=c.id, requester_id=c.requester_id, receiver_id=c.receiver_id, status=status,
                              created_at=c.created_at, updated_at=c.updated_at, accepted_at=c.accepted_at,
                              partner=user_summary(partner), mutual_count=mutual)


def partner_ids(db: Session, me: UUID) -> set:
    rows = db.query(Connection.requester_id, Connection.receiver_id).filter(_mine(me), Connection.status == "accepted").all()
    return {b if a == me else a for a, b in rows}


@router.get("", response_model=ConnectionPage)
def my_connections(search: Optional[str] = Query(None, max_length=100), page: Optional[str] = None,
                   limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db),
                   me: User = Depends(get_current_user)):
    """My Connections: accepted only, searchable by username or name, with mutual connection counts."""
    query = db.query(Connection).options(*_EAGER).filter(_mine(me.id), Connection.status == "accepted")
    if search and search.strip():
        term = f"%{search.strip()}%"
        matching = db.query(User.id).outerjoin(UserProfile).filter(
            or_(User.username.ilike(term), UserProfile.full_name.ilike(term))).scalar_subquery()
        query = query.filter(or_(and_(Connection.requester_id == me.id, Connection.receiver_id.in_(matching)),
                                 and_(Connection.receiver_id == me.id, Connection.requester_id.in_(matching))))
    rows, next_page = paginate(query, Connection, page, limit)
    mine = partner_ids(db, me.id)
    partners = [c.receiver_id if c.requester_id == me.id else c.requester_id for c in rows]
    mutual = Counter()
    for a, b in db.query(Connection.requester_id, Connection.receiver_id).filter(
            Connection.status == "accepted",
            or_(Connection.requester_id.in_(partners), Connection.receiver_id.in_(partners))).all():
        for p, other in ((a, b), (b, a)):
            if p in partners and other in mine:
                mutual[p] += 1
    return {"items": [_view(c, me.id, mutual[p]) for c, p in zip(rows, partners)], "next_page": next_page}


@router.get("/requests", response_model=ConnectionPage)
def connection_requests(direction: str = Query("incoming", pattern="^(incoming|outgoing)$"), page: Optional[str] = None,
                        limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db),
                        me: User = Depends(get_current_user)):
    if direction == "incoming":
        query = db.query(Connection).filter(Connection.receiver_id == me.id, Connection.status == "pending")
    else:
        cutoff = datetime.now(timezone.utc) - DECLINE_COOLDOWN
        query = db.query(Connection).filter(Connection.requester_id == me.id, or_(
            Connection.status == "pending", and_(Connection.status == "declined", Connection.updated_at > cutoff)))
    rows, next_page = paginate(query.options(*_EAGER), Connection, page, limit)
    return {"items": [_view(c, me.id) for c in rows], "next_page": next_page}


@router.get("/pending", response_model=List[ConnectionResponse])
def get_pending_incoming_connections(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    """Kept for older clients; same as /requests?direction=incoming without paging."""
    rows = db.query(Connection).options(*_EAGER).filter(Connection.receiver_id == me.id, Connection.status == "pending").all()
    return [_view(c, me.id) for c in rows]


def connection_state(db: Session, me: UUID, other: UUID) -> tuple[Optional[str], Optional[UUID]]:
    """(self | accepted | pending_sent | pending_received | None, connection id) as `me` should see it."""
    if me == other:
        return "self", None
    c = _pair(db, me, other)
    if not c or c.status == "removed" or (c.status == "declined" and (c.receiver_id == me or not _cooling_down(c))):
        return None, None
    if c.status == "accepted":
        return "accepted", c.id
    return ("pending_sent" if c.requester_id == me else "pending_received"), c.id


@router.get("/status/{user_id}", response_model=ConnectionStatusResponse)
def get_connection_status(user_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    status, connection_id = connection_state(db, me.id, user_id)
    return ConnectionStatusResponse(user_id=user_id, status=status, connection_id=connection_id)


@router.post("/request", response_model=ConnectionResponse, dependencies=[rate_limit("connect", 20, 60)])
def send_connection_request(data: ConnectionRequestCreate, db: Session = Depends(get_db),
                            me: User = Depends(get_current_user)):
    if data.target_user_id == me.id:
        raise HTTPException(status_code=400, detail="Cannot connect to yourself")
    target = db.query(User).filter(User.id == data.target_user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target developer not found")
    if blocked_between(db, me.id, target.id):
        raise HTTPException(status_code=403, detail="You can't connect with this person")
    if not accepts_connection_requests(target):
        raise HTTPException(status_code=403, detail="This person is not accepting connection requests")

    c = _pair(db, me.id, target.id)
    if c and c.status == "accepted":
        raise HTTPException(status_code=409, detail="Already connected")
    if c and c.status == "pending" and c.receiver_id == me.id:
        raise Conflict("incoming_request_exists", "This developer already sent you a request. Accept or decline it.",
                       connection_id=c.id)
    if c and (c.status == "pending" or (_cooling_down(c) and c.requester_id == me.id)):
        return _view(c, me.id)  # already asked (or quietly declined): nothing changes, nobody is notified again

    if c:  # declined long enough ago, declined by me earlier, or a legacy removed row
        c.requester_id, c.receiver_id, c.status, c.accepted_at = me.id, target.id, "pending", None
    else:
        c = Connection(requester_id=me.id, receiver_id=target.id, status="pending")
        db.add(c)
    notify(db, target.id, me, "connection_request", str(me.id), f"{display_name(me)} sent you a connection request.")
    db.commit()
    db.refresh(c)
    return _view(c, me.id)


def _incoming_pending(db: Session, connection_id: UUID, me: User) -> Connection:
    c = db.query(Connection).filter(Connection.id == connection_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Connection request not found")
    if c.receiver_id != me.id:
        raise HTTPException(status_code=403, detail="You can only answer requests sent to you")
    if c.status != "pending":
        raise HTTPException(status_code=400, detail="This request is no longer pending")
    return c


@router.put("/{connection_id}/accept", response_model=ConnectionResponse)
def accept_connection(connection_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    c = _incoming_pending(db, connection_id, me)
    c.status, c.accepted_at = "accepted", datetime.now(timezone.utc)
    notify(db, c.requester_id, me, "connection_accepted", str(me.id), f"{display_name(me)} accepted your connection request.")
    db.commit()
    db.refresh(c)
    return _view(c, me.id)


@router.put("/{connection_id}/decline")
def decline_connection(connection_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    c = _incoming_pending(db, connection_id, me)
    c.status = "declined"  # the requester is not told
    db.commit()
    return {"message": "Connection request declined"}


@router.delete("/{connection_id}")
def remove_or_withdraw_connection(connection_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    """Requester withdraws a pending request, or either side removes an accepted connection."""
    c = db.query(Connection).filter(Connection.id == connection_id).first()
    if not c or me.id not in (c.requester_id, c.receiver_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    if c.status == "pending" and c.requester_id != me.id:
        raise HTTPException(status_code=400, detail="Decline the request instead")
    if c.status == "declined":
        return {"message": "Connection removed"}  # the row stays so withdrawing cannot skip the decline cooldown
    other = c.receiver_id if c.requester_id == me.id else c.requester_id
    queue_event(db, other, {"type": "connection.removed", "by": str(me.id)})
    db.delete(c)
    db.commit()
    return {"message": "Connection removed"}


@router.get("/suggested", response_model=List[ConnectionUserSummary])
def get_suggested_developers(limit: int = Query(5, ge=1, le=20), db: Session = Depends(get_db),
                             me: User = Depends(get_current_user)):
    """People I have no connection row with (so declined users stay out), most mutual connections first."""
    rows = db.query(Connection.requester_id, Connection.receiver_id).filter(_mine(me.id)).all()
    hidden = {uid for (uid,) in db.query(UserProfile.user_id).filter(UserProfile.discoverable.is_(False)).all()}
    excluded = {b if a == me.id else a for a, b in rows} | {me.id} | hidden
    partners = partner_ids(db, me.id)
    mutual = Counter()
    for a, b in db.query(Connection.requester_id, Connection.receiver_id).filter(
            Connection.status == "accepted", or_(Connection.requester_id.in_(partners), Connection.receiver_id.in_(partners))).all():
        for p, other in ((a, b), (b, a)):
            if p in partners and other not in excluded:
                mutual[other] += 1
    ranked = [uid for uid, _ in mutual.most_common(limit)]
    users = {u.id: u for u in db.query(User).options(selectinload(User.profile)).filter(User.id.in_(ranked)).all()}
    result = [users[uid] for uid in ranked if uid in users]
    if len(result) < limit:
        result += db.query(User).options(selectinload(User.profile)).filter(
            not_(User.id.in_(excluded | set(ranked)))).order_by(User.created_at.desc()).limit(limit - len(result)).all()
    return [user_summary(u) for u in result]
