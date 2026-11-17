"""Account pause and scheduled deletion

Revision ID: 027_account_pause
Revises: 026_chat_safety
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "027_account_pause"
down_revision: Union[str, None] = "026_chat_safety"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table(table):
        return set()
    return {col["name"] for col in inspect(bind).get_columns(table)}


def upgrade() -> None:
    cols = _columns("users")
    if "deactivated_at" not in cols:
        op.add_column("users", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))
    if "delete_after" not in cols:
        op.add_column("users", sa.Column("delete_after", sa.DateTime(timezone=True), nullable=True))
        op.create_index("ix_users_delete_after", "users", ["delete_after"])


def downgrade() -> None:
    cols = _columns("users")
    if "delete_after" in cols:
        op.drop_index("ix_users_delete_after", table_name="users")
        op.drop_column("users", "delete_after")
    if "deactivated_at" in cols:
        op.drop_column("users", "deactivated_at")
