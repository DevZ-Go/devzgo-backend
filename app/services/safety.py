"""Block, restrict and mute, as chosen by each person for themselves.

block:    full cut either way — no DMs, no connection requests; any connection ends; hidden from
          each other's search and network lists. Public work is not shown across the block.
restrict: no DMs either way while the restrict stands; connection may stay; their profile and
          projects stay findable in search/Explore; they can still see your public work.
mute:     a direct chat or group stays, but sends no notifications and leaves the unread badge alone.
"""

from __future__ import annotations

from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.safety import ChatMute, UserBlock


def relation(db: Session, owner: UUID, other: UUID) -> Optional[str]:
    """What owner chose about other: block, restrict, or None."""
    row = db.query(UserBlock.kind).filter(UserBlock.blocker_id == owner, UserBlock.blocked_id == other).first()
    return row[0] if row else None


def blocked_between(db: Session, a: UUID, b: UUID) -> bool:
    return db.query(UserBlock.blocker_id).filter(
        UserBlock.kind == "block",
        or_(
            and_(UserBlock.blocker_id == a, UserBlock.blocked_id == b),
            and_(UserBlock.blocker_id == b, UserBlock.blocked_id == a),
        ),
    ).first() is not None


def dm_blocked(db: Session, a: UUID, b: UUID) -> bool:
    """True when either person blocked or restricted the other — no DMs either way."""
    if blocked_between(db, a, b):
        return True
    return db.query(UserBlock.blocker_id).filter(
        UserBlock.kind == "restrict",
        or_(
            and_(UserBlock.blocker_id == a, UserBlock.blocked_id == b),
            and_(UserBlock.blocker_id == b, UserBlock.blocked_id == a),
        ),
    ).first() is not None


def silenced_by(db: Session, owner: UUID, other: UUID) -> bool:
    """owner has blocked or restricted other, so other's activity should not ping owner."""
    return relation(db, owner, other) is not None


def quiet_senders(db: Session, owner: UUID) -> set[UUID]:
    """People whose direct messages should not ring the badge: blocked, restricted, or muted."""
    blocked = {row[0] for row in db.query(UserBlock.blocked_id).filter(UserBlock.blocker_id == owner)}
    muted = {
        row[0] for row in db.query(ChatMute.muted_user_id)
        .filter(ChatMute.user_id == owner, ChatMute.muted_user_id.isnot(None))
    }
    return blocked | muted


def muted_rooms(db: Session, owner: UUID) -> set[UUID]:
    return {
        row[0] for row in db.query(ChatMute.room_id)
        .filter(ChatMute.user_id == owner, ChatMute.room_id.isnot(None))
    }


def is_muted(db: Session, owner: UUID, *, person: Optional[UUID] = None, room: Optional[UUID] = None) -> bool:
    query = db.query(ChatMute.id).filter(ChatMute.user_id == owner)
    if person is not None:
        query = query.filter(ChatMute.muted_user_id == person)
    if room is not None:
        query = query.filter(ChatMute.room_id == room)
    return query.first() is not None


def blocked_any(db: Session, me: UUID, others: Iterable[UUID]) -> set[UUID]:
    """Which of others are blocked by me or have blocked me."""
    ids = list(others)
    if not ids:
        return set()
    rows = db.query(UserBlock.blocker_id, UserBlock.blocked_id).filter(
        UserBlock.kind == "block",
        or_(
            and_(UserBlock.blocker_id == me, UserBlock.blocked_id.in_(ids)),
            and_(UserBlock.blocked_id == me, UserBlock.blocker_id.in_(ids)),
        ),
    ).all()
    return {b if a == me else a for a, b in rows}
