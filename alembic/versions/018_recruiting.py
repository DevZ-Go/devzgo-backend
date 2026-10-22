"""recruiter shortlists and contact requests

Revision ID: 018_recruiting
Revises: 017_recruiter_accounts
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "018_recruiting"
down_revision: Union[str, None] = "017_recruiter_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(table: str) -> bool:
    return inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    if not _has("shortlists"):
        op.create_table(
            "shortlists",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("description", sa.String(length=280), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        )
        op.create_index("ix_shortlists_owner", "shortlists", ["owner_id"])
    if not _has("shortlist_entries"):
        op.create_table(
            "shortlist_entries",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("shortlist_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shortlists.id", ondelete="CASCADE"), nullable=False),
            sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.UniqueConstraint("shortlist_id", "candidate_id", name="uq_shortlist_entries_candidate"),
        )
        op.create_index("ix_shortlist_entries_candidate", "shortlist_entries", ["candidate_id"])
    if not _has("recruitment_requests"):
        op.create_table(
            "recruitment_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("recruiter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role_title", sa.String(length=120), nullable=False),
            sa.Column("work_type", sa.String(length=20), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_recruitment_requests_candidate_status", "recruitment_requests", ["candidate_id", "status"])
        op.create_index("ix_recruitment_requests_recruiter_created", "recruitment_requests", ["recruiter_id", "created_at"])


def downgrade() -> None:
    for table in ("recruitment_requests", "shortlist_entries", "shortlists"):
        if _has(table):
            op.drop_table(table)
