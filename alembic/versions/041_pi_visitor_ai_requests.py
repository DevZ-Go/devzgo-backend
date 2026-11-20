"""Track community/visitor AI summary requests (max N projects per user).

Revision ID: 041_pi_visitor_ai_requests
Revises: 040_project_ai_publications
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "041_pi_visitor_ai_requests"
down_revision: Union[str, None] = "040_project_ai_publications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return name in inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "pi_visitor_ai_requests"):
        op.create_table(
            "pi_visitor_ai_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("generation_record_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pi_generation_records.id", ondelete="SET NULL"), nullable=True),
            sa.Column("was_cache_hit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.UniqueConstraint("user_id", "project_id", name="uq_pi_visitor_user_project"),
        )
        op.create_index("ix_pi_visitor_user", "pi_visitor_ai_requests", ["user_id", "created_at"])
        op.create_index("ix_pi_visitor_project", "pi_visitor_ai_requests", ["project_id"])

    op.execute(
        sa.text(
            """
            UPDATE platform_settings
            SET value = value || '{
              "visitor_ai_enabled": true,
              "visitor_ai_project_limit": 5
            }'::jsonb
            WHERE key = 'project_intelligence'
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "pi_visitor_ai_requests"):
        op.drop_table("pi_visitor_ai_requests")
