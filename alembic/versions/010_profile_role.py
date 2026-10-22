"""profile role chosen by the person

Revision ID: 010_profile_role
Revises: 009_chat_rooms
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "010_profile_role"
down_revision: Union[str, None] = "009_chat_rooms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {col["name"] for col in inspect(bind).get_columns("profiles")}
    if "role" not in existing:
        op.add_column("profiles", sa.Column("role", sa.String(length=40), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {col["name"] for col in inspect(bind).get_columns("profiles")}
    if "role" in existing:
        op.drop_column("profiles", "role")
