"""Per-account start page, challenge notices, and one GitHub account per connection

Revision ID: 024_session_preferences
Revises: 023_code_workspace
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "024_session_preferences"
down_revision: Union[str, None] = "023_code_workspace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table(table):
        return set()
    return {col["name"] for col in inspect(bind).get_columns(table)}


def _indexes(table: str) -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table(table):
        return set()
    return {idx["name"] for idx in inspect(bind).get_indexes(table)}


def upgrade() -> None:
    profile = _columns("profiles")
    if profile and "notify_challenges" not in profile:
        op.add_column("profiles", sa.Column("notify_challenges", sa.Boolean(), nullable=False, server_default=sa.false()))
    if profile and "start_page" not in profile:
        op.add_column("profiles", sa.Column("start_page", sa.String(length=16), nullable=False, server_default="home"))
    if "github_connections" in set(inspect(op.get_bind()).get_table_names()) and "uq_github_connections_github_user" not in _indexes("github_connections"):
        # Keep the oldest link when the same GitHub account was saved on two DevZ-Go users.
        op.execute(
            """
            DELETE FROM github_connections
            WHERE id IN (
                SELECT id FROM (
                    SELECT id, row_number() OVER (
                        PARTITION BY github_user_id ORDER BY created_at ASC, id ASC
                    ) AS n
                    FROM github_connections
                    WHERE github_user_id IS NOT NULL
                ) ranked
                WHERE n > 1
            )
            """
        )
        op.create_index("uq_github_connections_github_user", "github_connections", ["github_user_id"], unique=True)


def downgrade() -> None:
    if "uq_github_connections_github_user" in _indexes("github_connections"):
        op.drop_index("uq_github_connections_github_user", table_name="github_connections")
    profile = _columns("profiles")
    if "start_page" in profile:
        op.drop_column("profiles", "start_page")
    if "notify_challenges" in profile:
        op.drop_column("profiles", "notify_challenges")
