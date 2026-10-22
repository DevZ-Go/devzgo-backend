"""GitHub editing connection, drafts, audit events and sync jobs

Revision ID: 023_code_workspace
Revises: 022_file_metadata
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "023_code_workspace"
down_revision: Union[str, None] = "022_file_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _project_columns() -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns("projects")}


def upgrade() -> None:
    if "github_tracked_branch" not in _project_columns():
        op.add_column("projects", sa.Column("github_tracked_branch", sa.String(length=255), nullable=True))
    tables = _tables()
    if "github_connections" not in tables:
        op.create_table(
            "github_connections",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("github_user_id", sa.BigInteger(), nullable=True),
            sa.Column("login", sa.String(length=100), nullable=True),
            sa.Column("access_token_enc", sa.Text(), nullable=True),
            sa.Column("refresh_token_enc", sa.Text(), nullable=True),
            sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("last_error", sa.String(length=300), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if "workspace_audit_events" not in tables:
        op.create_table(
            "workspace_audit_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("outcome", sa.String(length=16), nullable=False),
            sa.Column("repo_full_name", sa.String(length=200), nullable=True),
            sa.Column("branch", sa.String(length=255), nullable=True),
            sa.Column("path", sa.String(length=500), nullable=True),
            sa.Column("commit_sha", sa.String(length=40), nullable=True),
            sa.Column("url", sa.String(length=500), nullable=True),
            sa.Column("error_code", sa.String(length=40), nullable=True),
            sa.Column("idempotency_key", sa.String(length=80), nullable=True, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if "workspace_drafts" not in tables:
        op.create_table(
            "workspace_drafts",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("repo_id", sa.BigInteger(), nullable=True),
            sa.Column("branch", sa.String(length=255), nullable=False),
            sa.Column("path", sa.String(length=500), nullable=False),
            sa.Column("base_blob_sha", sa.String(length=64), nullable=True),
            sa.Column("base_commit_sha", sa.String(length=40), nullable=True),
            sa.Column("content_enc", sa.Text(), nullable=False),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column("line_ending", sa.String(length=8), nullable=False, server_default="lf"),
            sa.Column("has_bom", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "project_id", "branch", "path", name="uq_workspace_draft"),
        )
    if "workspace_sync_jobs" not in tables:
        op.create_table(
            "workspace_sync_jobs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("audit_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspace_audit_events.id", ondelete="SET NULL"), nullable=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("trigger", sa.String(length=32), nullable=False),
            sa.Column("branch", sa.String(length=255), nullable=True),
            sa.Column("commit_sha", sa.String(length=40), nullable=True),
            sa.Column("path", sa.String(length=500), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("steps", postgresql.JSONB(), nullable=True),
            sa.Column("error", sa.String(length=300), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    tables = _tables()
    for name in ("workspace_sync_jobs", "workspace_drafts", "workspace_audit_events", "github_connections"):
        if name in tables:
            op.drop_table(name)
    if "github_tracked_branch" in _project_columns():
        op.drop_column("projects", "github_tracked_branch")
