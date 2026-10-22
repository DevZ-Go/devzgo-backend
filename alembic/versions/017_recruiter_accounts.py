"""recruiter account type, versioned tour state, and opt-in availability

Revision ID: 017_recruiter_accounts
Revises: 016_chat_room_photo
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "017_recruiter_accounts"
down_revision: Union[str, None] = "016_chat_room_photo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROFILE_COLUMNS = (
    ("tour_version", lambda: sa.Column("tour_version", sa.Integer(), nullable=True)),
    ("tour_state", lambda: sa.Column("tour_state", sa.String(length=20), nullable=True)),
    ("open_to_work", lambda: sa.Column("open_to_work", sa.Boolean(), nullable=False, server_default=sa.false())),
    ("work_types", lambda: sa.Column("work_types", sa.String(length=120), nullable=True)),
    ("work_arrangements", lambda: sa.Column("work_arrangements", sa.String(length=60), nullable=True)),
    ("availability_note", lambda: sa.Column("availability_note", sa.String(length=160), nullable=True)),
)


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table(table):
        return set()
    return {col["name"] for col in inspect(bind).get_columns(table)}


def upgrade() -> None:
    users = _columns("users")
    if users and "account_type" not in users:
        op.add_column("users", sa.Column("account_type", sa.String(length=20), nullable=False, server_default="developer"))
        if _columns("profiles"):
            # Existing recruiters chose that role at sign-up; carry it over to the account itself.
            op.execute(
                "UPDATE users SET account_type = 'recruiter' "
                "WHERE id IN (SELECT user_id FROM profiles WHERE role = 'Recruiter')"
            )
    profiles = _columns("profiles")
    if profiles:
        for name, column in PROFILE_COLUMNS:
            if name not in profiles:
                op.add_column("profiles", column())


def downgrade() -> None:
    profiles = _columns("profiles")
    for name, _ in reversed(PROFILE_COLUMNS):
        if name in profiles:
            op.drop_column("profiles", name)
    if "account_type" in _columns("users"):
        op.drop_column("users", "account_type")
