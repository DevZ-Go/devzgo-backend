"""one connection per pair of users, and when it was accepted

Revision ID: 006_connection_pairs
Revises: 005_project_comments
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006_connection_pairs"
down_revision: Union[str, None] = "005_project_comments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    clashes = op.get_bind().execute(sa.text(
        "SELECT least(requester_id, receiver_id), greatest(requester_id, receiver_id), count(*) FROM connections "
        "GROUP BY 1, 2 HAVING count(*) > 1"
    )).all()
    if clashes:
        report = "; ".join(f"{a} <-> {b} ({n} rows)" for a, b, n in clashes)
        raise RuntimeError(f"Users with more than one connection row must be resolved by hand first: {report}")
    op.add_column("connections", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE connections SET accepted_at = coalesce(updated_at, created_at) WHERE status = 'accepted'")
    op.create_index("uq_connections_pair", "connections",
                    [sa.text("least(requester_id, receiver_id)"), sa.text("greatest(requester_id, receiver_id)")], unique=True)


def downgrade() -> None:
    op.drop_index("uq_connections_pair", table_name="connections")
    op.drop_column("connections", "accepted_at")
