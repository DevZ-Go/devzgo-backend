"""add language_stats and detected_tech_stack_ids

Revision ID: 002_analysis
Revises: 001_cat_other
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "002_analysis"
down_revision: Union[str, None] = "001_cat_other"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # create_all on a fresh database already adds these columns from the model.
    # Skip them when present so upgrade is safe on both fresh and existing DBs.
    bind = op.get_bind()
    if not inspect(bind).has_table("projects"):
        return  # empty database: the 003 baseline creates everything
    existing = {col["name"] for col in inspect(bind).get_columns("projects")}
    if "language_stats" not in existing:
        op.add_column(
            "projects",
            sa.Column("language_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
    if "detected_tech_stack_ids" not in existing:
        op.add_column(
            "projects",
            sa.Column(
                "detected_tech_stack_ids",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )


def downgrade() -> None:
    pass  # never drop data; the baseline owns these columns
