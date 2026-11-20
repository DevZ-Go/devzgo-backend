"""Versioned achievement engine: Experience, Skill Evidence, and Assessment badge families."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

BADGE_FAMILIES = ("experience", "skill_evidence", "assessment")
BADGE_STATUSES = ("active", "disabled", "deprecated")
EVIDENCE_CLASSES = (
    "user_declaration",
    "resume_claim",
    "work_history_claim",
    "project_detection",
    "confirmed_project_tech",
    "contribution_linked",
    "documented_responsibility",
    "assessment_reviewed",
    "third_party_verified",
)


class BadgeDefinition(Base):
    __tablename__ = "badge_definitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stable_key = Column(String(80), nullable=False, unique=True)
    family = Column(String(40), nullable=False)
    track = Column(String(40), nullable=False)  # e.g. full_time, participation, performance, bronze
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=False)
    icon_key = Column(String(80), nullable=False, default="medallion", server_default="medallion")
    status = Column(String(20), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    criteria_versions = relationship("BadgeCriteriaVersion", back_populates="definition", cascade="all, delete-orphan")
    awards = relationship("EarnedBadge", back_populates="definition")


class BadgeCriteriaVersion(Base):
    __tablename__ = "badge_criteria_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    definition_id = Column(UUID(as_uuid=True), ForeignKey("badge_definitions.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    published_criteria = Column(Text, nullable=False)
    rules = Column(JSONB, nullable=False, default=dict)
    published_at = Column(DateTime(timezone=True), server_default=func.now())

    definition = relationship("BadgeDefinition", back_populates="criteria_versions")

    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_badge_criteria_version"),
    )


class EarnedBadge(Base):
    __tablename__ = "earned_badges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    definition_id = Column(UUID(as_uuid=True), ForeignKey("badge_definitions.id", ondelete="CASCADE"), nullable=False)
    criteria_version_id = Column(UUID(as_uuid=True), ForeignKey("badge_criteria_versions.id", ondelete="RESTRICT"), nullable=False)
    awarded_at = Column(DateTime(timezone=True), server_default=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(String(280), nullable=True)
    evidence_refs = Column(JSONB, nullable=False, default=list)
    visibility = Column(String(20), nullable=False, default="connections", server_default="connections")
    provenance_label = Column(String(80), nullable=True)  # self-reported | document-supported | assessed

    definition = relationship("BadgeDefinition", back_populates="awards")
    criteria_version = relationship("BadgeCriteriaVersion")

    __table_args__ = (
        UniqueConstraint("user_id", "definition_id", "criteria_version_id", name="uq_earned_badge"),
        Index("ix_earned_badges_user", "user_id"),
    )


class SkillEvidenceRecord(Base):
    """Provenance-backed skill claim. Extends the skill matrix without replacing it."""
    __tablename__ = "skill_evidence_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tech_stack_id = Column(Integer, ForeignKey("techstacks.id", ondelete="CASCADE"), nullable=False)
    evidence_class = Column(String(40), nullable=False)
    source_type = Column(String(40), nullable=False)  # profile | project | career | resume | assessment
    source_id = Column(String(80), nullable=True)
    source_ref = Column(JSONB, nullable=True)
    review_state = Column(String(20), nullable=False, default="confirmed", server_default="confirmed")
    audience = Column(String(20), nullable=False, default="connections", server_default="connections")
    invalidated = Column(Boolean, nullable=False, default=False, server_default=false())
    invalidation_reason = Column(String(280), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tech_stack = relationship("TechStack")

    __table_args__ = (
        Index("ix_skill_evidence_user_tech", "user_id", "tech_stack_id"),
        Index("ix_skill_evidence_class", "evidence_class"),
    )


class AssessmentAchievementRecord(Base):
    __tablename__ = "assessment_achievement_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), ForeignKey("challenge_submissions.id", ondelete="CASCADE"), nullable=False)
    challenge_id = Column(UUID(as_uuid=True), ForeignKey("technical_challenges.id", ondelete="CASCADE"), nullable=False)
    track = Column(String(40), nullable=False)  # participation | performance
    rating_summary = Column(JSONB, nullable=True)
    eligible = Column(Boolean, nullable=False, default=True, server_default="true")
    ineligible_reason = Column(String(280), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("submission_id", "track", name="uq_assessment_achievement"),
        Index("ix_assessment_achievements_user", "user_id"),
    )
