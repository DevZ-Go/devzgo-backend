import uuid
from sqlalchemy import Boolean, Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base

DEFAULT_TIMEZONE = "America/Toronto"

class UserProfile(Base):
    __tablename__ = "profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    full_name = Column(String, nullable=True)
    headline = Column(String, nullable=True)
    role = Column(String(40), nullable=True)
    company = Column(String(120), nullable=True)  # the company or agency, required when role is Recruiter
    guide_seen = Column(Boolean, nullable=False, default=False, server_default="false")
    bio = Column(Text, nullable=True)
    avatar_url = Column(String, nullable=True)
    github_url = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)
    website_url = Column(String, nullable=True)
    skills = Column(Text, nullable=True)  # Comma-separated or JSON list of skills
    location = Column(String, nullable=True)
    timezone = Column(String(64), nullable=False, server_default=DEFAULT_TIMEZONE)  # IANA name; Monthly Wrap months follow it
    profile_visibility = Column(String(20), nullable=False, default="public", server_default="public")
    message_policy = Column(String(20), nullable=False, default="anyone", server_default="anyone")
    comment_policy = Column(String(20), nullable=False, default="anyone", server_default="anyone")
    activity_visibility = Column(String(20), nullable=False, default="public", server_default="public")
    discoverable = Column(Boolean, nullable=False, default=True, server_default="true")
    allow_connection_requests = Column(Boolean, nullable=False, default=True, server_default="true")
    connection_visibility = Column(String(20), nullable=False, default="public", server_default="public")
    collaboration_policy = Column(String(20), nullable=False, default="anyone", server_default="anyone")
    email_visibility = Column(String(20), nullable=False, default="private", server_default="private")
    resume_url = Column(String, nullable=True)
    resume_visibility = Column(String(20), nullable=False, default="connections", server_default="connections")
    notify_likes = Column(Boolean, nullable=False, default=True, server_default="true")
    notify_comments = Column(Boolean, nullable=False, default=True, server_default="true")
    notify_shares = Column(Boolean, nullable=False, default=True, server_default="true")
    notify_connections = Column(Boolean, nullable=False, default=True, server_default="true")
    notify_messages = Column(Boolean, nullable=False, default=True, server_default="true")
    notify_collaborations = Column(Boolean, nullable=False, default=True, server_default="true")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="profile")
