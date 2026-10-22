"""group chat rooms for project teams and custom groups

Revision ID: 009_chat_rooms
Revises: 008_monthly_wraps
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "009_chat_rooms"
down_revision: Union[str, None] = "008_monthly_wraps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_rooms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id"),
    )
    op.create_table(
        "chat_members",
        sa.Column("room_id", UUID(as_uuid=True), sa.ForeignKey("chat_rooms.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "room_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", UUID(as_uuid=True), sa.ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_room_messages_room_id", "room_messages", ["room_id"])
    op.create_index("ix_room_messages_created_at", "room_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_room_messages_created_at", table_name="room_messages")
    op.drop_index("ix_room_messages_room_id", table_name="room_messages")
    op.drop_table("room_messages")
    op.drop_table("chat_members")
    op.drop_table("chat_rooms")
