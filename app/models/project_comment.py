import uuid
from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base


class ProjectComment(Base):
    """Comments on a project (separate from comments on Network posts). Supports threaded replies."""

    __tablename__ = "project_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    parent_id = Column(
        UUID(as_uuid=True), ForeignKey("project_comments.id", ondelete="CASCADE"), nullable=True
    )
    body = Column(String(2000), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    author = relationship("User")
    parent = relationship("ProjectComment", remote_side=[id], backref="replies")
    likes = relationship("ProjectCommentLike", back_populates="comment", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_project_comments_project_created", "project_id", created_at.desc(), id.desc()),
        Index("ix_project_comments_parent", "parent_id", "created_at"),
    )


class ProjectCommentLike(Base):
    __tablename__ = "project_comment_likes"

    comment_id = Column(UUID(as_uuid=True), ForeignKey("project_comments.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    comment = relationship("ProjectComment", back_populates="likes")

    __table_args__ = (Index("ix_project_comment_likes_user", "user_id"),)
