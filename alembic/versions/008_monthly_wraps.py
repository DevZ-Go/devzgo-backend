"""each user's timezone and stored Monthly Wrap snapshots

Revision ID: 008_monthly_wraps
Revises: 007_collaboration
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "008_monthly_wraps"
down_revision: Union[str, None] = "007_collaboration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("timezone", sa.String(64), nullable=False, server_default="America/Toronto"))
    op.create_table(
        "monthly_wraps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("metrics", JSONB(), nullable=False),
        sa.Column("persona", JSONB(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("shared_activity_id", UUID(as_uuid=True),
                  sa.ForeignKey("network_activities.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("user_id", "month", name="uq_monthly_wraps_user_month"),
    )


def downgrade() -> None:
    op.drop_table("monthly_wraps")
    op.drop_column("profiles", "timezone")
