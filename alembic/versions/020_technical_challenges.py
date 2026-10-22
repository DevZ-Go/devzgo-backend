"""technical challenges, invitations, submissions and reviews

Revision ID: 020_technical_challenges
Revises: 019_project_evidence
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "020_technical_challenges"
down_revision: Union[str, None] = "019_project_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _has(table: str) -> bool:
    return inspect(op.get_bind()).has_table(table)


def _user(column: str) -> sa.Column:
    return sa.Column(column, UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    if not _has("technical_challenges"):
        op.create_table(
            "technical_challenges",
            sa.Column("id", UUID, primary_key=True),
            _user("recruiter_id"),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("challenge_type", sa.String(length=30), nullable=False),
            sa.Column("technologies", postgresql.JSONB(), nullable=False),
            sa.Column("difficulty", sa.String(length=20), nullable=False),
            sa.Column("estimated_minutes", sa.Integer(), nullable=True),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("instructions", sa.Text(), nullable=False),
            sa.Column("criteria", postgresql.JSONB(), nullable=False),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("visibility", sa.String(length=20), nullable=False, server_default="invitation"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        )
        op.create_index("ix_technical_challenges_recruiter", "technical_challenges", ["recruiter_id"])
        op.create_index("ix_technical_challenges_status_visibility", "technical_challenges", ["status", "visibility"])
    if not _has("challenge_invitations"):
        op.create_table(
            "challenge_invitations",
            sa.Column("id", UUID, primary_key=True),
            sa.Column("challenge_id", UUID, sa.ForeignKey("technical_challenges.id", ondelete="CASCADE"), nullable=False),
            _user("developer_id"),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("challenge_id", "developer_id", name="uq_challenge_invitations_developer"),
        )
        op.create_index("ix_challenge_invitations_developer_status", "challenge_invitations", ["developer_id", "status"])
    if not _has("challenge_submissions"):
        op.create_table(
            "challenge_submissions",
            sa.Column("id", UUID, primary_key=True),
            sa.Column("challenge_id", UUID, sa.ForeignKey("technical_challenges.id", ondelete="CASCADE"), nullable=False),
            _user("developer_id"),
            sa.Column("invitation_id", UUID, sa.ForeignKey("challenge_invitations.id", ondelete="SET NULL"), nullable=True),
            sa.Column("repo_url", sa.String(length=500), nullable=True),
            sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
            sa.Column("explanation", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="submitted"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("share_ratings", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("share_feedback", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("challenge_id", "developer_id", name="uq_challenge_submissions_developer"),
        )
        op.create_index("ix_challenge_submissions_challenge_status", "challenge_submissions", ["challenge_id", "status"])
        op.create_index("ix_challenge_submissions_developer", "challenge_submissions", ["developer_id"])
    if not _has("challenge_reviews"):
        op.create_table(
            "challenge_reviews",
            sa.Column("id", UUID, primary_key=True),
            sa.Column("submission_id", UUID, sa.ForeignKey("challenge_submissions.id", ondelete="CASCADE"),
                      nullable=False, unique=True),
            _user("reviewer_id"),
            sa.Column("ratings", postgresql.JSONB(), nullable=False),
            sa.Column("feedback", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for table in ("challenge_reviews", "challenge_submissions", "challenge_invitations", "technical_challenges"):
        if _has(table):
            op.drop_table(table)
