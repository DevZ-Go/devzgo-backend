"""project invitations, member permissions, one pending join request per person and project

Revision ID: 007_collaboration
Revises: 006_connection_pairs
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "007_collaboration"
down_revision: Union[str, None] = "006_connection_pairs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMISSIONS = ("can_edit_details", "can_manage_files", "can_view_secrets")


def _flags():
    return [sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()) for name in PERMISSIONS]


def upgrade() -> None:
    clashes = op.get_bind().execute(sa.text(
        "SELECT project_id, requester_id, count(*) FROM collaboration_requests WHERE status = 'pending' "
        "GROUP BY 1, 2 HAVING count(*) > 1"
    )).all()
    if clashes:
        report = "; ".join(f"project {p} / user {u} ({n} rows)" for p, u, n in clashes)
        raise RuntimeError(f"Duplicate pending collaboration requests must be resolved by hand first: {report}")
    op.create_index("uq_collab_requests_pending", "collaboration_requests", ["project_id", "requester_id"],
                    unique=True, postgresql_where=sa.text("status = 'pending'"))

    for column in _flags():
        op.add_column("project_collaborators", column)

    op.create_table(
        "project_invitations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("inviter_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invitee_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(100), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        *_flags(),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_invitations_invitee", "project_invitations", ["invitee_id"])
    op.create_index("uq_project_invitations_pending", "project_invitations", ["project_id", "invitee_id"],
                    unique=True, postgresql_where=sa.text("status = 'pending'"))


def downgrade() -> None:
    op.drop_table("project_invitations")
    for name in PERMISSIONS:
        op.drop_column("project_collaborators", name)
    op.drop_index("uq_collab_requests_pending", table_name="collaboration_requests")
