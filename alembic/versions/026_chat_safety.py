"""Block, restrict, mute, group admins, and file names for chat attachments

Revision ID: 026_chat_safety
Revises: 025_chat_and_assessments
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID

revision: str = "026_chat_safety"
down_revision: Union[str, None] = "025_chat_and_assessments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table(table):
        return set()
    return {col["name"] for col in inspect(bind).get_columns(table)}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "user_blocks" not in tables:
        op.create_table(
            "user_blocks",
            sa.Column("blocker_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("blocked_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("kind", sa.String(16), nullable=False, server_default="block"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_user_blocks_blocked", "user_blocks", ["blocked_id"])
    if "chat_mutes" not in tables:
        op.create_table(
            "chat_mutes",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("muted_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("room_id", UUID(as_uuid=True), sa.ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("uq_chat_mutes_user_person", "chat_mutes", ["user_id", "muted_user_id"], unique=True)
        op.create_index("uq_chat_mutes_user_room", "chat_mutes", ["user_id", "room_id"], unique=True)
    members = _columns("chat_members")
    if members and "is_admin" not in members:
        op.add_column("chat_members", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    for table in ("messages", "room_messages"):
        cols = _columns(table)
        if cols and "media_name" not in cols:
            op.add_column(table, sa.Column("media_name", sa.String(length=160), nullable=True))


def downgrade() -> None:
    for table in ("room_messages", "messages"):
        if "media_name" in _columns(table):
            op.drop_column(table, "media_name")
    if "is_admin" in _columns("chat_members"):
        op.drop_column("chat_members", "is_admin")
    tables = _tables()
    if "chat_mutes" in tables:
        op.drop_table("chat_mutes")
    if "user_blocks" in tables:
        op.drop_table("user_blocks")
