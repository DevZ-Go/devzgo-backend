import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(64), nullable=False)  # connection_request, connection_accepted, new_message, collaboration_request, collaboration_accepted, comment, like
    related_id = Column(String(255), nullable=True)  # ID of related entity (project, activity, user, etc.)
    message = Column(String(255), nullable=False)
    read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    recipient = relationship("User", foreign_keys=[recipient_id], back_populates="notifications_received")
    actor = relationship("User", foreign_keys=[actor_id])
