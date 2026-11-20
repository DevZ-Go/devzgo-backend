"""Project Intelligence: snapshots, jobs, claims, insights, AI drafts, usage, prefs.

Revision ID: 037_project_intelligence
Revises: 036_talent_intelligence
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "037_project_intelligence"
down_revision: Union[str, None] = "036_talent_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return name in inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "project_analysis_preferences"):
        op.create_table(
            "project_analysis_preferences",
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("auto_analyze_default", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )

    if not _has_table(bind, "project_intelligence_settings"):
        op.create_table(
            "project_intelligence_settings",
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("intelligence_enabled", sa.Boolean(), nullable=True),
            sa.Column("external_ai_allowed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("insight_audience", sa.String(20), nullable=False, server_default="public"),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )

    if not _has_table(bind, "project_source_snapshots"):
        op.create_table(
            "project_source_snapshots",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_kind", sa.String(16), nullable=False),
            sa.Column("revision", sa.String(80), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("byte_total", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.UniqueConstraint("project_id", "revision", "content_hash", name="uq_project_snapshot_rev_hash"),
        )
        op.create_index("ix_project_source_snapshots_project", "project_source_snapshots", ["project_id", "created_at"])

    if not _has_table(bind, "project_analysis_jobs"):
        op.create_table(
            "project_analysis_jobs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_source_snapshots.id", ondelete="SET NULL"), nullable=True),
            sa.Column("requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
            sa.Column("job_kind", sa.String(20), nullable=False, server_default="analyze"),
            sa.Column("idempotency_key", sa.String(200), nullable=False),
            sa.Column("analyzer_version", sa.String(40), nullable=False),
            sa.Column("policy_hash", sa.String(64), nullable=False),
            sa.Column("progress", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("coverage_pct", sa.Float(), nullable=True),
            sa.Column("lease_owner", sa.String(80), nullable=True),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("idempotency_key", name="uq_project_analysis_jobs_idem"),
        )
        op.create_index("ix_project_analysis_jobs_project", "project_analysis_jobs", ["project_id", "created_at"])
        op.create_index("ix_project_analysis_jobs_claim", "project_analysis_jobs", ["status", "lease_until"])

    if not _has_table(bind, "project_evidence_claims"):
        op.create_table(
            "project_evidence_claims",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_source_snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_analysis_jobs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("claim_type", sa.String(60), nullable=False),
            sa.Column("wording", sa.Text(), nullable=False),
            sa.Column("support_class", sa.String(40), nullable=False),
            sa.Column("source_file", sa.String(500), nullable=True),
            sa.Column("source_location", sa.String(120), nullable=True),
            sa.Column("source_revision", sa.String(80), nullable=False),
            sa.Column("analyzer_version", sa.String(40), nullable=False),
            sa.Column("coverage_note", sa.String(200), nullable=True),
            sa.Column("visibility", sa.String(20), nullable=False, server_default="team"),
            sa.Column("attribution_status", sa.String(20), nullable=False, server_default="unattributed"),
            sa.Column("extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_project_evidence_claims_project", "project_evidence_claims", ["project_id", "snapshot_id"])
        op.create_index("ix_project_evidence_claims_type", "project_evidence_claims", ["claim_type"])

    if not _has_table(bind, "project_evidence_refs"):
        op.create_table(
            "project_evidence_refs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("claim_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_evidence_claims.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_file", sa.String(500), nullable=False),
            sa.Column("line_start", sa.Integer(), nullable=True),
            sa.Column("line_end", sa.Integer(), nullable=True),
            sa.Column("snippet_hash", sa.String(64), nullable=True),
            sa.Column("label", sa.String(200), nullable=True),
        )
        op.create_index("ix_project_evidence_refs_claim", "project_evidence_refs", ["claim_id"])

    if not _has_table(bind, "project_plain_insights"):
        op.create_table(
            "project_plain_insights",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_source_snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_analysis_jobs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("audience", sa.String(20), nullable=False, server_default="public"),
            sa.Column("everyday", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("technical", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("uncertainties", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("claim_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("composer_version", sa.String(40), nullable=False, server_default="rule_v1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_project_plain_insights_project", "project_plain_insights", ["project_id", "created_at"])

    if not _has_table(bind, "project_ai_description_drafts"):
        op.create_table(
            "project_ai_description_drafts",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_source_snapshots.id", ondelete="SET NULL"), nullable=True),
            sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_analysis_jobs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("parent_draft_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_ai_description_drafts.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
            sa.Column("model", sa.String(80), nullable=True),
            sa.Column("short_description", sa.Text(), nullable=True),
            sa.Column("full_overview", sa.Text(), nullable=True),
            sa.Column("key_features", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("problem_solved", sa.Text(), nullable=True),
            sa.Column("potential_audience", sa.Text(), nullable=True),
            sa.Column("how_it_works_simple", sa.Text(), nullable=True),
            sa.Column("technical_explanation", sa.Text(), nullable=True),
            sa.Column("resume_bullets", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("evidence_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_project_ai_drafts_project", "project_ai_description_drafts", ["project_id", "created_at"])

    if not _has_table(bind, "project_ai_usage_ledger"):
        op.create_table(
            "project_ai_usage_ledger",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
            sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_analysis_jobs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("provider", sa.String(40), nullable=False),
            sa.Column("model", sa.String(80), nullable=False),
            sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_project_ai_usage_user_created", "project_ai_usage_ledger", ["user_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    for name in (
        "project_ai_usage_ledger",
        "project_ai_description_drafts",
        "project_plain_insights",
        "project_evidence_refs",
        "project_evidence_claims",
        "project_analysis_jobs",
        "project_source_snapshots",
        "project_intelligence_settings",
        "project_analysis_preferences",
    ):
        if _has_table(bind, name):
            op.drop_table(name)
