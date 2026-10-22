import uuid
from sqlalchemy import Column, String, DateTime, Index, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    # Null for accounts created through GitHub until the user sets a password (forgot-password flow).
    password_hash = Column(String, nullable=True)
    github_id = Column(String(32), nullable=True)
    # Bumped when the password changes; tokens carrying an older value stop working.
    token_version = Column(Integer, nullable=False, server_default="0")
    # "developer" or "recruiter"; fixed at sign-up and never changed by profile edits.
    account_type = Column(String(20), nullable=False, default="developer", server_default="developer")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("uq_users_github_id", "github_id", unique=True),
        Index("uq_users_username_lower", func.lower(username), unique=True),
    )

    @property
    def is_recruiter(self) -> bool:
        return self.account_type == "recruiter"

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def username_needs_choice(self) -> bool:
        """Accounts created before handles were required still have an email sitting in the username column."""
        from app.core.policy import normalize_username, username_problem
        return username_problem(normalize_username(self.username or "")) is not None

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