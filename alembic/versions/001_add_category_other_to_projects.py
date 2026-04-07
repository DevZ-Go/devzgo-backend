"""add category_other to projects

Revision ID: 001_cat_other
Revises:
Create Date: 2026-04-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "001_cat_other"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("category_other", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "category_other")
