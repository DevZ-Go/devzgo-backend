"""Project comment replies/likes and network clubs with executive roles.

Revision ID: 042_comments_clubs
Revises: 041_pi_visitor_ai_requests
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "042_comments_clubs"
down_revision: Union[str, None] = "041_pi_visitor_ai_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return name in inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    cols = {c["name"] for c in inspect(bind).get_columns(table)}
    return column in cols


def upgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "project_comments") and not _has_column(bind, "project_comments", "parent_id"):
        op.add_column(
            "project_comments",
            sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_comments.id", ondelete="CASCADE"), nullable=True),
        )
        op.create_index("ix_project_comments_parent", "project_comments", ["parent_id", "created_at"])

    if not _has_table(bind, "project_comment_likes"):
        op.create_table(
            "project_comment_likes",
            sa.Column("comment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project_comments.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_project_comment_likes_user", "project_comment_likes", ["user_id"])

    if not _has_table(bind, "network_clubs"):
        op.create_table(
            "network_clubs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("slug", sa.String(120), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("tagline", sa.String(280), nullable=False, server_default=""),
            sa.Column("about", sa.Text(), nullable=False, server_default=""),
            sa.Column("category", sa.String(40), nullable=False, server_default="general"),
            sa.Column("banner_url", sa.Text(), nullable=True),
            sa.Column("avatar_url", sa.Text(), nullable=True),
            sa.Column("tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("policies", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("slug", name="uq_network_clubs_slug"),
        )
        op.create_index("ix_network_clubs_category", "network_clubs", ["category", "created_at"])

    if not _has_table(bind, "club_custom_roles"):
        op.create_table(
            "club_custom_roles",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("club_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("club_id", "title", name="uq_club_custom_role_title"),
        )

    if not _has_table(bind, "club_members"):
        op.create_table(
            "club_members",
            sa.Column("club_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("network_clubs.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("role", sa.String(32), nullable=False, server_default="member"),
            sa.Column("custom_title", sa.String(120), nullable=True),
            sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_club_members_user", "club_members", ["user_id", "joined_at"])
        # A user may be president of at most one club.
        op.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_club_one_presidency
                ON club_members (user_id)
                WHERE role = 'president'
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "club_members"):
        op.execute(sa.text("DROP INDEX IF EXISTS uq_club_one_presidency"))
        op.drop_table("club_members")
    if _has_table(bind, "club_custom_roles"):
        op.drop_table("club_custom_roles")
    if _has_table(bind, "network_clubs"):
        op.drop_table("network_clubs")
    if _has_table(bind, "project_comment_likes"):
        op.drop_table("project_comment_likes")
    if _has_table(bind, "project_comments") and _has_column(bind, "project_comments", "parent_id"):
        op.drop_index("ix_project_comments_parent", table_name="project_comments")
        op.drop_column("project_comments", "parent_id")
