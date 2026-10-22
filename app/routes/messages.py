from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, and_, case, desc, func
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.message import Message
from app.models.chat import ChatMember, ChatRoom, RoomMessage
from app.core.dependencies import get_current_user
from app.services.social import display_name, may_message, notify, user_summary
from app.services.chat_rooms import (
    connected,
    latest_preview,
    post_room_message,
    room_for_member,
    sync_my_project_rooms,
    unread_count,
)
from app.realtime.broadcaster import broadcaster, queue_event
from app.schemas.network import (
    ChatRoomCreate,
    ChatRoomSummary,
    ConversationResponse,
    MessageCreate,
    MessageResponse,
    RoomMessageCreate,
    RoomMessageResponse,
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
        queue_event(db, other_user_id, {"type": "message.read", "by": str(me)})
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

    recipient = db.query(User).options(selectinload(User.profile)).filter(User.id == msg_data.receiver_id).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient developer not found")
    if not may_message(db, current_user.id, recipient):
        raise HTTPException(status_code=403, detail="This person only accepts messages from connections")

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
    broadcaster.publish(msg_data.receiver_id, {
        "type": "message.new",
        "message": _message_response(new_msg, msg_data.receiver_id).model_dump(mode="json"),
    })
    return _message_response(new_msg, current_user.id)


@router.put("/read/{other_user_id}")
def mark_thread_as_read(
    other_user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if db.query(Message).filter(
        Message.sender_id == other_user_id,
        Message.receiver_id == current_user.id,
        Message.read.is_(False),
    ).update({"read": True}):
        queue_event(db, other_user_id, {"type": "message.read", "by": str(current_user.id)})
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


def _room_summary(db: Session, room: ChatRoom, me: UUID) -> ChatRoomSummary:
    mine = next((m for m in room.members if m.user_id == me), None)
    last = latest_preview(db, room.id)
    return ChatRoomSummary(
        id=room.id,
        title=room.title,
        project_id=room.project_id,
        member_count=len(room.members),
        unread_count=unread_count(db, room.id, me, mine.last_read_at if mine else None),
        last_message=last.content if last else None,
        last_at=last.created_at if last else None,
    )


def _room_message(msg: RoomMessage, viewer: UUID) -> RoomMessageResponse:
    sender = msg.sender
    return RoomMessageResponse(
        id=msg.id,
        room_id=msg.room_id,
        sender_id=msg.sender_id,
        sender_username=sender.username if sender else "developer",
        sender_name=display_name(sender) if sender else "Developer",
        content=msg.content,
        created_at=msg.created_at,
        is_outgoing=msg.sender_id == viewer,
    )


@router.get("/groups", response_model=List[ChatRoomSummary])
def list_groups(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sync_my_project_rooms(db, current_user.id)
    db.commit()
    rooms = (
        db.query(ChatRoom)
        .options(selectinload(ChatRoom.members))
        .join(ChatMember, ChatMember.room_id == ChatRoom.id)
        .filter(ChatMember.user_id == current_user.id)
        .all()
    )
    summaries = [_room_summary(db, room, current_user.id) for room in rooms]
    summaries.sort(key=lambda r: r.last_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return summaries


@router.post("/groups", response_model=ChatRoomSummary)
def create_group(data: ChatRoomCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    member_ids = {uid for uid in data.member_ids if uid != current_user.id}
    if not member_ids:
        raise HTTPException(status_code=400, detail="Pick at least one other person")
    for uid in member_ids:
        if not db.query(User.id).filter(User.id == uid).first():
            raise HTTPException(status_code=404, detail="One of those people could not be found")
        if not connected(db, current_user.id, uid):
            raise HTTPException(status_code=400, detail="You can only start a group with people you're connected to")
    room = ChatRoom(title=data.title.strip()[:120], created_by=current_user.id)
    db.add(room)
    db.flush()
    for uid in member_ids | {current_user.id}:
        db.add(ChatMember(room_id=room.id, user_id=uid))
    db.commit()
    db.refresh(room)
    room = db.query(ChatRoom).options(selectinload(ChatRoom.members)).filter(ChatRoom.id == room.id).one()
    for uid in member_ids:
        queue_event(db, uid, {"type": "message.room", "room_id": str(room.id)})
    db.commit()
    return _room_summary(db, room, current_user.id)


@router.get("/groups/{room_id}", response_model=List[RoomMessageResponse])
def group_thread(
    room_id: UUID,
    before: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    room_for_member(db, room_id, current_user.id)
    query = db.query(RoomMessage).options(selectinload(RoomMessage.sender)).filter(RoomMessage.room_id == room_id)
    if before:
        query = query.filter(RoomMessage.created_at < before)
    rows = query.order_by(desc(RoomMessage.created_at)).limit(limit).all()[::-1]
    if not before:
        db.query(ChatMember).filter(
            ChatMember.room_id == room_id, ChatMember.user_id == current_user.id
        ).update({"last_read_at": datetime.now(timezone.utc)})
        db.commit()
    return [_room_message(m, current_user.id) for m in rows]


@router.post("/groups/{room_id}", response_model=RoomMessageResponse)
def send_group_message(
    room_id: UUID,
    data: RoomMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    room = room_for_member(db, room_id, current_user.id)
    content = data.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    msg = post_room_message(db, room, current_user, content)
    db.query(ChatMember).filter(ChatMember.room_id == room.id, ChatMember.user_id == current_user.id).update(
        {"last_read_at": datetime.now(timezone.utc)}
    )
    db.commit()
    db.refresh(msg)
    msg.sender = current_user
    return _room_message(msg, current_user.id)
