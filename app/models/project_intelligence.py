"""Project Intelligence: snapshots, jobs, evidence claims, plain insights, AI drafts, usage."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

JOB_STATUSES = (
    "queued",
    "analyzing",
    "generating",
    "ready",
    "incomplete",
    "failed",
    "cancelled",
)
SUPPORT_CLASSES = (
    "documented",
    "source_supported",
    "developer_declared",
    "runtime_verified",
)
DRAFT_STATUSES = ("draft", "approved", "discarded")
ATTRIBUTION_STATUSES = ("unattributed", "candidate", "attributed", "rejected")


class ProjectAnalysisPreference(Base):
    """Per-user global defaults for Project Intelligence."""

    __tablename__ = "project_analysis_preferences"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    auto_analyze_default = Column(Boolean, nullable=False, default=True, server_default=true())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProjectIntelligenceSettings(Base):
    """Per-project enablement and external-AI allow flag."""

    __tablename__ = "project_intelligence_settings"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    # None = inherit global; True/False = override
    intelligence_enabled = Column(Boolean, nullable=True)
    external_ai_allowed = Column(Boolean, nullable=False, default=False, server_default=false())
    insight_audience = Column(String(20), nullable=False, default="public", server_default="public")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProjectSourceSnapshot(Base):
    __tablename__ = "project_source_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_kind = Column(String(16), nullable=False)  # zip | github
    revision = Column(String(80), nullable=False)  # commit SHA or content hash
    content_hash = Column(String(64), nullable=False)
    file_count = Column(Integer, nullable=False, default=0, server_default="0")
    byte_total = Column(BigInteger, nullable=False, default=0, server_default="0")
    meta = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_project_source_snapshots_project", "project_id", "created_at"),
        UniqueConstraint("project_id", "revision", "content_hash", name="uq_project_snapshot_rev_hash"),
    )


class ProjectAnalysisJob(Base):
    __tablename__ = "project_analysis_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey("project_source_snapshots.id", ondelete="SET NULL"), nullable=True)
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(20), nullable=False, default="queued", server_default="queued")
    job_kind = Column(String(20), nullable=False, default="analyze", server_default="analyze")  # analyze | generate
    idempotency_key = Column(String(200), nullable=False)
    analyzer_version = Column(String(40), nullable=False)
    policy_hash = Column(String(64), nullable=False)
    progress = Column(JSONB, nullable=False, default=dict)
    error = Column(Text, nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False, server_default=false())
    coverage_pct = Column(Float, nullable=True)
    lease_owner = Column(String(80), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    snapshot = relationship("ProjectSourceSnapshot")

    __table_args__ = (
        Index("ix_project_analysis_jobs_project", "project_id", "created_at"),
        Index("ix_project_analysis_jobs_claim", "status", "lease_until"),
        UniqueConstraint("idempotency_key", name="uq_project_analysis_jobs_idem"),
    )


class ProjectEvidenceClaim(Base):
    __tablename__ = "project_evidence_claims"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey("project_source_snapshots.id", ondelete="CASCADE"), nullable=False)
    job_id = Column(UUID(as_uuid=True), ForeignKey("project_analysis_jobs.id", ondelete="SET NULL"), nullable=True)
    claim_type = Column(String(60), nullable=False)
    wording = Column(Text, nullable=False)
    support_class = Column(String(40), nullable=False)
    source_file = Column(String(500), nullable=True)
    source_location = Column(String(120), nullable=True)  # e.g. L12-L40
    source_revision = Column(String(80), nullable=False)
    analyzer_version = Column(String(40), nullable=False)
    coverage_note = Column(String(200), nullable=True)
    visibility = Column(String(20), nullable=False, default="team", server_default="team")
    attribution_status = Column(String(20), nullable=False, default="unattributed", server_default="unattributed")
    extra = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    refs = relationship("ProjectEvidenceRef", back_populates="claim", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_project_evidence_claims_project", "project_id", "snapshot_id"),
        Index("ix_project_evidence_claims_type", "claim_type"),
    )


class ProjectEvidenceRef(Base):
    __tablename__ = "project_evidence_refs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_id = Column(
        UUID(as_uuid=True), ForeignKey("project_evidence_claims.id", ondelete="CASCADE"), nullable=False
    )
    source_file = Column(String(500), nullable=False)
    line_start = Column(Integer, nullable=True)
    line_end = Column(Integer, nullable=True)
    snippet_hash = Column(String(64), nullable=True)
    label = Column(String(200), nullable=True)

    claim = relationship("ProjectEvidenceClaim", back_populates="refs")

    __table_args__ = (Index("ix_project_evidence_refs_claim", "claim_id"),)


class ProjectPlainInsight(Base):
    __tablename__ = "project_plain_insights"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey("project_source_snapshots.id", ondelete="CASCADE"), nullable=False)
    job_id = Column(UUID(as_uuid=True), ForeignKey("project_analysis_jobs.id", ondelete="SET NULL"), nullable=True)
    audience = Column(String(20), nullable=False, default="public", server_default="public")
    everyday = Column(JSONB, nullable=False, default=dict)
    technical = Column(JSONB, nullable=False, default=dict)
    uncertainties = Column(JSONB, nullable=False, default=list)
    claim_ids = Column(JSONB, nullable=False, default=list)
    composer_version = Column(String(40), nullable=False, default="rule_v1", server_default="rule_v1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_project_plain_insights_project", "project_id", "created_at"),
    )


class ProjectAiDescriptionDraft(Base):
    __tablename__ = "project_ai_description_drafts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey("project_source_snapshots.id", ondelete="SET NULL"), nullable=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("project_analysis_jobs.id", ondelete="SET NULL"), nullable=True)
    parent_draft_id = Column(UUID(as_uuid=True), ForeignKey("project_ai_description_drafts.id", ondelete="SET NULL"), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(20), nullable=False, default="draft", server_default="draft")
    model = Column(String(80), nullable=True)
    short_description = Column(Text, nullable=True)
    full_overview = Column(Text, nullable=True)
    key_features = Column(JSONB, nullable=False, default=list)
    problem_solved = Column(Text, nullable=True)
    potential_audience = Column(Text, nullable=True)
    how_it_works_simple = Column(Text, nullable=True)
    technical_explanation = Column(Text, nullable=True)
    resume_bullets = Column(JSONB, nullable=False, default=list)
    evidence_refs = Column(JSONB, nullable=False, default=list)
    raw_payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    approved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_project_ai_drafts_project", "project_id", "created_at"),
    )


class ProjectAiUsageLedger(Base):
    __tablename__ = "project_ai_usage_ledger"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("project_analysis_jobs.id", ondelete="SET NULL"), nullable=True)
    provider = Column(String(40), nullable=False)
    model = Column(String(80), nullable=False)
    tokens_in = Column(Integer, nullable=False, default=0, server_default="0")
    tokens_out = Column(Integer, nullable=False, default=0, server_default="0")
    estimated_cost_usd = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_project_ai_usage_user_created", "user_id", "created_at"),
    )


class ProjectAiPublication(Base):
    """Approved AI content kept separate from developer-authored project descriptions."""

    __tablename__ = "project_ai_publications"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    source_draft_id = Column(
        UUID(as_uuid=True), ForeignKey("project_ai_description_drafts.id", ondelete="SET NULL"), nullable=True
    )
    ai_short_description = Column(Text, nullable=True)
    ai_detailed_description = Column(Text, nullable=True)
    simple_explanation = Column(JSONB, nullable=False, default=dict)
    key_features = Column(JSONB, nullable=False, default=list)
    technical_explanation = Column(Text, nullable=True)
    resume_bullets = Column(JSONB, nullable=False, default=list)
    publish_simple = Column(Boolean, nullable=False, default=False, server_default=false())
    show_detailed_on_page = Column(Boolean, nullable=False, default=False, server_default=false())
    adopted_short = Column(Boolean, nullable=False, default=False, server_default=false())
    adopted_detailed = Column(Boolean, nullable=False, default=False, server_default=false())
    developer_short_backup = Column(Text, nullable=True)
    developer_full_backup = Column(Text, nullable=True)
    # valid | low_quality | rejected | deterministic | unknown | stale
    quality_status = Column(String(32), nullable=False, default="unknown", server_default="unknown")
    schema_version = Column(String(40), nullable=False, default="legacy", server_default="legacy")
    prompt_version = Column(String(40), nullable=True)
    # ai | deterministic
    explanation_kind = Column(String(32), nullable=False, default="ai", server_default="ai")
    quality_reasons = Column(JSONB, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PiVisitorAiRequest(Base):
    """Signed-in users may request a shared AI summary for viewable projects (capped)."""

    __tablename__ = "pi_visitor_ai_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    generation_record_id = Column(
        UUID(as_uuid=True), ForeignKey("pi_generation_records.id", ondelete="SET NULL"), nullable=True
    )
    was_cache_hit = Column(Boolean, nullable=False, default=False, server_default=false())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_pi_visitor_user_project"),
        Index("ix_pi_visitor_user", "user_id", "created_at"),
        Index("ix_pi_visitor_project", "project_id"),
    )


class PiMonthlyUsage(Base):
    """Calendar-month UTC counters for AI packages (not deterministic analyze)."""

    __tablename__ = "pi_monthly_usage"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    year_month = Column(String(7), primary_key=True)  # YYYY-MM
    ai_projects_used = Column(Integer, nullable=False, default=0, server_default="0")
    regenerations_used = Column(Integer, nullable=False, default=0, server_default="0")
    requests_count = Column(Integer, nullable=False, default=0, server_default="0")
    tokens_in = Column(Integer, nullable=False, default=0, server_default="0")
    tokens_out = Column(Integer, nullable=False, default=0, server_default="0")
    cost_usd = Column(Float, nullable=False, default=0.0, server_default="0")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PiGenerationRecord(Base):
    """Durable reservation + cache record for each AI package request."""

    __tablename__ = "pi_generation_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    snapshot_id = Column(
        UUID(as_uuid=True), ForeignKey("project_source_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    job_id = Column(UUID(as_uuid=True), ForeignKey("project_analysis_jobs.id", ondelete="SET NULL"), nullable=True)
    draft_id = Column(
        UUID(as_uuid=True), ForeignKey("project_ai_description_drafts.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key = Column(String(220), nullable=False)
    cache_key = Column(String(220), nullable=False)
    kind = Column(String(20), nullable=False, default="initial", server_default="initial")
    status = Column(String(20), nullable=False, default="reserved", server_default="reserved")
    output_selection = Column(JSONB, nullable=False, default=dict)
    model = Column(String(80), nullable=True)
    prompt_version = Column(String(40), nullable=False, default="pi_prompt_v2", server_default="pi_prompt_v2")
    analyzer_version = Column(String(40), nullable=True)
    year_month = Column(String(7), nullable=False)
    counts_as_project = Column(Boolean, nullable=False, default=False, server_default=false())
    tokens_in = Column(Integer, nullable=False, default=0, server_default="0")
    tokens_out = Column(Integer, nullable=False, default=0, server_default="0")
    tokens_cached = Column(Integer, nullable=False, default=0, server_default="0")
    reserved_cost_usd = Column(Float, nullable=True)
    actual_cost_usd = Column(Float, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_pi_generation_idem"),
        Index("ix_pi_generation_user_month", "user_id", "year_month"),
        Index("ix_pi_generation_cache", "cache_key", "status"),
        Index("ix_pi_generation_project", "project_id", "created_at"),
    )
