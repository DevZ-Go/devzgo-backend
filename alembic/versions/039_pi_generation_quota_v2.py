"""PI 2.0: generation records, monthly usage ledger, richer settings seed.

Revision ID: 039_pi_generation_quota_v2
Revises: 038_platform_settings_pi_quota
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "039_pi_generation_quota_v2"
down_revision: Union[str, None] = "038_platform_settings_pi_quota"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return name in inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "pi_monthly_usage"):
        op.create_table(
            "pi_monthly_usage",
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("year_month", sa.String(7), primary_key=True),  # YYYY-MM UTC
            sa.Column("ai_projects_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("regenerations_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("requests_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )

    if not _has_table(bind, "pi_generation_records"):
        op.create_table(
            "pi_generation_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_source_snapshots.id", ondelete="SET NULL"), nullable=True),
            sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_analysis_jobs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("draft_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_ai_description_drafts.id", ondelete="SET NULL"), nullable=True),
            sa.Column("idempotency_key", sa.String(220), nullable=False),
            sa.Column("cache_key", sa.String(220), nullable=False),
            sa.Column("kind", sa.String(20), nullable=False, server_default="initial"),  # initial | regeneration
            sa.Column("status", sa.String(20), nullable=False, server_default="reserved"),
            # reserved | succeeded | failed | released | cache_hit
            sa.Column("output_selection", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("model", sa.String(80), nullable=True),
            sa.Column("prompt_version", sa.String(40), nullable=False, server_default="pi_prompt_v2"),
            sa.Column("analyzer_version", sa.String(40), nullable=True),
            sa.Column("year_month", sa.String(7), nullable=False),
            sa.Column("counts_as_project", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_cached", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reserved_cost_usd", sa.Float(), nullable=True),
            sa.Column("actual_cost_usd", sa.Float(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("idempotency_key", name="uq_pi_generation_idem"),
        )
        op.create_index("ix_pi_generation_user_month", "pi_generation_records", ["user_id", "year_month"])
        op.create_index("ix_pi_generation_cache", "pi_generation_records", ["cache_key", "status"])
        op.create_index("ix_pi_generation_project", "pi_generation_records", ["project_id", "created_at"])

    # Merge v2 defaults into platform_settings without wiping admin edits
    op.execute(
        sa.text(
            """
            INSERT INTO platform_settings (key, value)
            VALUES (
              'project_intelligence',
              '{
                "free_project_limit": 10,
                "premium_project_limit": 30,
                "free_regenerations_per_project": 1,
                "premium_regenerations_per_project": 3,
                "free_concurrent": 1,
                "premium_concurrent": 2,
                "free_max_input_tokens": 6000,
                "free_max_output_tokens": 900,
                "premium_max_input_tokens": 12000,
                "premium_max_output_tokens": 1800,
                "per_request_cost_ceiling_usd": 0.15,
                "user_daily_cost_ceiling_usd": 1.0,
                "user_monthly_cost_ceiling_usd": 5.0,
                "global_daily_budget_usd": 50.0,
                "global_monthly_budget_usd": 500.0,
                "max_concurrent_paid_calls": 8,
                "ai_generate_enabled": true,
                "visitor_insights_enabled": true,
                "auto_analyze_on_upload": true,
                "approved_models": ["gpt-5.4-mini", "gpt-5-mini"],
                "default_model": "gpt-5.4-mini",
                "premium_model": "gpt-5.4-mini"
              }'::jsonb
            )
            ON CONFLICT (key) DO UPDATE SET value = platform_settings.value || EXCLUDED.value
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "pi_generation_records"):
        op.drop_table("pi_generation_records")
    if _has_table(bind, "pi_monthly_usage"):
        op.drop_table("pi_monthly_usage")
