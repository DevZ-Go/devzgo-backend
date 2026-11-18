"""Unsend and view-once messages

Revision ID: 029_chat_unsend_view_once
Revises: 028_project_likes_hidden
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "029_chat_unsend_view_once"
down_revision: Union[str, None] = "028_project_likes_hidden"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table(table):
        return set()
    return {col["name"] for col in inspect(bind).get_columns(table)}


def _add_flags(table: str) -> None:
    cols = _columns(table)
    if "view_once" not in cols:
        op.add_column(table, sa.Column("view_once", sa.Boolean(), server_default=sa.false(), nullable=False))
    if "opened_at" not in cols:
        op.add_column(table, sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True))
    if "unsent_at" not in cols:
        op.add_column(table, sa.Column("unsent_at", sa.DateTime(timezone=True), nullable=True))


def upgrade() -> None:
    _add_flags("messages")
    _add_flags("room_messages")
    if not inspect(op.get_bind()).has_table("room_message_opens"):
        op.create_table(
            "room_message_opens",
            sa.Column("message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("room_messages.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if inspect(op.get_bind()).has_table("room_message_opens"):
        op.drop_table("room_message_opens")
    for table in ("room_messages", "messages"):
        cols = _columns(table)
        for name in ("unsent_at", "opened_at", "view_once"):
            if name in cols:
                op.drop_column(table, name)
