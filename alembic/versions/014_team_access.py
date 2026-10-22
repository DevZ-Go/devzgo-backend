"""team access levels and what each person is working on

Revision ID: 014_team_access
Revises: 013_recruiter
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "014_team_access"
down_revision: Union[str, None] = "013_recruiter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEVEL_FROM_FLAGS = (
    "CASE WHEN can_view_secrets THEN 'manager' "
    "WHEN can_edit_details OR can_manage_files THEN 'editor' ELSE 'commenter' END"
)


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table(table):
        return set()
    return {col["name"] for col in inspect(bind).get_columns(table)}


def upgrade() -> None:
    for table in ("project_collaborators", "project_invitations"):
        columns = _columns(table)
        if columns and "access" not in columns:
            op.add_column(table, sa.Column("access", sa.String(length=16), nullable=False, server_default="commenter"))
            op.execute(f"UPDATE {table} SET access = {LEVEL_FROM_FLAGS}")
            op.execute(
                f"UPDATE {table} SET can_edit_details = access IN ('editor', 'manager'), "
                "can_manage_files = access IN ('editor', 'manager'), can_view_secrets = access = 'manager'"
            )
    members = _columns("project_collaborators")
    if members and "focus" not in members:
        op.add_column("project_collaborators", sa.Column("focus", sa.String(length=200), nullable=True))
    projects = _columns("projects")
    if projects and "owner_focus" not in projects:
        op.add_column("projects", sa.Column("owner_focus", sa.String(length=200), nullable=True))


def downgrade() -> None:
    if "owner_focus" in _columns("projects"):
        op.drop_column("projects", "owner_focus")
    if "focus" in _columns("project_collaborators"):
        op.drop_column("project_collaborators", "focus")
    for table in ("project_invitations", "project_collaborators"):
        if "access" in _columns(table):
            op.drop_column(table, "access")
