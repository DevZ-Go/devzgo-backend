"""add category_other to projects

Revision ID: 001_cat_other
Revises:
Create Date: 2026-04-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "001_cat_other"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column() -> bool:
    insp = inspect(op.get_bind())
    return insp.has_table("projects") and "category_other" in {c["name"] for c in insp.get_columns("projects")}


def upgrade() -> None:
    # Empty databases get the column from the 003 baseline instead.
    if inspect(op.get_bind()).has_table("projects") and not _has_column():
        op.add_column("projects", sa.Column("category_other", sa.String(length=64), nullable=True))


def downgrade() -> None:
    pass  # never drop data; the baseline owns this column
