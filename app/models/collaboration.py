import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base

class CollaborationRequest(Base):
    __tablename__ = "collaboration_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    requester_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message = Column(Text, nullable=True)
    role = Column(String(100), nullable=True)  # e.g. "Backend Developer", "UI/UX Designer"
    status = Column(String(32), default="pending", nullable=False)  # pending, accepted, declined
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    project = relationship("Project", back_populates="collaboration_requests")
    requester = relationship("User", foreign_keys=[requester_id], back_populates="collaboration_requests_sent")
    project_owner = relationship("User", foreign_keys=[project_owner_id], back_populates="collaboration_requests_received")

class ProjectCollaborator(Base):
    __tablename__ = "project_collaborators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project", back_populates="collaborators")
    user = relationship("User", back_populates="collaborations")

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_collaborator"),
    )
