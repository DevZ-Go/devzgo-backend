"""Hide a message for one person

Revision ID: 030_chat_message_hides
Revises: 029_chat_unsend_view_once
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "030_chat_message_hides"
down_revision: Union[str, None] = "029_chat_unsend_view_once"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _hide(name: str, message_table: str) -> None:
    if inspect(op.get_bind()).has_table(name):
        return
    op.create_table(
        name,
        sa.Column("message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{message_table}.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    _hide("message_hides", "messages")
    _hide("room_message_hides", "room_messages")


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("room_message_hides", "message_hides"):
        if inspect(bind).has_table(name):
            op.drop_table(name)
