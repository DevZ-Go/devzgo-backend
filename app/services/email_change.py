"""Request and confirm a new account email via OTP on the new address."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.email import EmailChangeRequest
from app.models.user import User
from app.services import email_service
from app.services.otp import (
    OTP_MAX_ATTEMPTS,
    generate_otp,
    hash_otp,
    mask_email,
    now_utc,
    otp_expiry,
    otp_matches,
    resend_at,
)


def start_email_change(db: Session, user: User, new_email: str) -> dict:
    new_email = new_email.strip().lower()
    if new_email == (user.email or "").lower():
        raise HTTPException(status_code=400, detail="That is already your email address")
    if db.query(User.id).filter(func.lower(User.email) == new_email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    db.query(EmailChangeRequest).filter(
        EmailChangeRequest.user_id == user.id,
        EmailChangeRequest.consumed_at.is_(None),
    ).delete(synchronize_session=False)

    code = generate_otp()
    now = now_utc()
    row = EmailChangeRequest(
        user_id=user.id,
        new_email=new_email,
        otp_hash=hash_otp(code, subject=new_email),
        otp_expires_at=otp_expiry(now),
        resend_available_at=resend_at(now),
    )
    db.add(row)
    db.flush()
    outbox = email_service.enqueue(
        db,
        event_type="verification_otp",
        template="verification_otp",
        to_email=new_email,
        context={"username": user.username, "code": code},
        idempotency_key=f"email_change_otp:{row.id}:{row.otp_expires_at.isoformat()}",
        user_id=user.id,
    )
    db.commit()
    email_service.try_send_now(db, outbox)
    db.refresh(row)
    return {
        "request_id": str(row.id),
        "email_masked": mask_email(new_email),
        "resend_after": row.resend_available_at.isoformat(),
    }


def confirm_email_change(db: Session, user: User, request_id: UUID, code: str) -> User:
    req = (
        db.query(EmailChangeRequest)
        .filter(
            EmailChangeRequest.id == request_id,
            EmailChangeRequest.user_id == user.id,
            EmailChangeRequest.consumed_at.is_(None),
        )
        .with_for_update()
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="Email change request not found")
    now = now_utc()
    if req.otp_expires_at <= now:
        raise HTTPException(status_code=400, detail="This code has expired. Request a new one.")
    if req.attempt_count >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=400, detail="Too many attempts. Request a new code.")
    if not otp_matches(code, subject=req.new_email, expected_hash=req.otp_hash):
        req.attempt_count += 1
        db.commit()
        left = OTP_MAX_ATTEMPTS - req.attempt_count
        raise HTTPException(status_code=400, detail=f"Incorrect code. {left} attempt{'s' if left != 1 else ''} left.")

    if db.query(User.id).filter(func.lower(User.email) == req.new_email.lower(), User.id != user.id).first():
        req.consumed_at = now
        db.commit()
        raise HTTPException(status_code=409, detail="Email already registered")

    old_email = user.email
    user.email = req.new_email
    user.email_verified = True
    user.token_version = (user.token_version or 0) + 1
    req.consumed_at = now
    email_service.enqueue(
        db,
        event_type="email_changed",
        template="email_changed",
        to_email=old_email,
        context={"username": user.username, "new_email": req.new_email},
        idempotency_key=f"email_changed:{user.id}:{user.token_version}",
        user_id=user.id,
    )
    email_service.enqueue(
        db,
        event_type="email_changed",
        template="email_changed",
        to_email=req.new_email,
        context={"username": user.username, "new_email": req.new_email},
        idempotency_key=f"email_changed_new:{user.id}:{user.token_version}",
        user_id=user.id,
    )
    db.commit()
    db.refresh(user)
    return user
