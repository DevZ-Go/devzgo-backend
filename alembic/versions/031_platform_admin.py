"""Platform admin access, approval requests, and account suspension

Revision ID: 031_platform_admin
Revises: 030_chat_message_hides
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "031_platform_admin"
down_revision: Union[str, None] = "030_chat_message_hides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Seeded the first owner address. 032_owner_email moves that address to idevzgo.ca@gmail.com.
# The other four are invited once, and can be removed later.
OWNER = "devzgo@gmail.com"
TEAM = (
    "singhchangaganjeet@gmail.com",
    "rajputmanasi2002@gmail.com",
    "rajputjanasi@gmail.com",
    "munnirani855@gmail.com",
)


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    if not inspect(bind).has_table(table):
        return set()
    return {col["name"] for col in inspect(bind).get_columns(table)}


def upgrade() -> None:
    users = _columns("users")
    if users and "suspended_at" not in users:
        op.add_column("users", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    if users and "suspended_reason" not in users:
        op.add_column("users", sa.Column("suspended_reason", sa.String(length=300), nullable=True))

    if "platform_admins" not in _tables():
        op.create_table(
            "platform_admins",
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("granted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if "platform_admin_invites" not in _tables():
        op.create_table(
            "platform_admin_invites",
            sa.Column("email", sa.String(length=255), primary_key=True),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="admin"),
            sa.Column("granted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if "admin_proposals" not in _tables():
        op.create_table(
            "admin_proposals",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("target_id", sa.String(length=80), nullable=True),
            sa.Column("target_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("summary", sa.String(length=300), nullable=False),
            sa.Column("warning", sa.Text(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("required_approvals", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("proposer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("proposer_email", sa.String(length=255), nullable=False),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_admin_proposals_status_created", "admin_proposals", ["status", "created_at"])
    if "admin_proposal_votes" not in _tables():
        op.create_table(
            "admin_proposal_votes",
            sa.Column("proposal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("admin_proposals.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("voter_email", sa.String(length=255), nullable=False),
            sa.Column("decision", sa.String(length=16), nullable=False),
            sa.Column("reason", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if "admin_audit_events" not in _tables():
        op.create_table(
            "admin_audit_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("actor_email", sa.String(length=255), nullable=True),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("summary", sa.String(length=300), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False),
            sa.Column("target_type", sa.String(length=64), nullable=True),
            sa.Column("target_id", sa.String(length=80), nullable=True),
            sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_admin_audit_created", "admin_audit_events", ["created_at"])

    emails = ", ".join(f"('{email}', '{role}')" for email, role in ((OWNER, "owner"), *((item, "admin") for item in TEAM)))
    op.execute(
        "INSERT INTO platform_admin_invites (email, role) VALUES "
        + emails
        + " ON CONFLICT (email) DO NOTHING"
    )
    quoted = ", ".join(f"'{email}'" for email in (OWNER, *TEAM))
    op.execute(
        "INSERT INTO platform_admins (user_id, role) "
        "SELECT id, CASE WHEN lower(email) = 'devzgo@gmail.com' THEN 'owner' ELSE 'admin' END "
        f"FROM users WHERE lower(email) IN ({quoted}) ON CONFLICT (user_id) DO NOTHING"
    )
    op.execute(
        "DELETE FROM platform_admin_invites AS invite USING users "
        "WHERE lower(users.email) = invite.email"
    )


def downgrade() -> None:
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "admin_audit_events" in names:
        op.drop_table("admin_audit_events")
    if "admin_proposal_votes" in names:
        op.drop_table("admin_proposal_votes")
    if "admin_proposals" in names:
        op.drop_table("admin_proposals")
    if "platform_admin_invites" in names:
        op.drop_table("platform_admin_invites")
    if "platform_admins" in names:
        op.drop_table("platform_admins")
    users = _columns("users")
    if "suspended_reason" in users:
        op.drop_column("users", "suspended_reason")
    if "suspended_at" in users:
        op.drop_column("users", "suspended_at")
