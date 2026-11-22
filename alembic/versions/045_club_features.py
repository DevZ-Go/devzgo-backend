"""Club extensions: perms, events, announcements, polls, funds, galleries, verification.

Revision ID: 045_club_features
Revises: 044_content_saves_link_previews
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "045_club_features"
down_revision: str | None = "044_content_saves_link_previews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())
    club_cols = {c["name"] for c in inspector.get_columns("network_clubs")} if "network_clubs" in existing else set()

    with op.batch_alter_table("network_clubs") as batch:
        if "institution" not in club_cols:
            batch.add_column(sa.Column("institution", sa.String(length=200), nullable=True))
        if "join_policy" not in club_cols:
            batch.add_column(
                sa.Column("join_policy", sa.String(length=20), nullable=False, server_default="open")
            )
        if "contact_socials" not in club_cols:
            batch.add_column(
                sa.Column(
                    "contact_socials",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=False,
                    server_default=sa.text("'{}'::jsonb"),
                )
            )
        if "verified_at" not in club_cols:
            batch.add_column(sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
        if "verified_by" not in club_cols:
            batch.add_column(sa.Column("verified_by", postgresql.UUID(as_uuid=True), nullable=True))
        if "purpose" not in club_cols:
            batch.add_column(sa.Column("purpose", sa.Text(), nullable=True))

    member_cols = {c["name"] for c in inspector.get_columns("club_members")} if "club_members" in existing else set()
    if "hide_on_profile" not in member_cols:
        with op.batch_alter_table("club_members") as batch:
            batch.add_column(
                sa.Column("hide_on_profile", sa.Boolean(), nullable=False, server_default="true")
            )

    if "club_role_permissions" not in existing:
        op.create_table(
            "club_role_permissions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("club_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("club_id", "role", name="uq_club_role_permissions"),
        )
        op.create_index("ix_club_role_permissions_club", "club_role_permissions", ["club_id"])

    if "club_events" not in existing:
        op.create_table(
            "club_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("club_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("location", sa.String(length=280), nullable=True),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("academic_term", sa.String(length=40), nullable=True),
            sa.Column("academic_year", sa.String(length=20), nullable=True),
            sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_club_events_club_starts", "club_events", ["club_id", "starts_at"])

    if "club_announcements" not in existing:
        op.create_table(
            "club_announcements",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("club_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("media_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("badge", sa.String(length=60), nullable=False, server_default="General Update"),
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="published"),
            sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("club_events.id", ondelete="SET NULL"), nullable=True),
            sa.Column("author_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_club_announcements_club_created", "club_announcements", ["club_id", "created_at"])

    if "club_polls" not in existing:
        op.create_table(
            "club_polls",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("club_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("announcement_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("club_announcements.id", ondelete="CASCADE"), nullable=True),
            sa.Column("question", sa.String(length=400), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
            sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )

    if "club_poll_options" not in existing:
        op.create_table(
            "club_poll_options",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("poll_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("club_polls.id", ondelete="CASCADE"), nullable=False),
            sa.Column("label", sa.String(length=200), nullable=False),
            sa.Column("sort_order", sa.String(length=10), nullable=False, server_default="0"),
        )

    if "club_poll_votes" not in existing:
        op.create_table(
            "club_poll_votes",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("poll_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("club_polls.id", ondelete="CASCADE"), nullable=False),
            sa.Column("option_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("club_poll_options.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("poll_id", "user_id", name="uq_club_poll_vote_member"),
        )

    if "club_fund_entries" not in existing:
        op.create_table(
            "club_fund_entries",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("club_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="CAD"),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("entry_type", sa.String(length=20), nullable=False, server_default="expense"),
            sa.Column("public_disclosure", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_club_fund_entries_club", "club_fund_entries", ["club_id", "created_at"])

    if "club_event_media" not in existing:
        op.create_table(
            "club_event_media",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("club_events.id", ondelete="CASCADE"), nullable=False),
            sa.Column("club_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("media_type", sa.String(length=40), nullable=False, server_default="image"),
            sa.Column("academic_term", sa.String(length=40), nullable=True),
            sa.Column("academic_year", sa.String(length=20), nullable=True),
            sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_club_event_media_club_term", "club_event_media", ["club_id", "academic_term", "academic_year"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())
    for table in (
        "club_event_media",
        "club_fund_entries",
        "club_poll_votes",
        "club_poll_options",
        "club_polls",
        "club_announcements",
        "club_events",
        "club_role_permissions",
    ):
        if table in existing:
            op.drop_table(table)
