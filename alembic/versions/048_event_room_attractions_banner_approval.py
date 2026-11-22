"""Event room/attractions + club banner 3-exec approval.

Revision ID: 048_event_room_perk
Revises: 047_club_ops_hardening
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "048_event_room_perk"
down_revision: str | None = "047_club_ops_hardening"
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
            if "room_number" not in event_cols:
                batch.add_column(sa.Column("room_number", sa.String(length=80), nullable=True))
            if "attractions" not in event_cols:
                batch.add_column(sa.Column("attractions", sa.Text(), nullable=True))

    club_cols = _cols(inspector, "network_clubs")
    if "network_clubs" in inspector.get_table_names():
        with op.batch_alter_table("network_clubs") as batch:
            if "pending_banner_url" not in club_cols:
                batch.add_column(sa.Column("pending_banner_url", sa.Text(), nullable=True))
            if "pending_banner_approvals" not in club_cols:
                batch.add_column(
                    sa.Column(
                        "pending_banner_approvals",
                        postgresql.JSONB(astext_type=sa.Text()),
                        nullable=False,
                        server_default=sa.text("'[]'::jsonb"),
                    )
                )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    event_cols = _cols(inspector, "club_events")
    if "club_events" in inspector.get_table_names():
        with op.batch_alter_table("club_events") as batch:
            if "attractions" in event_cols:
                batch.drop_column("attractions")
            if "room_number" in event_cols:
                batch.drop_column("room_number")
    club_cols = _cols(inspector, "network_clubs")
    if "network_clubs" in inspector.get_table_names():
        with op.batch_alter_table("network_clubs") as batch:
            if "pending_banner_approvals" in club_cols:
                batch.drop_column("pending_banner_approvals")
            if "pending_banner_url" in club_cols:
                batch.drop_column("pending_banner_url")
