import uuid
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Projects owned by this user
    projects = relationship("Project", back_populates="owner")

    # Developer Profile (1-to-1)
    profile = relationship("UserProfile", uselist=False, back_populates="user", cascade="all, delete-orphan")

    # Network activities, likes, comments
    activities = relationship("NetworkActivity", back_populates="user", cascade="all, delete-orphan")
    likes = relationship("ActivityLike", back_populates="user", cascade="all, delete-orphan")
    comments = relationship("ActivityComment", back_populates="author", cascade="all, delete-orphan")

    # Developer connections
    connections_sent = relationship("Connection", foreign_keys="Connection.requester_id", back_populates="requester", cascade="all, delete-orphan")
    connections_received = relationship("Connection", foreign_keys="Connection.receiver_id", back_populates="receiver", cascade="all, delete-orphan")

    # Direct messages
    messages_sent = relationship("Message", foreign_keys="Message.sender_id", back_populates="sender", cascade="all, delete-orphan")
    messages_received = relationship("Message", foreign_keys="Message.receiver_id", back_populates="receiver", cascade="all, delete-orphan")

    # Collaborations
    collaboration_requests_sent = relationship("CollaborationRequest", foreign_keys="CollaborationRequest.requester_id", back_populates="requester", cascade="all, delete-orphan")
    collaboration_requests_received = relationship("CollaborationRequest", foreign_keys="CollaborationRequest.project_owner_id", back_populates="project_owner", cascade="all, delete-orphan")
    collaborations = relationship("ProjectCollaborator", back_populates="user", cascade="all, delete-orphan")

    # Notifications
    notifications_received = relationship("Notification", foreign_keys="Notification.recipient_id", back_populates="recipient", cascade="all, delete-orphan")