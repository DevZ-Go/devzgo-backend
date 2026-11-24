"""Pending email/password registration with OTP."""

from __future__ import annotations

import hashlib
import secrets
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.email import PendingRegistration
from app.models.profile import UserProfile
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
from app.services.roles import account_type_for, assign_identity


def _auth_helpers():
    from app.routes import auth as auth_routes

    return auth_routes.save_new_user, auth_routes.username_taken


def _ip_hash(request: Request | None) -> str | None:
    if not request or not request.client:
        return None
    return hashlib.sha256(request.client.host.encode()).hexdigest()


def start_pending_registration(
    db: Session,
    *,
    email: str,
    username: str,
    password: str | None,
    role: str | None,
    company: str | None,
    request: Request | None = None,
    purpose: str = "register",
    github_id: str | None = None,
    github_payload: dict | None = None,
    full_name: str | None = None,
) -> dict:
    _, username_taken = _auth_helpers()
    email = email.strip().lower()
    if db.query(User.id).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    if username_taken(db, username):
        raise HTTPException(status_code=409, detail="Username already taken")
    if purpose == "github_onboarding" and not github_id:
        raise HTTPException(status_code=400, detail="GitHub identity is required")
    if purpose == "register" and not password:
        raise HTTPException(status_code=400, detail="Password is required")

    # Drop any unfinished pending for this email or username so codes stay unambiguous.
    db.query(PendingRegistration).filter(
        PendingRegistration.consumed_at.is_(None),
        or_(
            func.lower(PendingRegistration.email) == email,
            func.lower(PendingRegistration.username) == username.lower(),
        ),
    ).delete(synchronize_session=False)

    code = generate_otp()
    now = now_utc()
    pending = PendingRegistration(
        email=email,
        username=username,
        # GitHub onboarding has no password yet; store an unusable hash until verify creates the user.
        password_hash=hash_password(password) if password else hash_password(secrets.token_urlsafe(32)),
        account_type=account_type_for(role),
        role=role,
        company=company,
        purpose=purpose,
        github_id=github_id,
        github_payload={**(github_payload or {}), **({"full_name": full_name} if full_name else {})} or None,
        otp_hash=hash_otp(code, subject=email),
        otp_expires_at=otp_expiry(now),
        resend_available_at=resend_at(now),
        request_ip_hash=_ip_hash(request),
    )
    db.add(pending)
    db.flush()
    row = email_service.enqueue(
        db,
        event_type="verification_otp",
        template="verification_otp",
        to_email=email,
        context={"username": username, "code": code},
        idempotency_key=f"verification_otp:{pending.id}:{pending.otp_expires_at.isoformat()}",
    )
    db.commit()
    email_service.try_send_now(db, row)
    db.refresh(pending)
    return {
        "pending_id": str(pending.id),
        "email_masked": mask_email(email),
        "resend_after": pending.resend_available_at.isoformat(),
        "verification_required": True,
    }


def resend_pending_otp(db: Session, pending_id: UUID) -> dict:
    pending = (
        db.query(PendingRegistration)
        .filter(PendingRegistration.id == pending_id, PendingRegistration.consumed_at.is_(None))
        .with_for_update()
        .first()
    )
    if not pending:
        raise HTTPException(status_code=404, detail="Verification session not found")
    now = now_utc()
    if pending.resend_available_at and pending.resend_available_at > now:
        raise HTTPException(status_code=429, detail="Please wait before requesting another code")
    code = generate_otp()
    pending.otp_hash = hash_otp(code, subject=pending.email)
    pending.otp_expires_at = otp_expiry(now)
    pending.attempt_count = 0
    pending.resend_available_at = resend_at(now)
    row = email_service.enqueue(
        db,
        event_type="verification_otp",
        template="verification_otp",
        to_email=pending.email,
        context={"username": pending.username, "code": code},
        idempotency_key=f"verification_otp:{pending.id}:{pending.otp_expires_at.isoformat()}",
    )
    db.commit()
    email_service.try_send_now(db, row)
    return {
        "pending_id": str(pending.id),
        "email_masked": mask_email(pending.email),
        "resend_after": pending.resend_available_at.isoformat(),
    }


def verify_pending_otp(db: Session, pending_id: UUID, code: str) -> dict:
    pending = (
        db.query(PendingRegistration)
        .filter(PendingRegistration.id == pending_id, PendingRegistration.consumed_at.is_(None))
        .with_for_update()
        .first()
    )
    if not pending:
        raise HTTPException(status_code=404, detail="Verification session not found")
    now = now_utc()
    if pending.otp_expires_at <= now:
        raise HTTPException(status_code=400, detail="This code has expired. Request a new one.")
    if pending.attempt_count >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=400, detail="Too many attempts. Request a new code.")
    if not otp_matches(code, subject=pending.email, expected_hash=pending.otp_hash):
        pending.attempt_count += 1
        db.commit()
        left = OTP_MAX_ATTEMPTS - pending.attempt_count
        raise HTTPException(status_code=400, detail=f"Incorrect code. {left} attempt{'s' if left != 1 else ''} left.")

    save_new_user, username_taken = _auth_helpers()
    if db.query(User.id).filter(func.lower(User.email) == pending.email.lower()).first():
        pending.consumed_at = now
        db.commit()
        raise HTTPException(status_code=409, detail="Email already registered")
    if username_taken(db, pending.username):
        raise HTTPException(status_code=409, detail="Username already taken")

    is_github = pending.purpose == "github_onboarding"
    if is_github and pending.github_id and db.query(User.id).filter(User.github_id == pending.github_id).first():
        pending.consumed_at = now
        db.commit()
        raise HTTPException(status_code=409, detail="GitHub account already registered")

    user = save_new_user(
        db,
        User(
            username=pending.username,
            email=pending.email,
            password_hash=None if is_github else pending.password_hash,
            github_id=pending.github_id if is_github else None,
            account_type=pending.account_type,
            email_verified=True,
        ),
    )
    if is_github and isinstance(pending.github_payload, dict):
        gh = pending.github_payload
        profile = user.profile or UserProfile(user_id=user.id)
        if user.profile is None:
            db.add(profile)
            user.profile = profile
        profile.github_url = profile.github_url or gh.get("html_url")
        profile.avatar_url = profile.avatar_url or gh.get("avatar_url")
        profile.full_name = profile.full_name or gh.get("name") or gh.get("full_name")
        db.commit()
    elif isinstance(pending.github_payload, dict) and pending.github_payload.get("full_name"):
        profile = user.profile or UserProfile(user_id=user.id)
        if user.profile is None:
            db.add(profile)
            user.profile = profile
        profile.full_name = profile.full_name or pending.github_payload.get("full_name")
        db.commit()
    if pending.role or pending.company:
        profile = user.profile or UserProfile(user_id=user.id)
        if user.profile is None:
            db.add(profile)
            user.profile = profile
        assign_identity(
            user,
            profile,
            pending.role,
            pending.company,
            company_sent=True,
            role_sent=pending.role is not None,
        )
        db.commit()
    pending.consumed_at = now
    welcome_row = None
    if user.welcome_email_sent_at is None:
        welcome_row = email_service.enqueue(
            db,
            event_type="welcome",
            template="welcome",
            to_email=user.email,
            context={"username": user.username},
            idempotency_key=f"welcome:{user.id}",
            user_id=user.id,
        )
        user.welcome_email_sent_at = now
    db.commit()
    email_service.try_send_now(db, welcome_row)
    return {"ok": True, "username": user.username, "email": user.email}
