"""add language_stats and detected_tech_stack_ids

Revision ID: 002_analysis
Revises: 001_cat_other
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "002_analysis"
down_revision: Union[str, None] = "001_cat_other"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("language_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "detected_tech_stack_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "detected_tech_stack_ids")
    op.drop_column("projects", "language_stats")
