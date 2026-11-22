"""Club visibility settings and project showcases.

Revision ID: 046_club_visibility_showcases
Revises: 045_club_features
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "046_club_visibility_showcases"
down_revision: str | None = "045_club_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())
    club_cols = {c["name"] for c in inspector.get_columns("network_clubs")} if "network_clubs" in existing else set()

    with op.batch_alter_table("network_clubs") as batch:
        if "funds_visibility" not in club_cols:
            batch.add_column(
                sa.Column("funds_visibility", sa.String(length=20), nullable=False, server_default="members")
            )
        if "announcements_audience" not in club_cols:
            batch.add_column(
                sa.Column(
                    "announcements_audience", sa.String(length=20), nullable=False, server_default="public"
                )
            )

    if "club_announcements" in existing:
        ann_cols = {c["name"] for c in inspector.get_columns("club_announcements")}
        if "audience" not in ann_cols:
            with op.batch_alter_table("club_announcements") as batch:
                batch.add_column(
                    sa.Column(
                        "audience",
                        sa.String(length=20),
                        nullable=False,
                        server_default="members",
                    )
                )

    if "club_showcases" not in existing:
        op.create_table(
            "club_showcases",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "club_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("network_clubs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "project_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("tagline", sa.String(length=280), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("how_we_built", sa.Text(), nullable=True),
            sa.Column("challenges", sa.Text(), nullable=True),
            sa.Column("kind", sa.String(length=40), nullable=False, server_default="software"),
            sa.Column("sub_category", sa.String(length=120), nullable=True),
            sa.Column("cover_url", sa.Text(), nullable=True),
            sa.Column(
                "tech_stacks",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("github_url", sa.Text(), nullable=True),
            sa.Column("live_url", sa.Text(), nullable=True),
            sa.Column("hardware_cad_url", sa.Text(), nullable=True),
            sa.Column(
                "embedded_specs",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column(
                "owner_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_club_showcases_club_created", "club_showcases", ["club_id", "created_at"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())
    if "club_showcases" in existing:
        op.drop_table("club_showcases")
    club_cols = {c["name"] for c in inspector.get_columns("network_clubs")} if "network_clubs" in existing else set()
    with op.batch_alter_table("network_clubs") as batch:
        if "announcements_audience" in club_cols:
            batch.drop_column("announcements_audience")
        if "funds_visibility" in club_cols:
            batch.drop_column("funds_visibility")
