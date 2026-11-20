"""Quiet notes of pages opened and gallery direction

Revision ID: 034_usage_events
Revises: 033_owner_email
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "034_usage_events"
down_revision: Union[str, None] = "033_owner_email"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if inspect(op.get_bind()).has_table("usage_events"):
        return
    op.create_table(
        "usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_usage_events_name_created", "usage_events", ["name", "created_at"])


def downgrade() -> None:
    if inspect(op.get_bind()).has_table("usage_events"):
        op.drop_table("usage_events")
