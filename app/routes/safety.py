"""Each person's own block, restrict and mute lists."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.chat import ChatRoom
from app.models.connection import Connection
from app.models.safety import ChatMute, UserBlock
from app.models.user import User
from app.realtime.broadcaster import queue_event
from app.schemas.network import MutedChat, PersonSafety, SafetyChoice, SafetyOverview, SafetyPerson
from app.services.chat_rooms import room_for_member
from app.services.safety import is_muted, relation
from app.services.social import user_summary

router = APIRouter(prefix="/safety", tags=["Safety"])


class SafetyByUsername(SafetyChoice):
    username: str = Field(min_length=1, max_length=80)


class _Ok(BaseModel):
    ok: bool = True


def _person(db: Session, me: User, other_id: UUID) -> PersonSafety:
    return PersonSafety(
        relation=relation(db, me.id, other_id),
        blocked_me=relation(db, other_id, me.id) == "block",
        muted=is_muted(db, me.id, person=other_id),
    )


def _target(db: Session, me: User, user_id: UUID) -> User:
    if user_id == me.id:
        raise HTTPException(status_code=400, detail="You can't do that to yourself")
    other = db.query(User).filter(User.id == user_id).first()
    if other is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return other


def _apply(db: Session, me: User, other: User, kind: str) -> PersonSafety:
    row = db.query(UserBlock).filter(UserBlock.blocker_id == me.id, UserBlock.blocked_id == other.id).first()
    if row is None:
        db.add(UserBlock(blocker_id=me.id, blocked_id=other.id, kind=kind))
    else:
        row.kind = kind
    if kind == "block":
        db.query(Connection).filter(
            or_(
                and_(Connection.requester_id == me.id, Connection.receiver_id == other.id),
                and_(Connection.requester_id == other.id, Connection.receiver_id == me.id),
            )
        ).delete(synchronize_session=False)
        queue_event(db, other.id, {"type": "connection.removed", "by": str(me.id)})
    db.commit()
    return _person(db, me, other.id)


@router.get("", response_model=SafetyOverview)
def my_safety(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    rows = (
        db.query(UserBlock, User)
        .join(User, User.id == UserBlock.blocked_id)
        .options(selectinload(User.profile))
        .filter(UserBlock.blocker_id == me.id)
        .order_by(UserBlock.created_at.desc())
        .all()
    )
    overview = SafetyOverview()
    for block, user in rows:
        item = SafetyPerson(user=user_summary(user), kind=block.kind, since=block.created_at)
        (overview.blocked if block.kind == "block" else overview.restricted).append(item)
    mutes = db.query(ChatMute).filter(ChatMute.user_id == me.id).order_by(ChatMute.created_at.desc()).all()
    people = {
        u.id: u for u in db.query(User).options(selectinload(User.profile))
        .filter(User.id.in_([m.muted_user_id for m in mutes if m.muted_user_id]))
    } if mutes else {}
    rooms = {
        r.id: r.title for r in db.query(ChatRoom).filter(ChatRoom.id.in_([m.room_id for m in mutes if m.room_id]))
    } if mutes else {}
    for mute in mutes:
        if mute.muted_user_id and mute.muted_user_id in people:
            overview.muted.append(MutedChat(user=user_summary(people[mute.muted_user_id]), since=mute.created_at))
        elif mute.room_id and mute.room_id in rooms:
            overview.muted.append(MutedChat(room_id=mute.room_id, room_title=rooms[mute.room_id], since=mute.created_at))
    return overview


@router.get("/people/{user_id}", response_model=PersonSafety)
def person_safety(user_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    return _person(db, me, _target(db, me, user_id).id)


@router.put("/people/{user_id}", response_model=PersonSafety)
def set_person_safety(user_id: UUID, data: SafetyChoice, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    return _apply(db, me, _target(db, me, user_id), data.kind)


@router.post("/people", response_model=PersonSafety)
def set_person_safety_by_username(data: SafetyByUsername, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    username = data.username.strip().lstrip("@")
    other = db.query(User).filter(User.username.ilike(username)).first()
    if other is None:
        raise HTTPException(status_code=404, detail=f"No one goes by @{username}")
    return _apply(db, me, _target(db, me, other.id), data.kind)


@router.delete("/people/{user_id}", response_model=PersonSafety)
def clear_person_safety(user_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    other = _target(db, me, user_id)
    db.query(UserBlock).filter(UserBlock.blocker_id == me.id, UserBlock.blocked_id == other.id).delete()
    db.commit()
    return _person(db, me, other.id)


@router.put("/mutes/people/{user_id}", response_model=PersonSafety)
def mute_person(user_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    other = _target(db, me, user_id)
    if not is_muted(db, me.id, person=other.id):
        db.add(ChatMute(user_id=me.id, muted_user_id=other.id))
        db.commit()
    return _person(db, me, other.id)


@router.delete("/mutes/people/{user_id}", response_model=PersonSafety)
def unmute_person(user_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    other = _target(db, me, user_id)
    db.query(ChatMute).filter(ChatMute.user_id == me.id, ChatMute.muted_user_id == other.id).delete()
    db.commit()
    return _person(db, me, other.id)


@router.put("/mutes/groups/{room_id}", response_model=_Ok)
def mute_group(room_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    room_for_member(db, room_id, me.id)
    if not is_muted(db, me.id, room=room_id):
        db.add(ChatMute(user_id=me.id, room_id=room_id))
        db.commit()
    return _Ok()


@router.delete("/mutes/groups/{room_id}", response_model=_Ok)
def unmute_group(room_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    db.query(ChatMute).filter(ChatMute.user_id == me.id, ChatMute.room_id == room_id).delete()
    db.commit()
    return _Ok()
