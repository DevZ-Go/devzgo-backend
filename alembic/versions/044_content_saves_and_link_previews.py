"""Content saves and link preview cache.

Revision ID: 044_content_saves_link_previews
Revises: 043_pi_explanation_quality
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "044_content_saves_link_previews"
down_revision: Union[str, None] = "043_pi_explanation_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind):
    return set(inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    if "content_saves" not in existing:
        op.create_table(
            "content_saves",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("target_type", sa.String(20), nullable=False),
            sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("user_id", "target_type", "target_id", name="uq_content_save_user_target"),
        )
        op.create_index("ix_content_saves_user_created", "content_saves", ["user_id", "created_at"])
        op.create_index("ix_content_saves_target", "content_saves", ["target_type", "target_id"])

    if "link_previews" not in existing:
        op.create_table(
            "link_previews",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("url", sa.String(1000), nullable=False, unique=True),
            sa.Column("normalized_url", sa.String(1000), nullable=False),
            sa.Column("title", sa.String(500), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("image_url", sa.Text(), nullable=True),
            sa.Column("domain", sa.String(255), nullable=True),
            sa.Column("kind", sa.String(32), nullable=False, server_default="external"),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_link_previews_normalized", "link_previews", ["normalized_url"])


def downgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    if "link_previews" in existing:
        op.drop_table("link_previews")
    if "content_saves" in existing:
        op.drop_table("content_saves")
