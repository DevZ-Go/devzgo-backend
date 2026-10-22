"""per-person GitHub contributors, commits, and pushes for imported projects

Revision ID: 015_github_history
Revises: 014_team_access
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "015_github_history"
down_revision: Union[str, None] = "014_team_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(table: str) -> bool:
    return inspect(op.get_bind()).has_table(table)


def _columns(table: str) -> set[str]:
    if not _has(table):
        return set()
    return {col["name"] for col in inspect(op.get_bind()).get_columns(table)}


def _project_fk() -> sa.Column:
    return sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    projects = _columns("projects")
    if projects:
        if "github_owner_id" not in projects:
            op.add_column("projects", sa.Column("github_owner_id", sa.String(length=32), nullable=True))
        if "github_synced_at" not in projects:
            op.add_column("projects", sa.Column("github_synced_at", sa.DateTime(timezone=True), nullable=True))

    if not _has("project_contributors"):
        op.create_table(
            "project_contributors",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            _project_fk(),
            sa.Column("github_id", sa.String(length=32), nullable=True),
            sa.Column("login", sa.String(length=100), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("avatar_url", sa.String(length=500), nullable=True),
            sa.Column("commits", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("additions", sa.Integer(), nullable=True),
            sa.Column("deletions", sa.Integer(), nullable=True),
            sa.Column("first_commit_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_commit_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("weekly", postgresql.JSONB(), nullable=True),
        )
        op.create_index("ix_project_contributors_project_id", "project_contributors", ["project_id"])
        op.create_index("ix_project_contributors_github_id", "project_contributors", ["github_id"])
        op.create_index("ix_project_contributors_email", "project_contributors", ["email"])

    if not _has("project_commits"):
        op.create_table(
            "project_commits",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            _project_fk(),
            sa.Column("sha", sa.String(length=40), nullable=False),
            sa.Column("message", sa.String(length=300), nullable=False, server_default=""),
            sa.Column("author_github_id", sa.String(length=32), nullable=True),
            sa.Column("author_login", sa.String(length=100), nullable=True),
            sa.Column("author_name", sa.String(length=200), nullable=True),
            sa.Column("author_email", sa.String(length=255), nullable=True),
            sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("project_id", "sha", name="uq_project_commits_sha"),
        )
        op.create_index("ix_project_commits_project_time", "project_commits", ["project_id", "committed_at"])

    if not _has("project_pushes"):
        op.create_table(
            "project_pushes",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            _project_fk(),
            sa.Column("event_id", sa.String(length=32), nullable=False),
            sa.Column("actor_github_id", sa.String(length=32), nullable=True),
            sa.Column("actor_login", sa.String(length=100), nullable=True),
            sa.Column("branch", sa.String(length=200), nullable=True),
            sa.Column("commit_count", sa.Integer(), nullable=True),
            sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("project_id", "event_id", name="uq_project_pushes_event"),
        )
        op.create_index("ix_project_pushes_project_time", "project_pushes", ["project_id", "pushed_at"])
        op.create_index("ix_project_pushes_actor_github_id", "project_pushes", ["actor_github_id"])


def downgrade() -> None:
    for table in ("project_pushes", "project_commits", "project_contributors"):
        if _has(table):
            op.drop_table(table)
    projects = _columns("projects")
    for name in ("github_synced_at", "github_owner_id"):
        if name in projects:
            op.drop_column("projects", name)
