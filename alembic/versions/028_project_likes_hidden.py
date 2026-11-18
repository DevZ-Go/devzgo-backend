"""Per-project hide like counts

Revision ID: 028_project_likes_hidden
Revises: 027_account_pause
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "028_project_likes_hidden"
down_revision: Union[str, None] = "027_account_pause"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table(table):
        return set()
    return {col["name"] for col in inspect(bind).get_columns(table)}


def upgrade() -> None:
    if "likes_hidden" not in _columns("projects"):
        op.add_column(
            "projects",
            sa.Column("likes_hidden", sa.Boolean(), server_default=sa.false(), nullable=False),
        )


def downgrade() -> None:
    if "likes_hidden" in _columns("projects"):
        op.drop_column("projects", "likes_hidden")
