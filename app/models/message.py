import uuid
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base

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

    sender = relationship("User", foreign_keys=[sender_id], back_populates="messages_sent")
    receiver = relationship("User", foreign_keys=[receiver_id], back_populates="messages_received")

    __table_args__ = (
        Index("ix_messages_pair_created", "sender_id", "receiver_id", "created_at"),
        Index("ix_messages_receiver_unread", "receiver_id", postgresql_where=text("read = false")),
    )


class MessageReaction(Base):
    """One emoji from one person on a direct message. Sending it again removes it."""

    __tablename__ = "message_reactions"

    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    emoji = Column(String(16), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
