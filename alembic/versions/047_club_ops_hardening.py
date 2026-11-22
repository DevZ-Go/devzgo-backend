"""Club ops: event fields, poll close, email opt-in, verification status.

Revision ID: 047_club_ops_hardening
Revises: 046_club_visibility_showcases
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "047_club_ops_hardening"
down_revision: str | None = "046_club_visibility_showcases"
branch_labels = None
depends_on = None


def _cols(inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    event_cols = _cols(inspector, "club_events")
    if "club_events" in inspector.get_table_names():
        with op.batch_alter_table("club_events") as batch:
            if "timezone" not in event_cols:
                batch.add_column(sa.Column("timezone", sa.String(length=64), nullable=True))
            if "modality" not in event_cols:
                batch.add_column(
                    sa.Column("modality", sa.String(length=20), nullable=False, server_default="in_person")
                )
            if "venue_name" not in event_cols:
                batch.add_column(sa.Column("venue_name", sa.String(length=200), nullable=True))
            if "venue_address" not in event_cols:
                batch.add_column(sa.Column("venue_address", sa.Text(), nullable=True))
            if "meeting_url" not in event_cols:
                batch.add_column(sa.Column("meeting_url", sa.Text(), nullable=True))
            if "meeting_platform" not in event_cols:
                batch.add_column(sa.Column("meeting_platform", sa.String(length=80), nullable=True))
            if "poster_url" not in event_cols:
                batch.add_column(sa.Column("poster_url", sa.Text(), nullable=True))
            if "poster_format" not in event_cols:
                batch.add_column(sa.Column("poster_format", sa.String(length=20), nullable=True))
            if "poster_orientation" not in event_cols:
                batch.add_column(sa.Column("poster_orientation", sa.String(length=20), nullable=True))
            if "rsvp_url" not in event_cols:
                batch.add_column(sa.Column("rsvp_url", sa.Text(), nullable=True))

    poll_cols = _cols(inspector, "club_polls")
    if "club_polls" in inspector.get_table_names():
        with op.batch_alter_table("club_polls") as batch:
            if "closes_at" not in poll_cols:
                batch.add_column(sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True))
            if "allow_vote_change" not in poll_cols:
                batch.add_column(
                    sa.Column("allow_vote_change", sa.Boolean(), nullable=False, server_default="true")
                )

    member_cols = _cols(inspector, "club_members")
    if "club_members" in inspector.get_table_names() and "email_broadcast_opt_in" not in member_cols:
        with op.batch_alter_table("club_members") as batch:
            batch.add_column(
                sa.Column(
                    "email_broadcast_opt_in",
                    sa.Boolean(),
                    nullable=False,
                    server_default="true",
                )
            )

    club_cols = _cols(inspector, "network_clubs")
    if "network_clubs" in inspector.get_table_names():
        with op.batch_alter_table("network_clubs") as batch:
            if "verification_status" not in club_cols:
                batch.add_column(
                    sa.Column(
                        "verification_status",
                        sa.String(length=20),
                        nullable=False,
                        server_default="none",
                    )
                )
            if "verification_note" not in club_cols:
                batch.add_column(sa.Column("verification_note", sa.Text(), nullable=True))
        # Align status with existing verified_at
        op.execute(
            sa.text(
                "UPDATE network_clubs SET verification_status = 'verified' "
                "WHERE verified_at IS NOT NULL AND verification_status = 'none'"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "network_clubs" in inspector.get_table_names():
        club_cols = _cols(inspector, "network_clubs")
        with op.batch_alter_table("network_clubs") as batch:
            if "verification_note" in club_cols:
                batch.drop_column("verification_note")
            if "verification_status" in club_cols:
                batch.drop_column("verification_status")

    if "club_members" in inspector.get_table_names():
        member_cols = _cols(inspector, "club_members")
        if "email_broadcast_opt_in" in member_cols:
            with op.batch_alter_table("club_members") as batch:
                batch.drop_column("email_broadcast_opt_in")

    if "club_polls" in inspector.get_table_names():
        poll_cols = _cols(inspector, "club_polls")
        with op.batch_alter_table("club_polls") as batch:
            if "allow_vote_change" in poll_cols:
                batch.drop_column("allow_vote_change")
            if "closes_at" in poll_cols:
                batch.drop_column("closes_at")

    if "club_events" in inspector.get_table_names():
        event_cols = _cols(inspector, "club_events")
        drops = [
            "rsvp_url",
            "poster_orientation",
            "poster_format",
            "poster_url",
            "meeting_platform",
            "meeting_url",
            "venue_address",
            "venue_name",
            "modality",
            "timezone",
        ]
        with op.batch_alter_table("club_events") as batch:
            for col in drops:
                if col in event_cols:
                    batch.drop_column(col)
