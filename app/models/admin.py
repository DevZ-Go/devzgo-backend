"""Platform admin access, the audit trail, and changes that wait for more than one admin."""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.db.session import Base


class PlatformAdmin(Base):
    """A signed-in account that may open the admin area. The owner row is the recovery account."""

    __tablename__ = "platform_admins"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(16), nullable=False)  # owner | admin
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AdminInvite(Base):
    """An email that should become an admin the next time that account signs up or signs in."""

    __tablename__ = "platform_admin_invites"

    email = Column(String(255), primary_key=True)
    role = Column(String(16), nullable=False, default="admin", server_default="admin")
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AdminProposal(Base):
    """A dangerous change that does not run until enough other admins approve it."""

    __tablename__ = "admin_proposals"
    __table_args__ = (Index("ix_admin_proposals_status_created", "status", "created_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action = Column(String(64), nullable=False)
    target_id = Column(String(80), nullable=True)
    # When set, this person cannot vote. Stops someone approving their own removal.
    target_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    summary = Column(String(300), nullable=False)
    warning = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    required_approvals = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    proposer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    proposer_email = Column(String(255), nullable=False)
    result = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class AdminProposalVote(Base):
    __tablename__ = "admin_proposal_votes"

    proposal_id = Column(UUID(as_uuid=True), ForeignKey("admin_proposals.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    voter_email = Column(String(255), nullable=False)
    decision = Column(String(16), nullable=False)  # approve | reject
    reason = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AdminAuditEvent(Base):
    """Who changed what. Browsing the admin area is not recorded."""

    __tablename__ = "admin_audit_events"
    __table_args__ = (Index("ix_admin_audit_created", "created_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_email = Column(String(255), nullable=True)
    action = Column(String(64), nullable=False)
    summary = Column(String(300), nullable=False)
    severity = Column(String(16), nullable=False)
    target_type = Column(String(64), nullable=True)
    target_id = Column(String(80), nullable=True)
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
