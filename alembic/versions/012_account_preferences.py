"""account privileges, resume, and notification choices

Revision ID: 012_preferences
Revises: 011_privacy
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "012_preferences"
down_revision: Union[str, None] = "011_privacy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing() -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table("profiles"):
        return set()
    return {col["name"] for col in inspect(bind).get_columns("profiles")}


def upgrade() -> None:
    existing = _existing()
    if not existing:
        return
    text_cols = (
        ("connection_visibility", "public"),
        ("collaboration_policy", "anyone"),
        ("email_visibility", "private"),
        ("resume_visibility", "connections"),
    )
    for name, default in text_cols:
        if name not in existing:
            op.add_column("profiles", sa.Column(name, sa.String(length=20), nullable=False, server_default=default))
    if "resume_url" not in existing:
        op.add_column("profiles", sa.Column("resume_url", sa.String(), nullable=True))
    for name in (
        "notify_likes",
        "notify_comments",
        "notify_shares",
        "notify_connections",
        "notify_messages",
        "notify_collaborations",
    ):
        if name not in existing:
            op.add_column("profiles", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    existing = _existing()
    for name in (
        "notify_collaborations",
        "notify_messages",
        "notify_connections",
        "notify_shares",
        "notify_comments",
        "notify_likes",
        "resume_url",
        "resume_visibility",
        "email_visibility",
        "collaboration_policy",
        "connection_visibility",
    ):
        if name in existing:
            op.drop_column("profiles", name)
