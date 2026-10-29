from datetime import datetime, timezone

import uuid as uuidlib

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, and_, case, desc, func
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.message import Message, MessageReaction
from app.models.chat import ChatMember, ChatRoom, RoomMessage, RoomMessageReaction
from app.models.collaboration import ProjectCollaborator
from app.models.project import Project
from app.models.safety import ChatMute, UserBlock
from app.services import storage
from app.core.dependencies import get_current_user
from app.services.chat_media import (
    clean_name,
    clip,
    media_response,
    normalize_emoji,
    reaction_map,
    store_upload,
    take_media,
    toggle_reaction,
)
from app.services.safety import blocked_any, blocked_between, is_muted, muted_rooms, quiet_senders, relation
from app.services.social import display_name, may_message, notify, user_summary
from app.services.chat_rooms import (
    connected,
    fixed_room_admins,
    latest_preview,
    notify_room,
    post_room_message,
    room_admins,
    room_for_member,
    sync_my_project_rooms,
    unread_count,
)
from app.realtime.broadcaster import broadcaster, queue_event
from app.schemas.network import (
    ChatRoomCreate,
    ChatRoomDetails,
    ChatRoomMember,
    ChatRoomMembersAdd,
    ChatRoomSummary,
    ChatRoomUpdate,
    AttachmentStored,
    ConversationResponse,
    MessageCreate,
    MessageResponse,
    ReactionCreate,
    RoomMessageCreate,
    RoomMessageResponse,
)

router = APIRouter(prefix="/messages", tags=["Messages"])


def _sender_profile(user: Optional[User]) -> dict:
    profile = user.profile if user else None
    return {
        "sender_username": user.username if user else None,
        "sender_name": display_name(user) if user else None,
        "sender_avatar_url": profile.avatar_url if profile else None,
    }


def _message_response(m: Message, viewer_id: UUID, reactions=None, hide_read: bool = False) -> MessageResponse:
    """hide_read: the other person restricted the viewer, so the viewer never learns they read it."""
    outgoing = m.sender_id == viewer_id
    return MessageResponse(
        id=m.id,
        sender_id=m.sender_id,
        receiver_id=m.receiver_id,
        content=m.content,
        read=False if (hide_read and outgoing) else m.read,
        created_at=m.created_at,
        is_outgoing=outgoing,
        media_kind=m.media_kind,
        media_url=f"/messages/attachments/{m.id}" if m.media_key else None,
        media_name=m.media_name,
        reactions=reactions or [],
        **_sender_profile(m.sender),
    )


def _restricted_by(db: Session, viewer: UUID) -> set[UUID]:
    """People who restricted the viewer."""
    return {
        row[0] for row in db.query(UserBlock.blocker_id)
        .filter(UserBlock.blocked_id == viewer, UserBlock.kind == "restrict")
    }


def _announce_read(db: Session, reader: UUID, sender: UUID) -> None:
    if relation(db, reader, sender) != "restrict":
        queue_event(db, sender, {"type": "message.read", "by": str(reader)})


def _direct_reactions(db: Session, rows: List[Message], viewer: UUID) -> dict:
    return reaction_map(db, MessageReaction, "message_id", [row.id for row in rows], viewer)


def _room_reactions(db: Session, rows: List[RoomMessage], viewer: UUID) -> dict:
    return reaction_map(db, RoomMessageReaction, "message_id", [row.id for row in rows], viewer)


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
        .options(selectinload(Message.sender).selectinload(User.profile))
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
    mine = dict(db.query(UserBlock.blocked_id, UserBlock.kind).filter(UserBlock.blocker_id == me))
    blocked_me = {
        row[0] for row in db.query(UserBlock.blocker_id).filter(UserBlock.blocked_id == me, UserBlock.kind == "block")
    }
    restricted_me = _restricted_by(db, me)
    muted = {
        row[0] for row in db.query(ChatMute.muted_user_id).filter(ChatMute.user_id == me, ChatMute.muted_user_id.isnot(None))
    }

    conversations = [
        ConversationResponse(
            other_user=user_summary(users[pid]),
            last_message=_message_response(m, me, hide_read=pid in restricted_me),
            unread_count=unread.get(pid, 0),
            muted=pid in muted,
            relation=mine.get(pid),
            blocked_me=pid in blocked_me,
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
    query = (
        db.query(Message)
        .options(selectinload(Message.sender).selectinload(User.profile))
        .filter(
            or_(
                and_(Message.sender_id == me, Message.receiver_id == other_user_id),
                and_(Message.sender_id == other_user_id, Message.receiver_id == me),
            )
        )
    )
    if before:
        query = query.filter(Message.created_at < before)
    messages = query.order_by(desc(Message.created_at)).limit(limit).all()[::-1]

    if not before and db.query(Message).filter(
        Message.sender_id == other_user_id, Message.receiver_id == me, Message.read.is_(False)
    ).update({"read": True}):
        _announce_read(db, me, other_user_id)
        db.commit()
        for m in messages:
            if m.receiver_id == me:
                m.read = True

    hide_read = relation(db, other_user_id, me) == "restrict"
    reactions = _direct_reactions(db, messages, me)
    return [_message_response(m, me, reactions.get(m.id), hide_read=hide_read) for m in messages]


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
    if relation(db, current_user.id, recipient.id) == "block":
        raise HTTPException(status_code=403, detail="You blocked this person. Unblock them to send a message.")
    if blocked_between(db, current_user.id, recipient.id):
        raise HTTPException(status_code=403, detail="You can't message this person")
    if not may_message(db, current_user.id, recipient):
        raise HTTPException(status_code=403, detail="This person only accepts messages from connections")

    content = msg_data.content.strip()
    media_key, media_kind = take_media(current_user.id, msg_data.media_key)
    if not content and not media_key:
        raise HTTPException(status_code=400, detail="Write a message or attach a photo, video or file")
    media_name = clean_name(msg_data.media_name, media_key) if media_kind == "file" else None

    new_msg = Message(
        sender_id=current_user.id, receiver_id=msg_data.receiver_id, content=content,
        media_key=media_key, media_kind=media_kind, media_name=media_name, read=False,
    )
    db.add(new_msg)
    snippet = clip(content, media_kind)
    notify(db, msg_data.receiver_id, current_user, "new_message", str(current_user.id),
           f"New message from {display_name(current_user)}: \"{snippet}\"")
    db.commit()
    db.refresh(new_msg)
    new_msg.sender = current_user
    broadcaster.publish(msg_data.receiver_id, {
        "type": "message.new",
        "quiet": relation(db, recipient.id, current_user.id) is not None
        or is_muted(db, recipient.id, person=current_user.id),
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
        _announce_read(db, current_user.id, other_user_id)
    db.commit()
    return {"message": "Messages marked as read"}


@router.get("/unread-count")
def get_unread_messages_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Muted chats, and people you blocked or restricted, don't add to the badge."""
    quiet = quiet_senders(db, current_user.id)
    direct_query = db.query(Message).filter(
        Message.receiver_id == current_user.id,
        Message.read.is_(False),
    )
    if quiet:
        direct_query = direct_query.filter(Message.sender_id.notin_(quiet))
    direct = direct_query.count()
    silent_rooms = muted_rooms(db, current_user.id)
    groups_query = (
        db.query(func.count(RoomMessage.id))
        .join(ChatMember, and_(ChatMember.room_id == RoomMessage.room_id, ChatMember.user_id == current_user.id))
        .filter(
            RoomMessage.sender_id != current_user.id,
            or_(ChatMember.last_read_at.is_(None), RoomMessage.created_at > ChatMember.last_read_at),
        )
    )
    if silent_rooms:
        groups_query = groups_query.filter(RoomMessage.room_id.notin_(silent_rooms))
    groups = groups_query.scalar() or 0
    return {"unread_count": direct + groups, "direct": direct, "groups": groups}


def _room_summary(db: Session, room: ChatRoom, me: UUID, muted: Optional[set] = None) -> ChatRoomSummary:
    mine = next((m for m in room.members if m.user_id == me), None)
    last = latest_preview(db, room.id)
    return ChatRoomSummary(
        id=room.id,
        title=room.title,
        avatar_url=room.avatar_url,
        project_id=room.project_id,
        member_count=len(room.members),
        unread_count=unread_count(db, room.id, me, mine.last_read_at if mine else None),
        last_message=clip(last.content, last.media_kind) if last else None,
        last_at=last.created_at if last else None,
        muted=room.id in (muted if muted is not None else muted_rooms(db, me)),
    )


def _room_message(msg: RoomMessage, viewer: UUID, reactions=None) -> RoomMessageResponse:
    sender = msg.sender
    profile = sender.profile if sender else None
    return RoomMessageResponse(
        id=msg.id,
        room_id=msg.room_id,
        sender_id=msg.sender_id,
        sender_username=sender.username if sender else "developer",
        sender_name=display_name(sender) if sender else "Developer",
        sender_avatar_url=profile.avatar_url if profile else None,
        content=msg.content,
        created_at=msg.created_at,
        is_outgoing=msg.sender_id == viewer,
        media_kind=msg.media_kind,
        media_url=f"/messages/groups/{msg.room_id}/attachments/{msg.id}" if msg.media_key else None,
        media_name=msg.media_name,
        reactions=reactions or [],
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
    muted = muted_rooms(db, current_user.id)
    summaries = [_room_summary(db, room, current_user.id, muted) for room in rooms]
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
    if blocked_any(db, current_user.id, member_ids):
        raise HTTPException(status_code=400, detail="You can't add someone you blocked, or who blocked you")
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
    query = (
        db.query(RoomMessage)
        .options(selectinload(RoomMessage.sender).selectinload(User.profile))
        .filter(RoomMessage.room_id == room_id)
    )
    if before:
        query = query.filter(RoomMessage.created_at < before)
    rows = query.order_by(desc(RoomMessage.created_at)).limit(limit).all()[::-1]
    if not before:
        db.query(ChatMember).filter(
            ChatMember.room_id == room_id, ChatMember.user_id == current_user.id
        ).update({"last_read_at": datetime.now(timezone.utc)})
        db.commit()
    reactions = _room_reactions(db, rows, current_user.id)
    return [_room_message(m, current_user.id, reactions.get(m.id)) for m in rows]


@router.put("/groups/{room_id}/read")
def mark_group_read(room_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    room_for_member(db, room_id, current_user.id)
    db.query(ChatMember).filter(ChatMember.room_id == room_id, ChatMember.user_id == current_user.id).update(
        {"last_read_at": datetime.now(timezone.utc)}
    )
    db.commit()
    return {"message": "Group marked as read"}


@router.post("/groups/{room_id}", response_model=RoomMessageResponse)
def send_group_message(
    room_id: UUID,
    data: RoomMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    room = room_for_member(db, room_id, current_user.id)
    content = data.content.strip()
    media_key, media_kind = take_media(current_user.id, data.media_key)
    if not content and not media_key:
        raise HTTPException(status_code=400, detail="Write a message or attach a photo, video or file")
    media_name = clean_name(data.media_name, media_key) if media_kind == "file" else None
    msg = post_room_message(
        db, room, current_user, content, media_key=media_key, media_kind=media_kind, media_name=media_name,
    )
    db.query(ChatMember).filter(ChatMember.room_id == room.id, ChatMember.user_id == current_user.id).update(
        {"last_read_at": datetime.now(timezone.utc)}
    )
    db.commit()
    db.refresh(msg)
    msg.sender = current_user
    return _room_message(msg, current_user.id)


PHOTO_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def _admin_room(db: Session, room_id: UUID, me: UUID) -> ChatRoom:
    room = room_for_member(db, room_id, me)
    if me not in room_admins(db, room):
        raise HTTPException(status_code=403, detail="Only a group admin can do that")
    return room


def _custom_only(room: ChatRoom) -> None:
    if room.project_id is not None:
        raise HTTPException(status_code=400, detail="A project team chat follows the team. Change members from the project page.")


def _room_details(db: Session, room: ChatRoom, me: UUID) -> ChatRoomDetails:
    fixed = fixed_room_admins(db, room)
    admins = room_admins(db, room)
    members = (
        db.query(ChatMember)
        .options(selectinload(ChatMember.user).selectinload(User.profile))
        .filter(ChatMember.room_id == room.id)
        .all()
    )
    team_roles: dict = {}
    project_title = None
    if room.project_id:
        project = db.query(Project).filter(Project.id == room.project_id).first()
        if project:
            project_title = project.title
            team_roles[project.owner_id] = "Owner"
            for user_id, role in db.query(ProjectCollaborator.user_id, ProjectCollaborator.role).filter(
                ProjectCollaborator.project_id == project.id
            ):
                team_roles[user_id] = role
    rows = [
        ChatRoomMember(
            user_id=m.user_id,
            username=m.user.username,
            full_name=display_name(m.user),
            avatar_url=m.user.profile.avatar_url if m.user.profile else None,
            is_admin=m.user_id in admins,
            admin_fixed=m.user_id in fixed,
            team_role=team_roles.get(m.user_id),
            joined_at=m.joined_at,
        )
        for m in members if m.user
    ]
    rows.sort(key=lambda r: (not r.is_admin, (r.full_name or r.username).lower()))
    return ChatRoomDetails(
        id=room.id,
        title=room.title,
        avatar_url=room.avatar_url,
        project_id=room.project_id,
        project_title=project_title,
        created_at=room.created_at,
        is_admin=me in admins,
        muted=is_muted(db, me, room=room.id),
        members=rows,
    )


@router.get("/groups/{room_id}/details", response_model=ChatRoomDetails)
def group_details(room_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _room_details(db, room_for_member(db, room_id, current_user.id), current_user.id)


@router.patch("/groups/{room_id}", response_model=ChatRoomDetails)
def rename_group(room_id: UUID, data: ChatRoomUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    room = _admin_room(db, room_id, current_user.id)
    _custom_only(room)
    room.title = data.title.strip()[:120]
    notify_room(db, room, skip=current_user.id)
    db.commit()
    return _room_details(db, room, current_user.id)


@router.post("/groups/{room_id}/photo", response_model=ChatRoomDetails)
def set_group_photo(
    room_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    room = _admin_room(db, room_id, current_user.id)
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in PHOTO_EXTS or not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Use a PNG, JPG, GIF, or WebP image")
    file.file.seek(0, 2)
    too_big = file.file.tell() > 5 * 1024 * 1024
    file.file.seek(0)
    if too_big:
        raise HTTPException(status_code=413, detail="Photo is larger than 5 MB")
    name = f"room_{room.id}_{uuidlib.uuid4().hex}.{ext}"
    storage.save(f"avatars/{name}", file.file)
    room.avatar_url = f"/storage/avatars/{name}"
    notify_room(db, room, skip=current_user.id)
    db.commit()
    return _room_details(db, room, current_user.id)


@router.post("/groups/{room_id}/members", response_model=ChatRoomDetails)
def add_group_members(room_id: UUID, data: ChatRoomMembersAdd, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    room = _admin_room(db, room_id, current_user.id)
    _custom_only(room)
    present = {m.user_id for m in room.members}
    new_ids = set(data.member_ids) - present
    if len(present) + len(new_ids) > 50:
        raise HTTPException(status_code=400, detail="A group can have at most 50 people")
    for uid in new_ids:
        if not db.query(User.id).filter(User.id == uid).first():
            raise HTTPException(status_code=404, detail="One of those people could not be found")
        if not connected(db, current_user.id, uid):
            raise HTTPException(status_code=400, detail="You can only add people you're connected to")
    if blocked_any(db, current_user.id, new_ids):
        raise HTTPException(status_code=400, detail="You can't add someone you blocked, or who blocked you")
    for uid in new_ids:
        db.add(ChatMember(room_id=room.id, user_id=uid))
    db.flush()
    db.refresh(room)
    notify_room(db, room, skip=current_user.id)
    db.commit()
    return _room_details(db, room, current_user.id)


@router.delete("/groups/{room_id}/members/{user_id}", response_model=ChatRoomDetails)
def remove_group_member(room_id: UUID, user_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    room = _admin_room(db, room_id, current_user.id)
    _custom_only(room)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Use Leave group to leave")
    fixed = fixed_room_admins(db, room)
    if user_id in fixed:
        raise HTTPException(status_code=403, detail="The person who started the group can't be removed")
    if user_id in room_admins(db, room) and current_user.id not in fixed:
        raise HTTPException(status_code=403, detail="Only the person who started the group can remove another admin")
    removed = db.query(ChatMember).filter(ChatMember.room_id == room.id, ChatMember.user_id == user_id).delete()
    if not removed:
        raise HTTPException(status_code=404, detail="That person is not in this group")
    queue_event(db, user_id, {"type": "message.room", "room_id": str(room.id)})
    db.flush()
    db.refresh(room)
    notify_room(db, room, skip=current_user.id)
    db.commit()
    return _room_details(db, room, current_user.id)


def _set_admin(db: Session, room_id: UUID, user_id: UUID, me: User, value: bool) -> ChatRoomDetails:
    room = _admin_room(db, room_id, me.id)
    _custom_only(room)
    member = db.query(ChatMember).filter(ChatMember.room_id == room.id, ChatMember.user_id == user_id).first()
    if member is None:
        raise HTTPException(status_code=404, detail="That person is not in this group")
    if user_id in fixed_room_admins(db, room):
        raise HTTPException(status_code=400, detail="The person who started the group is always an admin")
    if not value and user_id != me.id and me.id not in fixed_room_admins(db, room):
        raise HTTPException(status_code=403, detail="Only the person who started the group can remove another admin")
    member.is_admin = value
    db.flush()
    notify_room(db, room, skip=me.id)
    db.commit()
    return _room_details(db, room, me.id)


@router.post("/groups/{room_id}/admins/{user_id}", response_model=ChatRoomDetails)
def make_group_admin(room_id: UUID, user_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _set_admin(db, room_id, user_id, current_user, True)


@router.delete("/groups/{room_id}/admins/{user_id}", response_model=ChatRoomDetails)
def remove_group_admin(room_id: UUID, user_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _set_admin(db, room_id, user_id, current_user, False)


@router.post("/groups/{room_id}/leave")
def leave_group(room_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    room = room_for_member(db, room_id, current_user.id)
    if room.project_id is not None:
        raise HTTPException(status_code=400, detail="This chat belongs to a project team. Leave the project to leave its chat.")
    db.query(ChatMember).filter(ChatMember.room_id == room.id, ChatMember.user_id == current_user.id).delete()
    db.flush()
    remaining = (
        db.query(ChatMember).filter(ChatMember.room_id == room.id).order_by(ChatMember.joined_at).all()
    )
    if not remaining:
        db.query(ChatRoom).filter(ChatRoom.id == room.id).delete(synchronize_session=False)
    else:
        if room.created_by == current_user.id:
            successor = next((m for m in remaining if m.is_admin), remaining[0])
            room.created_by = successor.user_id
        db.flush()
        db.refresh(room)
        notify_room(db, room)
    db.commit()
    return {"message": "You left the group"}


def _direct_for(db: Session, message_id: UUID, me: UUID) -> Message:
    msg = (
        db.query(Message)
        .options(selectinload(Message.sender).selectinload(User.profile))
        .filter(Message.id == message_id)
        .first()
    )
    if msg is None or me not in (msg.sender_id, msg.receiver_id):
        raise HTTPException(status_code=404, detail="Message not found")
    return msg


def _room_for(db: Session, room_id: UUID, message_id: UUID, me: UUID) -> RoomMessage:
    room_for_member(db, room_id, me)
    msg = (
        db.query(RoomMessage)
        .options(selectinload(RoomMessage.sender).selectinload(User.profile))
        .filter(RoomMessage.id == message_id, RoomMessage.room_id == room_id)
        .first()
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return msg


def _publish_direct_reaction(msg: Message) -> None:
    broadcaster.publish(msg.receiver_id, {
        "type": "message.reaction", "message_id": str(msg.id), "with_user": str(msg.sender_id),
    })
    broadcaster.publish(msg.sender_id, {
        "type": "message.reaction", "message_id": str(msg.id), "with_user": str(msg.receiver_id),
    })


@router.post("/attachments", response_model=AttachmentStored)
def upload_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    key, kind = store_upload(current_user.id, file)
    return AttachmentStored(
        media_key=key, media_kind=kind, media_name=clean_name(file.filename, key) if kind == "file" else None,
    )


@router.get("/attachments/{message_id}")
def direct_attachment(message_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    msg = _direct_for(db, message_id, current_user.id)
    if not msg.media_key:
        raise HTTPException(status_code=404, detail="That file is gone")
    return media_response(msg.media_key, msg.media_name)


@router.post("/{message_id}/reactions", response_model=MessageResponse)
def react_to_message(
    message_id: UUID,
    data: ReactionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    msg = _direct_for(db, message_id, current_user.id)
    toggle_reaction(db, MessageReaction, "message_id", msg.id, current_user.id, normalize_emoji(data.emoji))
    db.commit()
    _publish_direct_reaction(msg)
    reactions = _direct_reactions(db, [msg], current_user.id)
    return _message_response(msg, current_user.id, reactions.get(msg.id))


@router.get("/groups/{room_id}/attachments/{message_id}")
def group_attachment(
    room_id: UUID,
    message_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    msg = _room_for(db, room_id, message_id, current_user.id)
    if not msg.media_key:
        raise HTTPException(status_code=404, detail="That file is gone")
    return media_response(msg.media_key, msg.media_name)


@router.post("/groups/{room_id}/{message_id}/reactions", response_model=RoomMessageResponse)
def react_to_group_message(
    room_id: UUID,
    message_id: UUID,
    data: ReactionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    room = room_for_member(db, room_id, current_user.id)
    msg = _room_for(db, room_id, message_id, current_user.id)
    toggle_reaction(db, RoomMessageReaction, "message_id", msg.id, current_user.id, normalize_emoji(data.emoji))
    for member in room.members:
        queue_event(db, member.user_id, {
            "type": "message.reaction", "room_id": str(room.id), "message_id": str(msg.id),
        })
    db.commit()
    reactions = _room_reactions(db, [msg], current_user.id)
    return _room_message(msg, current_user.id, reactions.get(msg.id))
