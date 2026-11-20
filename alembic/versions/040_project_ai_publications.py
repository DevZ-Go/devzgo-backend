"""Published AI content separate from developer-authored project text.

Revision ID: 040_project_ai_publications
Revises: 039_pi_generation_quota_v2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "040_project_ai_publications"
down_revision: Union[str, None] = "039_pi_generation_quota_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return name in inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    # Raise free/premium output budgets so reasoning models can finish compact JSON.
    op.execute(
        sa.text(
            """
            UPDATE platform_settings
            SET value = value
              || '{"free_max_output_tokens": 2000, "premium_max_output_tokens": 3500}'::jsonb
            WHERE key = 'project_intelligence'
            """
        )
    )
    if not _has_table(bind, "project_ai_publications"):
        op.create_table(
            "project_ai_publications",
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("source_draft_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_ai_description_drafts.id", ondelete="SET NULL"), nullable=True),
            sa.Column("ai_short_description", sa.Text(), nullable=True),
            sa.Column("ai_detailed_description", sa.Text(), nullable=True),
            sa.Column("simple_explanation", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("key_features", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("technical_explanation", sa.Text(), nullable=True),
            sa.Column("resume_bullets", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("publish_simple", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("show_detailed_on_page", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("adopted_short", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("adopted_detailed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("developer_short_backup", sa.Text(), nullable=True),
            sa.Column("developer_full_backup", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "project_ai_publications"):
        op.drop_table("project_ai_publications")
