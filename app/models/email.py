"""Durable email outbox, OTP pending signups, and delivery tracking."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.db.session import Base


class PendingRegistration(Base):
    """Email/password signup waiting on OTP. No users row until verification succeeds."""

    __tablename__ = "pending_registrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, index=True)
    username = Column(String(64), nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    account_type = Column(String(20), nullable=False, default="developer", server_default="developer")
    role = Column(String(40), nullable=True)
    company = Column(String(120), nullable=True)
    # github_onboarding | register (default)
    purpose = Column(String(40), nullable=False, default="register", server_default="register")
    github_id = Column(String(32), nullable=True)
    github_payload = Column(JSONB, nullable=True)
    otp_hash = Column(String(64), nullable=False)
    otp_expires_at = Column(DateTime(timezone=True), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    resend_available_at = Column(DateTime(timezone=True), nullable=False)
    request_ip_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_email_outbox_idempotency"),
        Index("ix_email_outbox_claim", "status", "next_attempt_at", "priority"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(64), nullable=False)
    template = Column(String(64), nullable=False)
    to_email = Column(String(255), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="pending", server_default="pending")
    priority = Column(SmallInteger, nullable=False, default=40, server_default="40")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=8, server_default="8")
    next_attempt_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    leased_until = Column(DateTime(timezone=True), nullable=True)
    lease_owner = Column(String(64), nullable=True)
    provider_message_id = Column(String(128), nullable=True)
    idempotency_key = Column(String(191), nullable=False)
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)


class EmailDeliveryEvent(Base):
    __tablename__ = "email_delivery_events"
    __table_args__ = (UniqueConstraint("provider_event_id", name="uq_email_delivery_provider_event"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    outbox_id = Column(UUID(as_uuid=True), ForeignKey("email_outbox.id", ondelete="SET NULL"), nullable=True)
    provider_event_id = Column(String(191), nullable=False)
    event_type = Column(String(64), nullable=False)
    recipient_domain_class = Column(String(32), nullable=True)
    detail = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EmailChangeRequest(Base):
    """Pending swap to a new email address, confirmed by OTP on the new address."""

    __tablename__ = "email_change_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    new_email = Column(String(255), nullable=False)
    otp_hash = Column(String(64), nullable=False)
    otp_expires_at = Column(DateTime(timezone=True), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    resend_available_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
