"""privacy and privilege settings on a profile

Revision ID: 011_privacy
Revises: 010_profile_role
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "011_privacy"
down_revision: Union[str, None] = "010_profile_role"
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
        ("profile_visibility", "public"),
        ("message_policy", "anyone"),
        ("comment_policy", "anyone"),
        ("activity_visibility", "public"),
    )
    for name, default in text_cols:
        if name not in existing:
            op.add_column("profiles", sa.Column(name, sa.String(length=20), nullable=False, server_default=default))
    if "discoverable" not in existing:
        op.add_column("profiles", sa.Column("discoverable", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "allow_connection_requests" not in existing:
        op.add_column(
            "profiles",
            sa.Column("allow_connection_requests", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    existing = _existing()
    for name in (
        "allow_connection_requests",
        "discoverable",
        "activity_visibility",
        "comment_policy",
        "message_policy",
        "profile_visibility",
    ):
        if name in existing:
            op.drop_column("profiles", name)
