from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc, func
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.message import Message
from app.models.notification import Notification
from app.core.dependencies import get_current_user
from app.schemas.network import (
    MessageCreate,
    MessageResponse,
    ConversationResponse,
    ConnectionUserSummary,
)

router = APIRouter(prefix="/messages", tags=["Messages"])

def _user_summary(user: User) -> ConnectionUserSummary:
    name = user.profile.full_name if user.profile and user.profile.full_name else user.username
    headline = user.profile.headline if user.profile else None
    avatar = user.profile.avatar_url if user.profile else None
    return ConnectionUserSummary(
        id=user.id,
        username=user.username,
        full_name=name,
        headline=headline,
        avatar_url=avatar,
    )

@router.get("/conversations", response_model=List[ConversationResponse])
def get_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Find all users with whom the current user has exchanged messages
    all_msgs = db.query(Message).filter(
        or_(
            Message.sender_id == current_user.id,
            Message.receiver_id == current_user.id,
        )
    ).order_by(desc(Message.created_at)).all()

    seen_partners = set()
    conversations = []

    for msg in all_msgs:
        partner_id = msg.receiver_id if msg.sender_id == current_user.id else msg.sender_id
        if partner_id in seen_partners:
            continue
        seen_partners.add(partner_id)

        partner = db.query(User).filter(User.id == partner_id).first()
        if not partner:
            continue

        # Count unread messages from this partner
        unread_count = db.query(Message).filter(
            Message.sender_id == partner_id,
            Message.receiver_id == current_user.id,
            Message.read == False,
        ).count()

        conversations.append(
            ConversationResponse(
                other_user=_user_summary(partner),
                last_message=MessageResponse(
                    id=msg.id,
                    sender_id=msg.sender_id,
                    receiver_id=msg.receiver_id,
                    content=msg.content,
                    read=msg.read,
                    created_at=msg.created_at,
                    is_outgoing=(msg.sender_id == current_user.id),
                ),
                unread_count=unread_count,
            )
        )

    return conversations

@router.get("/conversations/{other_user_id}", response_model=List[MessageResponse])
def get_message_thread(
    other_user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    messages = db.query(Message).filter(
        or_(
            and_(Message.sender_id == current_user.id, Message.receiver_id == other_user_id),
            and_(Message.sender_id == other_user_id, Message.receiver_id == current_user.id),
        )
    ).order_by(Message.created_at.asc()).all()

    # Automatically mark incoming messages as read
    incoming_unread = [m for m in messages if m.receiver_id == current_user.id and not m.read]
    if incoming_unread:
        for m in incoming_unread:
            m.read = True
        db.commit()

    return [
        MessageResponse(
            id=m.id,
            sender_id=m.sender_id,
            receiver_id=m.receiver_id,
            content=m.content,
            read=m.read,
            created_at=m.created_at,
            is_outgoing=(m.sender_id == current_user.id),
        )
        for m in messages
    ]

@router.post("", response_model=MessageResponse)
def send_message(
    msg_data: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if msg_data.receiver_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot send message to yourself")

    receiver = db.query(User).filter(User.id == msg_data.receiver_id).first()
    if not receiver:
        raise HTTPException(status_code=404, detail="Recipient developer not found")

    content = msg_data.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    new_msg = Message(
        sender_id=current_user.id,
        receiver_id=msg_data.receiver_id,
        content=content,
        read=False,
    )
    db.add(new_msg)

    # Send notification to receiver
    sender_name = current_user.profile.full_name if current_user.profile and current_user.profile.full_name else current_user.username
    notif = Notification(
        recipient_id=msg_data.receiver_id,
        actor_id=current_user.id,
        type="new_message",
        related_id=str(current_user.id),
        message=f"New message from {sender_name}: \"{content[:40]}...\"",
    )
    db.add(notif)

    db.commit()
    db.refresh(new_msg)

    return MessageResponse(
        id=new_msg.id,
        sender_id=new_msg.sender_id,
        receiver_id=new_msg.receiver_id,
        content=new_msg.content,
        read=new_msg.read,
        created_at=new_msg.created_at,
        is_outgoing=True,
    )

@router.put("/read/{other_user_id}")
def mark_thread_as_read(
    other_user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.query(Message).filter(
        Message.sender_id == other_user_id,
        Message.receiver_id == current_user.id,
        Message.read == False,
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
        Message.read == False,
    ).count()
    return {"unread_count": count}
