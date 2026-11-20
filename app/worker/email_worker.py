"""Drain email_outbox and clean abandoned pending registrations.

Run: python -m app.worker.email_worker
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.email import EmailOutbox, PendingRegistration
from app.services import email_service
from app.services.email import send_email

log = logging.getLogger("email_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
POLL_SECONDS = float(os.getenv("EMAIL_WORKER_POLL_SECONDS", "2"))
BATCH = int(os.getenv("EMAIL_WORKER_BATCH", "20"))
LEASE = timedelta(seconds=int(os.getenv("EMAIL_WORKER_LEASE_SECONDS", "60")))


def claim_batch(db: Session) -> list[EmailOutbox]:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(EmailOutbox)
        .filter(
            EmailOutbox.status == "pending",
            EmailOutbox.next_attempt_at <= now,
            or_(EmailOutbox.leased_until.is_(None), EmailOutbox.leased_until < now),
        )
        .order_by(EmailOutbox.priority.asc(), EmailOutbox.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(BATCH)
        .all()
    )
    until = now + LEASE
    for row in rows:
        row.status = "sending"
        row.leased_until = until
        row.lease_owner = OWNER
    if rows:
        db.commit()
        for row in rows:
            db.refresh(row)
    return rows


def process_one(db: Session, row: EmailOutbox) -> None:
    payload = row.payload or {}
    subject = str(payload.get("subject") or row.event_type)
    html = str(payload.get("html") or "")
    text = payload.get("text")
    try:
        provider_id = send_email(
            row.to_email,
            subject,
            html,
            text=str(text) if text else None,
            idempotency_key=row.idempotency_key,
            dev_note=f"outbox:{row.id}",
        )
        email_service.mark_sent(row, provider_id)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — worker must keep running
        db.rollback()
        row = db.get(EmailOutbox, row.id)
        if not row:
            return
        permanent = "resend_4" in str(exc) or "resend_test_sender" in str(exc)
        if permanent:
            email_service.mark_failed(row, str(exc))
        else:
            email_service.mark_retry(row, str(exc))
        db.commit()


def cleanup_pendings(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    q = db.query(PendingRegistration).filter(
        PendingRegistration.consumed_at.is_(None),
        PendingRegistration.created_at < cutoff,
    )
    n = q.count()
    q.delete(synchronize_session=False)
    if n:
        db.commit()
    return n


def run_once() -> None:
    db = SessionLocal()
    try:
        for row in claim_batch(db):
            process_one(db, row)
        cleanup_pendings(db)
        try:
            from app.services.entitlements import notify_expired_premium
            notify_expired_premium(db)
            db.commit()
        except Exception:  # noqa: BLE001
            log.exception("premium expiry notify failed")
            db.rollback()
    finally:
        db.close()


def main() -> None:
    log.info("email worker started as %s", OWNER)
    while True:
        try:
            run_once()
        except Exception:  # noqa: BLE001
            log.exception("email worker loop error")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
