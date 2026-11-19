"""Enqueue durable transactional email. Never send inside a half-finished DB transaction caller still owns — commit the outbox row with the business commit, then let the worker deliver."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.email import EmailOutbox
from app.services.email_templates import render_template

log = logging.getLogger("uvicorn.error")

PRIORITY = {
    "verification_otp": 10,
    "password_reset": 10,
    "password_changed": 20,
    "email_changed": 20,
    "account_paused": 20,
    "account_restored": 20,
    "account_deletion_requested": 20,
    "account_deleted": 20,
    "welcome": 20,
    "collaboration_invite": 30,
    "recruiter_opportunity": 30,
    "connection_request": 40,
    "connection_milestone": 40,
    "messages_digest": 40,
    "unread_messages_digest": 40,
    "project_update": 40,
    "weekly_summary": 40,
}


def require_email_verification() -> bool:
    return os.getenv("REQUIRE_EMAIL_VERIFICATION", "").lower() in {"1", "true", "yes", "on"}


def enqueue(
    db: Session,
    *,
    event_type: str,
    template: str,
    to_email: str,
    context: dict[str, Any],
    idempotency_key: str,
    user_id: uuid.UUID | None = None,
    priority: int | None = None,
) -> EmailOutbox | None:
    to = (to_email or "").strip().lower()
    if not to or "@" not in to:
        log.warning("Skipping email enqueue %s: missing recipient", event_type)
        return None
    # Render template once so the worker can send even if later code changes; OTP codes live only in payload.
    subject, html, text = render_template(template, context)
    payload = {
        "context": context,
        "subject": subject,
        "html": html,
        "text": text,
    }
    pri = priority if priority is not None else PRIORITY.get(template, 40)
    stmt = (
        pg_insert(EmailOutbox)
        .values(
            id=uuid.uuid4(),
            event_type=event_type,
            template=template,
            to_email=to,
            user_id=user_id,
            payload=payload,
            status="pending",
            priority=pri,
            idempotency_key=idempotency_key[:191],
            next_attempt_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(EmailOutbox.id)
    )
    row = db.execute(stmt).first()
    if row is None:
        existing = db.query(EmailOutbox).filter_by(idempotency_key=idempotency_key[:191]).one_or_none()
        return existing
    db.flush()
    return db.get(EmailOutbox, row[0])


def enqueue_and_commit(
    db: Session,
    **kwargs: Any,
) -> EmailOutbox | None:
    row = enqueue(db, **kwargs)
    db.commit()
    return row


def backoff_seconds(attempts: int) -> int:
    return min(2 ** max(attempts, 1), 3600)


def mark_retry(row: EmailOutbox, error: str) -> None:
    row.attempts = (row.attempts or 0) + 1
    row.last_error = (error or "")[:500]
    row.status = "pending" if row.attempts < row.max_attempts else "failed"
    row.leased_until = None
    row.lease_owner = None
    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds(row.attempts))


def mark_sent(row: EmailOutbox, provider_message_id: str | None) -> None:
    row.status = "sent"
    row.provider_message_id = (provider_message_id or "")[:128] or None
    row.sent_at = datetime.now(timezone.utc)
    row.leased_until = None
    row.lease_owner = None
    row.last_error = None


def mark_failed(row: EmailOutbox, error: str) -> None:
    row.status = "failed"
    row.last_error = (error or "")[:500]
    row.leased_until = None
    row.lease_owner = None


def try_send_now(db: Session, row: EmailOutbox | None) -> None:
    """Best-effort immediate send for high-priority mail when a worker is not yet running."""
    if row is None or row.status != "pending":
        return
    from app.services.email import send_email

    payload = row.payload or {}
    try:
        provider_id = send_email(
            row.to_email,
            str(payload.get("subject") or row.event_type),
            str(payload.get("html") or ""),
            text=str(payload["text"]) if payload.get("text") else None,
            idempotency_key=row.idempotency_key,
            dev_note=f"outbox:{row.id}",
        )
        mark_sent(row, provider_id)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        fresh = db.get(EmailOutbox, row.id)
        if fresh:
            mark_retry(fresh, str(exc))
            db.commit()
