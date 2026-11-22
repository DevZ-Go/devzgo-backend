"""Move 3-exec approval from banner to club logo (avatar).

Revision ID: 049_logo_approval
Revises: 048_event_room_perk
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "049_logo_approval"
down_revision: str | None = "048_event_room_perk"
branch_labels = None
depends_on = None


def _cols(inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    club_cols = _cols(inspector, "network_clubs")
    if "network_clubs" not in inspector.get_table_names():
        return

    with op.batch_alter_table("network_clubs") as batch:
        if "pending_avatar_url" not in club_cols:
            batch.add_column(sa.Column("pending_avatar_url", sa.Text(), nullable=True))
        if "pending_avatar_approvals" not in club_cols:
            batch.add_column(
                sa.Column(
                    "pending_avatar_approvals",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=False,
                    server_default=sa.text("'[]'::jsonb"),
                )
            )

    # Move any pending banner proposals into logo pending (safety), then drop banner pending cols
    if "pending_banner_url" in club_cols:
        op.execute(
            sa.text(
                """
                UPDATE network_clubs
                SET pending_avatar_url = COALESCE(pending_avatar_url, pending_banner_url),
                    pending_avatar_approvals = CASE
                      WHEN pending_banner_approvals IS NOT NULL
                           AND pending_banner_approvals::text != '[]'
                      THEN pending_banner_approvals
                      ELSE pending_avatar_approvals
                    END
                WHERE pending_banner_url IS NOT NULL
                """
            )
        )
        with op.batch_alter_table("network_clubs") as batch:
            batch.drop_column("pending_banner_url")
            if "pending_banner_approvals" in club_cols:
                batch.drop_column("pending_banner_approvals")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    club_cols = _cols(inspector, "network_clubs")
    if "network_clubs" not in inspector.get_table_names():
        return
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
        if "pending_avatar_url" in club_cols:
            batch.drop_column("pending_avatar_url")
        if "pending_avatar_approvals" in club_cols:
            batch.drop_column("pending_avatar_approvals")
