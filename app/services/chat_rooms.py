"""Group rooms: one per project team, plus rooms people start themselves."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session, selectinload

from app.models.chat import ChatMember, ChatRoom, RoomMessage
from app.models.collaboration import ProjectCollaborator
from app.models.connection import Connection
from app.models.project import Project
from app.models.user import User
from app.realtime.broadcaster import queue_event
from app.services.social import display_name


def connected(db: Session, a: UUID, b: UUID) -> bool:
    return (
        db.query(Connection.id)
        .filter(
            Connection.status == "accepted",
            or_(
                and_(Connection.requester_id == a, Connection.receiver_id == b),
                and_(Connection.requester_id == b, Connection.receiver_id == a),
            ),
        )
        .first()
        is not None
    )


def sync_project_room(db: Session, project: Project) -> Optional[ChatRoom]:
    """Keep a room whose members are the owner plus every collaborator. No room until someone has joined."""
    member_ids = {project.owner_id}
    member_ids.update(
        row[0] for row in db.query(ProjectCollaborator.user_id).filter(ProjectCollaborator.project_id == project.id)
    )
    if len(member_ids) < 2:
        return None
    room = db.query(ChatRoom).filter(ChatRoom.project_id == project.id).first()
    if room is None:
        room = ChatRoom(title=project.title[:120], project_id=project.id, created_by=project.owner_id)
        db.add(room)
        db.flush()
    elif room.title != project.title[:120]:
        room.title = project.title[:120]
    existing = {row[0] for row in db.query(ChatMember.user_id).filter(ChatMember.room_id == room.id)}
    for uid in member_ids - existing:
        db.add(ChatMember(room_id=room.id, user_id=uid))
    return room


def drop_project_member(db: Session, project_id: UUID, user_id: UUID) -> None:
    room = db.query(ChatRoom).filter(ChatRoom.project_id == project_id).first()
    if room is None:
        return
    db.query(ChatMember).filter(ChatMember.room_id == room.id, ChatMember.user_id == user_id).delete()


def sync_my_project_rooms(db: Session, me: UUID) -> None:
    owned = (
        db.query(Project)
        .filter(Project.owner_id == me)
        .filter(Project.id.in_(db.query(ProjectCollaborator.project_id)))
        .all()
    )
    joined = (
        db.query(Project)
        .filter(Project.id.in_(db.query(ProjectCollaborator.project_id).filter(ProjectCollaborator.user_id == me)))
        .all()
    )
    seen = set()
    for project in [*owned, *joined]:
        if project.id in seen:
            continue
        seen.add(project.id)
        sync_project_room(db, project)


def room_admins(db: Session, room: ChatRoom) -> set[UUID]:
    """Who runs a group: the starter of a custom group, or a project's owner and Managers."""
    if room.project_id is None:
        return {room.created_by}
    project = db.query(Project).filter(Project.id == room.project_id).first()
    if project is None:
        return {room.created_by}
    managers = db.query(ProjectCollaborator.user_id).filter(
        ProjectCollaborator.project_id == project.id, ProjectCollaborator.access == "manager"
    )
    return {project.owner_id, *(row[0] for row in managers)}


def notify_room(db: Session, room: ChatRoom, skip: UUID | None = None) -> None:
    """Tell members to refresh the room after its details or members change."""
    for member in room.members:
        if member.user_id != skip:
            queue_event(db, member.user_id, {"type": "message.room", "room_id": str(room.id)})


def room_for_member(db: Session, room_id: UUID, me: UUID) -> ChatRoom:
    from fastapi import HTTPException

    room = (
        db.query(ChatRoom)
        .options(selectinload(ChatRoom.members))
        .filter(ChatRoom.id == room_id)
        .first()
    )
    if room is None or not any(m.user_id == me for m in room.members):
        raise HTTPException(status_code=404, detail="Chat not found")
    return room


def post_room_message(db: Session, room: ChatRoom, sender: User, content: str) -> RoomMessage:
    msg = RoomMessage(room_id=room.id, sender_id=sender.id, content=content)
    db.add(msg)
    db.flush()
    db.refresh(msg)
    payload = {
        "type": "message.room",
        "room_id": str(room.id),
        "message": {
            "id": str(msg.id),
            "room_id": str(room.id),
            "sender_id": str(sender.id),
            "sender_username": sender.username,
            "sender_name": display_name(sender),
            "content": content,
            "created_at": (msg.created_at or datetime.now(timezone.utc)).isoformat(),
            "is_outgoing": False,
        },
    }
    for member in room.members:
        if member.user_id != sender.id:
            queue_event(db, member.user_id, payload)
    return msg


def unread_count(db: Session, room_id: UUID, me: UUID, last_read) -> int:
    query = db.query(func.count(RoomMessage.id)).filter(
        RoomMessage.room_id == room_id, RoomMessage.sender_id != me
    )
    if last_read is not None:
        query = query.filter(RoomMessage.created_at > last_read)
    return int(query.scalar() or 0)


def latest_preview(db: Session, room_id: UUID):
    return (
        db.query(RoomMessage)
        .filter(RoomMessage.room_id == room_id)
        .order_by(desc(RoomMessage.created_at))
        .first()
    )
