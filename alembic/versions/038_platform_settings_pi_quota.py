"""Platform settings for admin-tunable Project Intelligence quotas.

Revision ID: 038_platform_settings_pi_quota
Revises: 037_project_intelligence
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "038_platform_settings_pi_quota"
down_revision: Union[str, None] = "037_project_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return name in inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "platform_settings"):
        op.create_table(
            "platform_settings",
            sa.Column("key", sa.String(80), primary_key=True),
            sa.Column("value", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        )
    # Seed default PI settings if missing (idempotent)
    op.execute(
        sa.text(
            """
            INSERT INTO platform_settings (key, value)
            VALUES (
              'project_intelligence',
              '{"free_project_limit": 10, "visitor_insights_enabled": true, "ai_generate_enabled": true, "auto_analyze_on_upload": true}'::jsonb
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "platform_settings"):
        op.drop_table("platform_settings")
