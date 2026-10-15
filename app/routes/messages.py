from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, and_, case, desc, func
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.message import Message
from app.core.dependencies import get_current_user
from app.services.social import display_name, notify, user_summary
from app.schemas.network import (
    MessageCreate,
    MessageResponse,
    ConversationResponse,
)

router = APIRouter(prefix="/messages", tags=["Messages"])


def _message_response(m: Message, viewer_id: UUID) -> MessageResponse:
    return MessageResponse(
        id=m.id,
        sender_id=m.sender_id,
        receiver_id=m.receiver_id,
        content=m.content,
        read=m.read,
        created_at=m.created_at,
        is_outgoing=(m.sender_id == viewer_id),
    )


@router.get("/conversations", response_model=List[ConversationResponse])
def get_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    me = current_user.id
    partner = case((Message.sender_id == me, Message.receiver_id), else_=Message.sender_id)
    # Latest message per partner in one query (Postgres DISTINCT ON).
    latest = (
        db.query(Message)
        .filter(or_(Message.sender_id == me, Message.receiver_id == me))
        .distinct(partner)
        .order_by(partner, desc(Message.created_at))
        .all()
    )
    partner_ids = [m.receiver_id if m.sender_id == me else m.sender_id for m in latest]
    users = {u.id: u for u in db.query(User).options(selectinload(User.profile)).filter(User.id.in_(partner_ids))} if partner_ids else {}
    unread = dict(
        db.query(Message.sender_id, func.count())
        .filter(Message.receiver_id == me, Message.read.is_(False))
        .group_by(Message.sender_id).all()
    )

    conversations = [
        ConversationResponse(
            other_user=user_summary(users[pid]),
            last_message=_message_response(m, me),
            unread_count=unread.get(pid, 0),
        )
        for m, pid in zip(latest, partner_ids) if pid in users
    ]
    conversations.sort(key=lambda c: c.last_message.created_at, reverse=True)
    return conversations


@router.get("/conversations/{other_user_id}", response_model=List[MessageResponse])
def get_message_thread(
    other_user_id: UUID,
    before: Optional[datetime] = Query(None, description="Return messages older than this (pagination)"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    me = current_user.id
    query = db.query(Message).filter(
        or_(
            and_(Message.sender_id == me, Message.receiver_id == other_user_id),
            and_(Message.sender_id == other_user_id, Message.receiver_id == me),
        )
    )
    if before:
        query = query.filter(Message.created_at < before)
    messages = query.order_by(desc(Message.created_at)).limit(limit).all()[::-1]

    if not before and db.query(Message).filter(
        Message.sender_id == other_user_id, Message.receiver_id == me, Message.read.is_(False)
    ).update({"read": True}):
        db.commit()
        for m in messages:
            if m.receiver_id == me:
                m.read = True

    return [_message_response(m, me) for m in messages]


@router.post("", response_model=MessageResponse)
def send_message(
    msg_data: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if msg_data.receiver_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot send message to yourself")

    if not db.query(User.id).filter(User.id == msg_data.receiver_id).first():
        raise HTTPException(status_code=404, detail="Recipient developer not found")

    content = msg_data.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    new_msg = Message(sender_id=current_user.id, receiver_id=msg_data.receiver_id, content=content, read=False)
    db.add(new_msg)
    snippet = content if len(content) <= 40 else content[:40] + "..."
    notify(db, msg_data.receiver_id, current_user, "new_message", str(current_user.id),
           f"New message from {display_name(current_user)}: \"{snippet}\"")
    db.commit()
    db.refresh(new_msg)
    return _message_response(new_msg, current_user.id)


@router.put("/read/{other_user_id}")
def mark_thread_as_read(
    other_user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.query(Message).filter(
        Message.sender_id == other_user_id,
        Message.receiver_id == current_user.id,
        Message.read.is_(False),
    ).update({"read": True})
    db.commit()
    return {"message": "Messages marked as read"}


@router.get("/unread-count")
def get_unread_messages_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = db.query(Message).filter(
        Message.receiver_id == current_user.id,
        Message.read.is_(False),
    ).count()
    return {"unread_count": count}
