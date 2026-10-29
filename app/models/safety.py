import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.session import Base


class UserBlock(Base):
    """One person's choice about another: block (no contact either way) or restrict (quiet, no read receipts)."""

    __tablename__ = "user_blocks"

    blocker_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    blocked_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind = Column(String(16), nullable=False, default="block", server_default="block")  # block | restrict
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_user_blocks_blocked", "blocked_id"),)


class ChatMute(Base):
    """A muted direct chat (muted_user_id) or group (room_id). Messages still arrive, quietly."""

    __tablename__ = "chat_mutes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    muted_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    room_id = Column(UUID(as_uuid=True), ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("uq_chat_mutes_user_person", "user_id", "muted_user_id", unique=True),
        Index("uq_chat_mutes_user_room", "user_id", "room_id", unique=True),
    )
