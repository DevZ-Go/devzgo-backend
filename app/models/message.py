import uuid
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base

_EXTRAS: bool | None = None


def chat_extras() -> bool:
    """True when this database has the view-once and unsend columns.

    The shared database can be behind the code. Messages must still load, and
    unsend then deletes the row instead of writing a column that is not there.
    """
    global _EXTRAS
    if _EXTRAS is not None:
        return _EXTRAS
    try:
        from sqlalchemy import inspect
        from app.db.session import engine

        info = inspect(engine)
        if not info.has_table("messages"):
            _EXTRAS = True
        else:
            names = {col["name"] for col in info.get_columns("messages")}
            _EXTRAS = "view_once" in names and "unsent_at" in names
    except Exception:
        # A failed check must not pretend the columns exist. Selecting them on Neon would break every thread.
        _EXTRAS = False
    return _EXTRAS


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    receiver_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    # Storage key for a photo or video. Served only to the two people in the thread.
    media_key = Column(String, nullable=True)
    media_kind = Column(String(16), nullable=True)  # image | video | file
    media_name = Column(String(160), nullable=True)
    read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    if chat_extras():
        view_once = Column(Boolean, nullable=False, default=False, server_default="false")
        opened_at = Column(DateTime(timezone=True), nullable=True)
        unsent_at = Column(DateTime(timezone=True), nullable=True)
    else:
        view_once = None
        opened_at = None
        unsent_at = None

    sender = relationship("User", foreign_keys=[sender_id], back_populates="messages_sent")
    receiver = relationship("User", foreign_keys=[receiver_id], back_populates="messages_received")

    __table_args__ = (
        Index("ix_messages_pair_created", "sender_id", "receiver_id", "created_at"),
        Index("ix_messages_receiver_unread", "receiver_id", postgresql_where=text("read = false")),
    )


class MessageHide(Base):
    """The viewer removed this direct message from their own side. The other person still has it."""

    __tablename__ = "message_hides"

    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MessageReaction(Base):
    """One emoji from one person on a direct message. Sending it again removes it."""

    __tablename__ = "message_reactions"

    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    emoji = Column(String(16), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
