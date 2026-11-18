from datetime import datetime, timedelta, timezone

import uuid as uuidlib

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, and_, case, desc, func
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.message import Message, MessageHide, MessageReaction
from app.models.chat import ChatMember, ChatRoom, RoomMessage, RoomMessageHide, RoomMessageOpen, RoomMessageReaction
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


def _has_table(db: Session, name: str) -> bool:
    try:
        return sa_inspect(db.get_bind()).has_table(name)
    except Exception:
        return False


def _message_response(
    m: Message, viewer_id: UUID, reactions=None, hide_read: bool = False, *, reveal: bool = False,
) -> MessageResponse:
    """hide_read: the other person restricted the viewer, so the viewer never learns they read it.

    A view-once message stays visible to the sender until it is opened. Everyone else
    gets a sealed note, unless this response is the one-time open.
    """
    outgoing = m.sender_id == viewer_id
    unsent = m.unsent_at is not None
    opened = m.opened_at is not None
    show_body = not unsent and (not m.view_once or reveal or (outgoing and not opened))
    return MessageResponse(
        id=m.id,
        sender_id=m.sender_id,
        receiver_id=m.receiver_id,
        content=m.content if show_body else "",
        read=False if (hide_read and outgoing) else m.read,
        created_at=m.created_at,
        is_outgoing=outgoing,
        media_kind=None if unsent else m.media_kind,
        media_url=f"/messages/attachments/{m.id}" if show_body and m.media_key else None,
        media_name=m.media_name if show_body else None,
        view_once=bool(m.view_once) and not unsent,
        opened=opened and not unsent,
        unsent=unsent,
        reactions=[] if unsent else (reactions or []),
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
    if _has_table(db, "message_hides"):
        hidden = db.query(MessageHide.message_id).filter(MessageHide.user_id == me)
        query = query.filter(~Message.id.in_(hidden))
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
    from app.services.account_lifecycle import require_active_recipient
    require_active_recipient(recipient)
    mine = relation(db, current_user.id, recipient.id)
    theirs = relation(db, recipient.id, current_user.id)
    if mine == "block":
        raise HTTPException(status_code=403, detail="You blocked this person. Unblock them to send a message.")
    if mine == "restrict":
        raise HTTPException(status_code=403, detail="You restricted this person. Remove the restrict to message them.")
    if theirs == "block" or blocked_between(db, current_user.id, recipient.id):
        raise HTTPException(status_code=403, detail="You can't message this person")
    if theirs == "restrict":
        raise HTTPException(status_code=403, detail="You can't message this person")
    if not may_message(db, current_user.id, recipient):
        raise HTTPException(status_code=403, detail="This person only accepts messages from connections")

    content = msg_data.content.strip()
    media_key, media_kind = take_media(current_user.id, msg_data.media_key)
    if not content and not media_key:
        raise HTTPException(status_code=400, detail="Write a message or attach a photo, video or file")
    media_name = clean_name(msg_data.media_name, media_key) if media_kind == "file" else None

    fields = dict(
        sender_id=current_user.id, receiver_id=msg_data.receiver_id, content=content,
        media_key=media_key, media_kind=media_kind, media_name=media_name, read=False,
    )
    from app.models.message import chat_extras
    if chat_extras():
        fields["view_once"] = bool(msg_data.view_once)
    new_msg = Message(**fields)
    db.add(new_msg)
    snippet = "View once" if msg_data.view_once else clip(content, media_kind)
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
        last_message=_room_preview(last),
        last_at=last.created_at if last else None,
        muted=room.id in (muted if muted is not None else muted_rooms(db, me)),
    )


def _room_preview(last: Optional[RoomMessage]) -> Optional[str]:
    if last is None:
        return None
    if last.unsent_at is not None:
        return "Unsent a message"
    if last.view_once:
        return "View once"
    return clip(last.content, last.media_kind)


def _room_opened(db: Session, rows: List[RoomMessage], viewer: UUID) -> set:
    ids = [row.id for row in rows if row.view_once]
    if not ids:
        return set()
    return {
        row[0]
        for row in db.query(RoomMessageOpen.message_id).filter(
            RoomMessageOpen.user_id == viewer, RoomMessageOpen.message_id.in_(ids)
        )
    }


def _room_message(
    msg: RoomMessage, viewer: UUID, reactions=None, *, opened: bool = False, reveal: bool = False,
) -> RoomMessageResponse:
    sender = msg.sender
    profile = sender.profile if sender else None
    outgoing = msg.sender_id == viewer
    unsent = msg.unsent_at is not None
    show_body = not unsent and (not msg.view_once or outgoing or reveal)
    return RoomMessageResponse(
        id=msg.id,
        room_id=msg.room_id,
        sender_id=msg.sender_id,
        sender_username=sender.username if sender else "developer",
        sender_name=display_name(sender) if sender else "Developer",
        sender_avatar_url=profile.avatar_url if profile else None,
        content=msg.content if show_body else "",
        created_at=msg.created_at,
        is_outgoing=outgoing,
        media_kind=None if unsent else msg.media_kind,
        media_url=f"/messages/groups/{msg.room_id}/attachments/{msg.id}" if show_body and msg.media_key else None,
        media_name=msg.media_name if show_body else None,
        view_once=bool(msg.view_once) and not unsent,
        opened=opened and bool(msg.view_once) and not unsent,
        unsent=unsent,
        reactions=[] if unsent else (reactions or []),
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
    if _has_table(db, "room_message_hides"):
        hidden = db.query(RoomMessageHide.message_id).filter(RoomMessageHide.user_id == current_user.id)
        query = query.filter(~RoomMessage.id.in_(hidden))
    if before:
        query = query.filter(RoomMessage.created_at < before)
    rows = query.order_by(desc(RoomMessage.created_at)).limit(limit).all()[::-1]
    if not before:
        db.query(ChatMember).filter(
            ChatMember.room_id == room_id, ChatMember.user_id == current_user.id
        ).update({"last_read_at": datetime.now(timezone.utc)})
        db.commit()
    reactions = _room_reactions(db, rows, current_user.id)
    opened = _room_opened(db, rows, current_user.id)
    return [_room_message(m, current_user.id, reactions.get(m.id), opened=m.id in opened) for m in rows]


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
        view_once=bool(data.view_once),
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


def _forget_file(key: Optional[str]) -> None:
    if not key:
        return
    try:
        storage.delete_prefix(key)
    except Exception:
        return


def _clear_direct_file(msg: Message) -> None:
    key = msg.media_key
    msg.media_key = None
    msg.media_kind = None
    msg.media_name = None
    _forget_file(key)


def _publish_direct_update(db: Session, msg: Message) -> None:
    for viewer, other in ((msg.receiver_id, msg.sender_id), (msg.sender_id, msg.receiver_id)):
        broadcaster.publish(viewer, {
            "type": "message.updated",
            "with_user": str(other),
            "message": _message_response(msg, viewer).model_dump(mode="json"),
        })


def _publish_room_update(db: Session, room: ChatRoom, msg: RoomMessage) -> None:
    for member in room.members:
        queue_event(db, member.user_id, {
            "type": "message.updated",
            "room_id": str(room.id),
            "message_id": str(msg.id),
        })


def _direct_view_window(db: Session, msg: Message, viewer: UUID) -> None:
    """Attachment bytes for a view-once direct message, only while it is being opened."""
    if not msg.view_once:
        return
    now = datetime.now(timezone.utc)
    if viewer == msg.sender_id:
        if msg.opened_at is not None:
            raise HTTPException(status_code=410, detail="This view-once message was opened")
        return
    if msg.opened_at is None:
        raise HTTPException(status_code=403, detail="Open the view-once message first")
    opened = msg.opened_at if msg.opened_at.tzinfo else msg.opened_at.replace(tzinfo=timezone.utc)
    if now - opened > timedelta(minutes=3):
        _clear_direct_file(msg)
        db.commit()
        raise HTTPException(status_code=410, detail="This view-once message was opened")


@router.post("/{message_id}/hide")
def hide_message(message_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    msg = _direct_for(db, message_id, current_user.id)
    if _has_table(db, "message_hides"):
        exists = db.query(MessageHide).filter(MessageHide.message_id == msg.id, MessageHide.user_id == current_user.id).first()
        if exists is None:
            db.add(MessageHide(message_id=msg.id, user_id=current_user.id))
            db.commit()
    return {"message": "Deleted for you"}


@router.post("/{message_id}/unsend", response_model=MessageResponse)
def unsend_message(message_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    msg = _direct_for(db, message_id, current_user.id)
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the sender can unsend a message")
    key = msg.media_key
    payload = _message_response(msg, current_user.id).model_copy(update={
        "content": "", "media_url": None, "media_kind": None, "media_name": None,
        "view_once": False, "unsent": True, "reactions": [],
    })
    for viewer, other in ((msg.receiver_id, msg.sender_id), (msg.sender_id, msg.receiver_id)):
        broadcaster.publish(viewer, {
            "type": "message.updated",
            "with_user": str(other),
            "message": payload.model_dump(mode="json"),
        })
    db.query(MessageReaction).filter(MessageReaction.message_id == msg.id).delete()
    db.delete(msg)
    db.commit()
    _forget_file(key)
    return payload


@router.post("/{message_id}/open", response_model=MessageResponse)
def open_view_once(message_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    msg = _direct_for(db, message_id, current_user.id)
    if msg.unsent_at is not None or not msg.view_once:
        raise HTTPException(status_code=404, detail="That view-once message is gone")
    if msg.sender_id == current_user.id:
        return _message_response(msg, current_user.id, reveal=msg.opened_at is None)
    if msg.opened_at is not None:
        return _message_response(msg, current_user.id)
    msg.opened_at = datetime.now(timezone.utc)
    msg.read = True
    db.commit()
    _publish_direct_update(db, msg)
    return _message_response(msg, current_user.id, reveal=True)


@router.post("/{message_id}/consume", response_model=MessageResponse)
def consume_view_once(message_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    msg = _direct_for(db, message_id, current_user.id)
    if msg.view_once and msg.sender_id != current_user.id and msg.opened_at is not None:
        _clear_direct_file(msg)
        db.commit()
        _publish_direct_update(db, msg)
    return _message_response(msg, current_user.id)


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
    if msg.unsent_at is not None or not msg.media_key:
        raise HTTPException(status_code=404, detail="That file is gone")
    _direct_view_window(db, msg, current_user.id)
    db.commit()
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
    if msg.unsent_at is not None or not msg.media_key:
        raise HTTPException(status_code=404, detail="That file is gone")
    if msg.view_once and msg.sender_id != current_user.id:
        row = db.query(RoomMessageOpen).filter(
            RoomMessageOpen.message_id == msg.id, RoomMessageOpen.user_id == current_user.id
        ).one_or_none()
        if row is None or row.consumed_at is not None:
            raise HTTPException(status_code=403, detail="Open the view-once message first")
        opened = row.opened_at if row.opened_at.tzinfo else row.opened_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - opened > timedelta(minutes=3):
            raise HTTPException(status_code=410, detail="This view-once message was opened")
    return media_response(msg.media_key, msg.media_name)


@router.post("/groups/{room_id}/{message_id}/hide")
def hide_group_message(
    room_id: UUID, message_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    msg = _room_for(db, room_id, message_id, current_user.id)
    if _has_table(db, "room_message_hides"):
        exists = db.query(RoomMessageHide).filter(
            RoomMessageHide.message_id == msg.id, RoomMessageHide.user_id == current_user.id
        ).first()
        if exists is None:
            db.add(RoomMessageHide(message_id=msg.id, user_id=current_user.id))
            db.commit()
    return {"message": "Deleted for you"}


@router.post("/groups/{room_id}/{message_id}/unsend", response_model=RoomMessageResponse)
def unsend_group_message(
    room_id: UUID, message_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    room = room_for_member(db, room_id, current_user.id)
    msg = _room_for(db, room_id, message_id, current_user.id)
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the sender can unsend a message")
    key = msg.media_key
    payload = _room_message(msg, current_user.id).model_copy(update={
        "content": "", "media_url": None, "media_kind": None, "media_name": None,
        "view_once": False, "unsent": True, "reactions": [],
    })
    for member in room.members:
        queue_event(db, member.user_id, {
            "type": "message.updated",
            "room_id": str(room.id),
            "message_id": str(msg.id),
        })
    db.query(RoomMessageReaction).filter(RoomMessageReaction.message_id == msg.id).delete()
    db.delete(msg)
    db.commit()
    _forget_file(key)
    return payload


@router.post("/groups/{room_id}/{message_id}/open", response_model=RoomMessageResponse)
def open_group_view_once(
    room_id: UUID, message_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    room = room_for_member(db, room_id, current_user.id)
    msg = _room_for(db, room_id, message_id, current_user.id)
    if msg.unsent_at is not None or not msg.view_once:
        raise HTTPException(status_code=404, detail="That view-once message is gone")
    if msg.sender_id == current_user.id:
        return _room_message(msg, current_user.id, reveal=True)
    row = db.query(RoomMessageOpen).filter(
        RoomMessageOpen.message_id == msg.id, RoomMessageOpen.user_id == current_user.id
    ).one_or_none()
    if row is None:
        db.add(RoomMessageOpen(message_id=msg.id, user_id=current_user.id))
        _publish_room_update(db, room, msg)
        db.commit()
        return _room_message(msg, current_user.id, reveal=True, opened=True)
    return _room_message(msg, current_user.id, opened=True)


@router.post("/groups/{room_id}/{message_id}/consume", response_model=RoomMessageResponse)
def consume_group_view_once(
    room_id: UUID, message_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    room_for_member(db, room_id, current_user.id)
    msg = _room_for(db, room_id, message_id, current_user.id)
    row = db.query(RoomMessageOpen).filter(
        RoomMessageOpen.message_id == msg.id, RoomMessageOpen.user_id == current_user.id
    ).one_or_none()
    if row is not None and row.consumed_at is None:
        row.consumed_at = datetime.now(timezone.utc)
        db.commit()
    return _room_message(msg, current_user.id, opened=row is not None)


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
