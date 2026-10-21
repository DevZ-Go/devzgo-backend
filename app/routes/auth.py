import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.user import (
    ChangePasswordRequest, ForgotPasswordRequest, ResetPasswordRequest, UserCreate, UserResponse, UsernameAvailability,
)
from app.db.session import get_db
from app.models.user import User
from app.models.password_reset import PasswordResetToken
from app.core.policy import normalize_username, username_problem
from app.core.security import hash_password, verify_password, issue_token
from app.core.dependencies import get_current_user
from app.services.email import send_email

router = APIRouter(prefix="/auth", tags=["Auth"])

RESET_TTL = timedelta(minutes=30)
RESET_COOLDOWN = timedelta(minutes=1)


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def username_taken(db: Session, username: str) -> bool:
    return db.query(User.id).filter(func.lower(User.username) == username.lower()).first() is not None


def save_new_user(db: Session, user: User) -> User:
    """Insert, turning a uniqueness race into the same 409 the pre-checks give."""
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username or email already taken")
    db.refresh(user)
    return user


@router.get("/username-available", response_model=UsernameAvailability)
def username_available(username: str = Query(max_length=64), db: Session = Depends(get_db)):
    name = normalize_username(username)
    reason = username_problem(name) or ("taken" if username_taken(db, name) else None)
    return {"username": name, "available": reason is None, "reason": reason}


@router.post("/register", response_model=UserResponse)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    email = user_data.email.strip().lower()
    if db.query(User.id).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    if username_taken(db, user_data.username):
        raise HTTPException(status_code=409, detail="Username already taken")
    return save_new_user(db, User(username=user_data.username, email=email, password_hash=hash_password(user_data.password)))


@router.post("/change-password")
def change_password(data: ChangePasswordRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Change the password, or set a first one for a GitHub-only account. Other sessions are signed out."""
    if user.password_hash and not verify_password(data.current_password or "", user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(data.new_password)
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    return {"message": "Password updated.", "access_token": issue_token(user), "token_type": "bearer"}


@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(func.lower(User.email) == form_data.username.strip().lower()).first()

    if not user or not user.password_hash or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return {"access_token": issue_token(user), "token_type": "bearer"}


@router.post("/forgot-password")
def forgot_password(data: ForgotPasswordRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Always the same answer, so it cannot be used to find out which emails have accounts."""
    reply = {"message": "If that email has an account, a reset link is on its way."}
    user = db.query(User).filter(func.lower(User.email) == data.email.strip().lower()).first()
    if not user:
        return reply

    now = datetime.now(timezone.utc)
    if db.query(PasswordResetToken.id).filter(
        PasswordResetToken.user_id == user.id, PasswordResetToken.created_at > now - RESET_COOLDOWN
    ).first():
        return reply  # at most one email per minute per account

    token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(user_id=user.id, token_hash=_sha256(token), expires_at=now + RESET_TTL))
    db.commit()

    link = f"{os.getenv('FRONTEND_URL', 'http://localhost:5173').rstrip('/')}/reset-password?token={token}"
    html = (
        f"<p>Hi {escape(user.username)},</p><p>Reset your DevZ-Go password with the link below. "
        f"It expires in 30 minutes and works once.</p><p><a href=\"{link}\">Reset password</a></p>"
        "<p>If you did not ask for this, you can ignore this email.</p>"
    )
    # Sent after the response so response time does not reveal whether the account exists.
    background.add_task(send_email, user.email, "Reset your DevZ-Go password", html, f"Reset link: {link}")
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
    db.commit()
    return {"message": "Password updated. You can sign in now."}


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    return current_user
