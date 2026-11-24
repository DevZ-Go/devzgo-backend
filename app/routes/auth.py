import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.user import (
    AccountConfirmRequest, ChangePasswordRequest, EmailChangeConfirmBody, EmailChangeRequestBody,
    ForgotPasswordRequest, ResetPasswordRequest, ResendVerificationRequest, ScheduleDeleteRequest,
    UserCreate, UserResponse, UsernameAvailability, UsernameChangeRequest, VerifyEmailRequest,
)
from app.db.session import get_db
from app.models.user import User
from app.models.profile import UserProfile
from app.services.roles import ROLES, account_type_for, assign_identity
from app.models.password_reset import PasswordResetToken
from app.core.policy import normalize_username, username_problem
from app.core.public_urls import frontend_base
from app.core.rate_limit import rate_limit
from app.core.security import hash_password, verify_password, issue_token
from app.core.dependencies import get_current_user, get_current_user_optional
from app.services import account_lifecycle, email_service
from app.services.email_service import require_email_verification
from app.services import registration as registration_service

router = APIRouter(prefix="/auth", tags=["Auth"])

RESET_TTL = timedelta(minutes=30)
RESET_COOLDOWN = timedelta(minutes=1)


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def username_taken(db: Session, username: str, except_id=None) -> bool:
    query = db.query(User.id).filter(func.lower(User.username) == username.lower())
    if except_id is not None:
        query = query.filter(User.id != except_id)
    return query.first() is not None


def save_new_user(db: Session, user: User) -> User:
    """Insert, turning a uniqueness race into the same 409 the pre-checks give."""
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username or email already taken")
    db.refresh(user)
    from app.services.platform_admin import claim_reserved_admin
    claim_reserved_admin(db, user)
    return user


@router.get("/username-available", response_model=UsernameAvailability)
def username_available(
    username: str = Query(max_length=64),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    name = normalize_username(username)
    taken = username_taken(db, name, except_id=current_user.id if current_user else None)
    reason = username_problem(name) or ("taken" if taken else None)
    return {"username": name, "available": reason is None, "reason": reason}


@router.post("/register", dependencies=[rate_limit("register", 10, 60)])
def register(user_data: UserCreate, request: Request, db: Session = Depends(get_db)):
    email = user_data.email.strip().lower()
    if user_data.role is not None and user_data.role not in ROLES:
        raise HTTPException(status_code=400, detail="Choose one of the listed roles")
    if user_data.role == "Recruiter" and len(" ".join((user_data.company or "").split())) < 2:
        raise HTTPException(status_code=400, detail="Add the company or agency you recruit for")

    first = (user_data.first_name or "").strip()
    last = (user_data.last_name or "").strip()
    if not first or not last:
        raise HTTPException(status_code=400, detail="Enter your first and last name")
    full_name = f"{first} {last}"

    if require_email_verification():
        return registration_service.start_pending_registration(
            db,
            email=email,
            username=user_data.username,
            password=user_data.password,
            role=user_data.role,
            company=user_data.company,
            full_name=full_name,
            request=request,
        )

    if db.query(User.id).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    if username_taken(db, user_data.username):
        raise HTTPException(status_code=409, detail="Username already taken")
    user = save_new_user(db, User(
        username=user_data.username, email=email, password_hash=hash_password(user_data.password),
        account_type=account_type_for(user_data.role),
        email_verified=True,
    ))
    profile = UserProfile(user_id=user.id, full_name=full_name)
    db.add(profile)
    if user_data.role or user_data.company:
        assign_identity(
            user, profile, user_data.role, user_data.company,
            company_sent=True, role_sent=user_data.role is not None,
        )
    user.profile = profile
    db.commit()
    if user.welcome_email_sent_at is None:
        row = email_service.enqueue(
            db,
            event_type="welcome",
            template="welcome",
            to_email=user.email,
            context={"username": user.username},
            idempotency_key=f"welcome:{user.id}",
            user_id=user.id,
        )
        from datetime import datetime, timezone as tz
        user.welcome_email_sent_at = datetime.now(tz.utc)
        db.commit()
        email_service.try_send_now(db, row)
        db.refresh(user)
    return UserResponse.model_validate(user)


@router.post("/verify-email", dependencies=[rate_limit("verify_email", 20, 60)])
def verify_email(data: VerifyEmailRequest, db: Session = Depends(get_db)):
    return registration_service.verify_pending_otp(db, data.pending_id, data.code)


@router.post("/resend-verification", dependencies=[rate_limit("resend_verification", 10, 60)])
def resend_verification(data: ResendVerificationRequest, db: Session = Depends(get_db)):
    return registration_service.resend_pending_otp(db, data.pending_id)


@router.post("/email-change", dependencies=[rate_limit("email_change", 5, 60)])
def request_email_change(
    data: EmailChangeRequestBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services import email_change as email_change_service

    return email_change_service.start_email_change(db, user, str(data.new_email))


@router.post("/email-change/confirm", dependencies=[rate_limit("email_change_confirm", 20, 60)])
def confirm_email_change(
    data: EmailChangeConfirmBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services import email_change as email_change_service

    updated = email_change_service.confirm_email_change(db, user, data.request_id, data.code)
    return {
        "ok": True,
        "email": updated.email,
        "access_token": issue_token(updated),
        "token_type": "bearer",
    }


@router.post("/change-password")
def change_password(data: ChangePasswordRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Change the password, or set a first one for a GitHub-only account. Other sessions are signed out."""
    if user.password_hash and not verify_password(data.current_password or "", user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(data.new_password)
    user.token_version = (user.token_version or 0) + 1
    email_service.enqueue(
        db,
        event_type="password_changed",
        template="password_changed",
        to_email=user.email,
        context={"username": user.username},
        idempotency_key=f"password_changed:{user.id}:{user.token_version}",
        user_id=user.id,
    )
    db.commit()
    return {"message": "Password updated.", "access_token": issue_token(user), "token_type": "bearer"}


def _confirm_account_action(user: User, data: AccountConfirmRequest, *, require_username: bool = False) -> None:
    """Password accounts must prove the password; GitHub-only accounts confirm with their username."""
    if require_username or not user.password_hash:
        typed = normalize_username((data.username or "").strip())
        if not typed or typed.lower() != user.username.lower():
            raise HTTPException(status_code=400, detail="Type your username to confirm")
    if user.password_hash:
        if not data.password or not verify_password(data.password, user.password_hash):
            raise HTTPException(status_code=400, detail="Password is incorrect")


@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    login_id = form_data.username.strip().lower().removeprefix("@")
    column = User.email if "@" in login_id else User.username
    user = db.query(User).filter(func.lower(column) == login_id).first()

    if not user or not user.password_hash or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email, username or password")
    if require_email_verification() and not user.email_verified:
        raise HTTPException(status_code=403, detail="Verify your email before signing in.")

    user = account_lifecycle.reactivate_on_login(db, user)
    return {"access_token": issue_token(user), "token_type": "bearer"}


@router.post("/deactivate", response_model=UserResponse)
def deactivate_account(
    data: AccountConfirmRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hide the account until the next successful sign-in. Signs out every session."""
    _confirm_account_action(user, data)
    return account_lifecycle.deactivate(db, user)


@router.post("/schedule-delete", response_model=UserResponse)
def schedule_account_delete(
    data: ScheduleDeleteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Schedule permanent deletion in 30 days. Signing in before then cancels it."""
    _confirm_account_action(user, data, require_username=True)
    return account_lifecycle.schedule_delete(db, user)


@router.post("/cancel-delete", response_model=UserResponse)
def cancel_account_delete(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Clear a pending deletion while still authenticated."""
    return account_lifecycle.cancel_delete(db, user)


@router.post("/forgot-password", dependencies=[rate_limit("forgot_password", 10, 60)])
def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Always the same answer, so it cannot be used to find out which emails have accounts."""
    reply = {"message": "If that matches an account, a reset link is on its way to its email address."}
    handle = data.email.strip().lower().lstrip("@")
    if "@" in handle:
        user = db.query(User).filter(func.lower(User.email) == handle).first()
    else:
        user = db.query(User).filter(func.lower(User.username) == handle).first()
    if not user or not user.email:
        return reply

    now = datetime.now(timezone.utc)
    if db.query(PasswordResetToken.id).filter(
        PasswordResetToken.user_id == user.id, PasswordResetToken.created_at > now - RESET_COOLDOWN
    ).first():
        return reply  # at most one email per minute per account

    token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(user_id=user.id, token_hash=_sha256(token), expires_at=now + RESET_TTL))
    link = f"{frontend_base()}/reset-password?token={token}"
    row = email_service.enqueue(
        db,
        event_type="password_reset",
        template="password_reset",
        to_email=user.email,
        context={"username": user.username, "link": link},
        idempotency_key=f"password_reset:{user.id}:{_sha256(token)[:16]}",
        user_id=user.id,
    )
    db.commit()
    email_service.try_send_now(db, row)
    return reply


@router.post("/reset-password")
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == _sha256(data.token),
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > now,
    ).with_for_update().first()
    if not record:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

    user = db.query(User).filter(User.id == record.user_id).first()
    user.password_hash = hash_password(data.new_password)
    user.token_version = (user.token_version or 0) + 1
    # Using one link retires every other outstanding link for this account.
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)
    ).update({"used_at": now})
    email_service.enqueue(
        db,
        event_type="password_changed",
        template="password_changed",
        to_email=user.email,
        context={"username": user.username},
        idempotency_key=f"password_changed:{user.id}:{user.token_version}",
        user_id=user.id,
    )
    db.commit()
    return {"message": "Password updated. You can sign in now."}


@router.put("/username", response_model=UserResponse)
def change_username(data: UsernameChangeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if username_taken(db, data.username, except_id=user.id):
        raise HTTPException(status_code=409, detail="Username already taken")
    user.username = data.username
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username already taken")
    db.refresh(user)
    return user


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.platform_admin import claim_reserved_admin
    role = claim_reserved_admin(db, current_user)
    profile = current_user.profile
    full_name = (profile.full_name or "").strip() if profile else ""
    # Require first + last (at least two parts). Username-only or single-token names prompt completion.
    name_parts = [p for p in full_name.split() if p]
    return UserResponse.model_validate(current_user).model_copy(
        update={
            "is_platform_admin": role is not None,
            "admin_role": role,
            "avatar_url": profile.avatar_url if profile else None,
            "full_name": full_name or None,
            "name_needs_choice": len(name_parts) < 2,
        }
    )
