"""Email verification, outbox, prefs, and delivery tracking.

Revision ID: 035_email_system
Revises: 034_usage_events
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

revision: str = "035_email_system"
down_revision: Union[str, None] = "034_usage_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    cols = {c["name"] for c in inspect(bind).get_columns(table)}
    return column in cols


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_column(bind, "users", "email_verified"):
        op.add_column(
            "users",
            sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )
    if not _has_column(bind, "users", "welcome_email_sent_at"):
        op.add_column("users", sa.Column("welcome_email_sent_at", sa.DateTime(timezone=True), nullable=True))

    email_pref_cols = [
        ("email_notify_connection_requests", True),
        ("email_notify_connection_milestones", True),
        ("email_notify_messages_digest", True),
        ("email_notify_collaborations", True),
        ("email_notify_project_activity", True),
        ("email_notify_recruiter", True),
        ("email_notify_weekly_summary", True),
    ]
    for name, default in email_pref_cols:
        if not _has_column(bind, "profiles", name):
            op.add_column(
                "profiles",
                sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.text("true" if default else "false")),
            )
    if not _has_column(bind, "profiles", "connection_milestone_baseline"):
        op.add_column("profiles", sa.Column("connection_milestone_baseline", sa.Integer(), nullable=True))
    if not _has_column(bind, "profiles", "connection_milestone_last"):
        op.add_column("profiles", sa.Column("connection_milestone_last", sa.Integer(), nullable=True))

    if not insp.has_table("pending_registrations"):
        op.create_table(
            "pending_registrations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("username", sa.String(64), nullable=False),
            sa.Column("password_hash", sa.String(), nullable=False),
            sa.Column("account_type", sa.String(20), nullable=False, server_default="developer"),
            sa.Column("role", sa.String(40), nullable=True),
            sa.Column("company", sa.String(120), nullable=True),
            sa.Column("purpose", sa.String(40), nullable=False, server_default="register"),
            sa.Column("github_id", sa.String(32), nullable=True),
            sa.Column("github_payload", postgresql.JSONB(), nullable=True),
            sa.Column("otp_hash", sa.String(64), nullable=False),
            sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("resend_available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("request_ip_hash", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_pending_registrations_email", "pending_registrations", ["email"])
        op.create_index("ix_pending_registrations_username", "pending_registrations", ["username"])

    if not insp.has_table("email_outbox"):
        op.create_table(
            "email_outbox",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("template", sa.String(64), nullable=False),
            sa.Column("to_email", sa.String(255), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="40"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lease_owner", sa.String(64), nullable=True),
            sa.Column("provider_message_id", sa.String(128), nullable=True),
            sa.Column("idempotency_key", sa.String(191), nullable=False),
            sa.Column("last_error", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("idempotency_key", name="uq_email_outbox_idempotency"),
        )
        op.create_index("ix_email_outbox_claim", "email_outbox", ["status", "next_attempt_at", "priority"])

    if not insp.has_table("email_delivery_events"):
        op.create_table(
            "email_delivery_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("outbox_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("email_outbox.id", ondelete="SET NULL"), nullable=True),
            sa.Column("provider_event_id", sa.String(191), nullable=False),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("recipient_domain_class", sa.String(32), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("provider_event_id", name="uq_email_delivery_provider_event"),
        )

    if not insp.has_table("email_change_requests"):
        op.create_table(
            "email_change_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("new_email", sa.String(255), nullable=False),
            sa.Column("otp_hash", sa.String(64), nullable=False),
            sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("resend_available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_email_change_requests_user_id", "email_change_requests", ["user_id"])

    # Grandfather every existing account as verified (server_default already true for new rows).
    bind.execute(text("UPDATE users SET email_verified = true WHERE email_verified IS DISTINCT FROM true"))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    for table in ("email_change_requests", "email_delivery_events", "email_outbox", "pending_registrations"):
        if insp.has_table(table):
            op.drop_table(table)
    for col in (
        "connection_milestone_last",
        "connection_milestone_baseline",
        "email_notify_weekly_summary",
        "email_notify_recruiter",
        "email_notify_project_activity",
        "email_notify_collaborations",
        "email_notify_messages_digest",
        "email_notify_connection_milestones",
        "email_notify_connection_requests",
    ):
        if _has_column(bind, "profiles", col):
            op.drop_column("profiles", col)
    if _has_column(bind, "users", "welcome_email_sent_at"):
        op.drop_column("users", "welcome_email_sent_at")
    if _has_column(bind, "users", "email_verified"):
        op.drop_column("users", "email_verified")
