import uuid
from sqlalchemy import Boolean, Column, String, Text, DateTime, ForeignKey, Index, UniqueConstraint, false, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base

PERMISSIONS = ("can_edit_details", "can_manage_files", "can_view_secrets")
ACCESS_LEVELS = ("viewer", "commenter", "editor", "manager")
_LEVEL_FLAGS = {
    "viewer": (),
    "commenter": (),
    "editor": ("can_edit_details", "can_manage_files"),
    "manager": ("can_edit_details", "can_manage_files", "can_view_secrets"),
}


def apply_access(row, level: str) -> None:
    """Set an access level and the permission flags it implies on a member or an invitation."""
    row.access = level
    for flag in PERMISSIONS:
        setattr(row, flag, flag in _LEVEL_FLAGS[level])


def level_can_comment(level: str) -> bool:
    return level != "viewer"

class CollaborationRequest(Base):
    __tablename__ = "collaboration_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    requester_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message = Column(Text, nullable=True)
    role = Column(String(100), nullable=True)  # e.g. "Backend Developer", "UI/UX Designer"
    status = Column(String(32), default="pending", nullable=False)  # pending, accepted, declined, withdrawn
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    project = relationship("Project", back_populates="collaboration_requests")
    requester = relationship("User", foreign_keys=[requester_id], back_populates="collaboration_requests_sent")
    project_owner = relationship("User", foreign_keys=[project_owner_id], back_populates="collaboration_requests_received")

    __table_args__ = (
        Index("ix_collab_requests_project", "project_id"),
        Index("uq_collab_requests_pending", "project_id", "requester_id", unique=True,
              postgresql_where=text("status = 'pending'")),
    )


class ProjectInvitation(Base):
    """The owner invites someone; membership only starts when they accept. Pending past expires_at counts as expired."""
    __tablename__ = "project_invitations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    inviter_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    invitee_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(100), nullable=False)
    message = Column(Text, nullable=True)
    access = Column(String(16), nullable=False, server_default="commenter")
    can_edit_details = Column(Boolean, nullable=False, server_default=false())
    can_manage_files = Column(Boolean, nullable=False, server_default=false())
    can_view_secrets = Column(Boolean, nullable=False, server_default=false())
    status = Column(String(32), nullable=False, server_default="pending")  # pending, accepted, declined, revoked, expired
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    project = relationship("Project")
    inviter = relationship("User", foreign_keys=[inviter_id])
    invitee = relationship("User", foreign_keys=[invitee_id])

    __table_args__ = (
        Index("ix_project_invitations_invitee", "invitee_id"),
        Index("uq_project_invitations_pending", "project_id", "invitee_id", unique=True,
              postgresql_where=text("status = 'pending'")),
    )

class ProjectCollaborator(Base):
    __tablename__ = "project_collaborators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(100), nullable=False)
    access = Column(String(16), nullable=False, server_default="commenter")
    focus = Column(String(200), nullable=True)  # what the member says they are working on
    can_edit_details = Column(Boolean, nullable=False, server_default=false())
    can_manage_files = Column(Boolean, nullable=False, server_default=false())
    can_view_secrets = Column(Boolean, nullable=False, server_default=false())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project", back_populates="collaborators")
    user = relationship("User", back_populates="collaborations")

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_collaborator"),
        Index("ix_project_collaborators_user", "user_id"),
    )
