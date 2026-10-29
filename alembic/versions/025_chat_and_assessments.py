"""Chat photos, videos, emoji reactions, and assessment choices

Revision ID: 025_chat_and_assessments
Revises: 024_session_preferences
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID

revision: str = "025_chat_and_assessments"
down_revision: Union[str, None] = "024_session_preferences"
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
    messages = _columns("messages")
    if messages and "media_key" not in messages:
        op.add_column("messages", sa.Column("media_key", sa.String(), nullable=True))
        op.add_column("messages", sa.Column("media_kind", sa.String(length=16), nullable=True))
    rooms = _columns("room_messages")
    if rooms and "media_key" not in rooms:
        op.add_column("room_messages", sa.Column("media_key", sa.String(), nullable=True))
        op.add_column("room_messages", sa.Column("media_kind", sa.String(length=16), nullable=True))
    if "message_reactions" not in _tables():
        op.create_table(
            "message_reactions",
            sa.Column("message_id", UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("emoji", sa.String(16), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if "room_message_reactions" not in _tables():
        op.create_table(
            "room_message_reactions",
            sa.Column("message_id", UUID(as_uuid=True), sa.ForeignKey("room_messages.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("emoji", sa.String(16), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    profile = _columns("profiles")
    if profile and "challenge_notices" not in profile:
        op.add_column("profiles", sa.Column("challenge_notices", sa.String(length=20), nullable=False, server_default="off"))
        op.execute("UPDATE profiles SET challenge_notices = 'always' WHERE notify_challenges IS TRUE")
    if profile and "challenge_invites" not in profile:
        op.add_column("profiles", sa.Column("challenge_invites", sa.String(length=20), nullable=False, server_default="always"))


def downgrade() -> None:
    tables = _tables()
    if "room_message_reactions" in tables:
        op.drop_table("room_message_reactions")
    if "message_reactions" in tables:
        op.drop_table("message_reactions")
    rooms = _columns("room_messages")
    if "media_kind" in rooms:
        op.drop_column("room_messages", "media_kind")
    if "media_key" in rooms:
        op.drop_column("room_messages", "media_key")
    messages = _columns("messages")
    if "media_kind" in messages:
        op.drop_column("messages", "media_kind")
    if "media_key" in messages:
        op.drop_column("messages", "media_key")
    profile = _columns("profiles")
    if "challenge_invites" in profile:
        op.drop_column("profiles", "challenge_invites")
    if "challenge_notices" in profile:
        op.drop_column("profiles", "challenge_notices")
