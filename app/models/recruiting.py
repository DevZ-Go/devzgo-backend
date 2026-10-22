"""Recruiter tools: private shortlists and contact requests a candidate can accept or decline."""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

ENTRY_STATUSES = ("new", "reviewing", "contacted", "interviewing", "hired", "not_a_fit")
REQUEST_STATUSES = ("pending", "accepted", "declined", "withdrawn")


class Shortlist(Base):
    """Only its owner ever sees a shortlist, its entries, or its notes."""
    __tablename__ = "shortlists"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(80), nullable=False)
    description = Column(String(280), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    entries = relationship("ShortlistEntry", back_populates="shortlist", cascade="all, delete-orphan",
                           order_by="ShortlistEntry.created_at")

    __table_args__ = (Index("ix_shortlists_owner", "owner_id"),)


class ShortlistEntry(Base):
    __tablename__ = "shortlist_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shortlist_id = Column(UUID(as_uuid=True), ForeignKey("shortlists.id", ondelete="CASCADE"), nullable=False)
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(20), nullable=False, default="new", server_default="new")
    note = Column(Text, nullable=True)  # private to the shortlist owner
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    shortlist = relationship("Shortlist", back_populates="entries")
    candidate = relationship("User", foreign_keys=[candidate_id])

    __table_args__ = (
        UniqueConstraint("shortlist_id", "candidate_id", name="uq_shortlist_entries_candidate"),
        Index("ix_shortlist_entries_candidate", "candidate_id"),
    )


class RecruitmentRequest(Base):
    """A recruiter asks to talk about a role. Accepting opens direct messages between the two."""
    __tablename__ = "recruitment_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recruiter_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_title = Column(String(120), nullable=False)
    work_type = Column(String(20), nullable=True)
    message = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending", server_default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    responded_at = Column(DateTime(timezone=True), nullable=True)

    recruiter = relationship("User", foreign_keys=[recruiter_id])
    candidate = relationship("User", foreign_keys=[candidate_id])

    __table_args__ = (
        Index("ix_recruitment_requests_candidate_status", "candidate_id", "status"),
        Index("ix_recruitment_requests_recruiter_created", "recruiter_id", "created_at"),
    )
