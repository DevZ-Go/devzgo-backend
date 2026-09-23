import uuid
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base

class NetworkActivity(Base):
    __tablename__ = "network_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    activity_type = Column(String(64), nullable=False)  # project_added, project_analyzed, profile_updated, wrap_up_published, project_completed, post, collaboration
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(255), nullable=True)
    content = Column(Text, nullable=True)
    media_url = Column(String, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="activities")
    project = relationship("Project", back_populates="activities")
    likes = relationship("ActivityLike", back_populates="activity", cascade="all, delete-orphan")
    comments = relationship("ActivityComment", back_populates="activity", cascade="all, delete-orphan", order_by="ActivityComment.created_at.asc()")

class ActivityLike(Base):
    __tablename__ = "activity_likes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_id = Column(UUID(as_uuid=True), ForeignKey("network_activities.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    activity = relationship("NetworkActivity", back_populates="likes")
    user = relationship("User", back_populates="likes")

    __table_args__ = (
        UniqueConstraint("activity_id", "user_id", name="uq_activity_user_like"),
    )

class ActivityComment(Base):
    __tablename__ = "activity_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_id = Column(UUID(as_uuid=True), ForeignKey("network_activities.id", ondelete="CASCADE"), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    comment_text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    activity = relationship("NetworkActivity", back_populates="comments")
    author = relationship("User", back_populates="comments")
