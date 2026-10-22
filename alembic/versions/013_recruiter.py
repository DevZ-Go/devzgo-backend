"""recruiter company, guide flag, and GitHub activity captured at import

Revision ID: 013_recruiter
Revises: 012_preferences
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "013_recruiter"
down_revision: Union[str, None] = "012_preferences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table(table):
        return set()
    return {col["name"] for col in inspect(bind).get_columns(table)}


def upgrade() -> None:
    profiles = _columns("profiles")
    if profiles:
        if "company" not in profiles:
            op.add_column("profiles", sa.Column("company", sa.String(length=120), nullable=True))
        if "guide_seen" not in profiles:
            op.add_column("profiles", sa.Column("guide_seen", sa.Boolean(), nullable=False, server_default=sa.false()))
    projects = _columns("projects")
    if projects:
        if "github_commit_count" not in projects:
            op.add_column("projects", sa.Column("github_commit_count", sa.Integer(), nullable=True))
        if "github_push_count" not in projects:
            op.add_column("projects", sa.Column("github_push_count", sa.Integer(), nullable=True))
        if "github_pushed_at" not in projects:
            op.add_column("projects", sa.Column("github_pushed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    profiles = _columns("profiles")
    for name in ("guide_seen", "company"):
        if name in profiles:
            op.drop_column("profiles", name)
    projects = _columns("projects")
    for name in ("github_pushed_at", "github_push_count", "github_commit_count"):
        if name in projects:
            op.drop_column("projects", name)
