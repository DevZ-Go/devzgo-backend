"""group chat photo

Revision ID: 016_chat_room_photo
Revises: 015_github_history
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "016_chat_room_photo"
down_revision: Union[str, None] = "015_github_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table("chat_rooms"):
        return set()
    return {col["name"] for col in inspect(bind).get_columns("chat_rooms")}


def upgrade() -> None:
    columns = _columns()
    if columns and "avatar_url" not in columns:
        op.add_column("chat_rooms", sa.Column("avatar_url", sa.String(), nullable=True))


def downgrade() -> None:
    if "avatar_url" in _columns():
        op.drop_column("chat_rooms", "avatar_url")
