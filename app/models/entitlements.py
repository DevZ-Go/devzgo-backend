"""Server-side entitlements. Premium currently means Resume Studio only — no payment collection."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.db.session import Base

ENTITLEMENT_FEATURES = ("resume_studio", "project_intelligence")
ENTITLEMENT_SOURCES = ("dev_grant", "admin_grant", "billing")  # billing reserved; unused until approved


class Entitlement(Base):
    __tablename__ = "entitlements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    feature = Column(String(40), nullable=False)
    source = Column(String(40), nullable=False, default="dev_grant", server_default="dev_grant")
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "feature", name="uq_entitlement_user_feature"),
        Index("ix_entitlements_feature", "feature"),
    )


class GeneratedResume(Base):
    __tablename__ = "generated_resumes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(160), nullable=False)
    template_key = Column(String(40), nullable=False, default="classic_ats", server_default="classic_ats")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_generated_resumes_user", "user_id"),)


class GeneratedResumeVersion(Base):
    __tablename__ = "generated_resume_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resume_id = Column(UUID(as_uuid=True), ForeignKey("generated_resumes.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    content = Column(JSONB, nullable=False, default=dict)
    job_description_excerpt = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("resume_id", "version", name="uq_generated_resume_version"),
    )


class ResumeExport(Base):
    __tablename__ = "resume_exports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    resume_version_id = Column(UUID(as_uuid=True), ForeignKey("generated_resume_versions.id", ondelete="CASCADE"), nullable=False)
    format = Column(String(10), nullable=False)  # pdf | docx
    storage_key = Column(String(500), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_resume_exports_user", "user_id"),)


class ConsentRecord(Base):
    __tablename__ = "consents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose = Column(String(80), nullable=False)
    scope = Column(JSONB, nullable=False, default=dict)
    granted_at = Column(DateTime(timezone=True), server_default=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_consents_user_purpose", "user_id", "purpose"),)


class CandidateEvaluation(Base):
    """Ontario-oriented evaluation record. Not a hiring decision."""
    __tablename__ = "candidate_evaluations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recruiter_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose = Column(String(40), nullable=False)  # general_discovery | opportunity_evaluation
    acknowledgement = Column(Boolean, nullable=False, default=False)
    role_title = Column(String(160), nullable=True)
    requirements = Column(JSONB, nullable=False, default=dict)
    authorized_evidence = Column(JSONB, nullable=False, default=list)
    result_summary = Column(JSONB, nullable=False, default=dict)
    matching_engine_version = Column(String(40), nullable=False, default="jd_match_v1", server_default="jd_match_v1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_candidate_evaluations_recruiter", "recruiter_id", "created_at"),
        Index("ix_candidate_evaluations_candidate", "candidate_id"),
    )


class CareerAuditLog(Base):
    __tablename__ = "career_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    subject_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(80), nullable=False)
    resource_type = Column(String(40), nullable=False)
    resource_id = Column(String(80), nullable=True)
    detail = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_career_audit_subject", "subject_user_id", "created_at"),)
