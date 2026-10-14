import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.schemas.user import ForgotPasswordRequest, ResetPasswordRequest, UserCreate, UserResponse
from app.db.session import get_db
from app.models.user import User
from app.models.password_reset import PasswordResetToken
from app.core.security import hash_password, verify_password, create_access_token
from app.core.dependencies import get_current_user
from app.services.email import send_email

router = APIRouter(prefix="/auth", tags=["Auth"])

RESET_TTL = timedelta(minutes=30)
RESET_COOLDOWN = timedelta(minutes=1)


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/register", response_model=UserResponse)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    email = user_data.email.strip().lower()
    if db.query(User.id).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    if db.query(User.id).filter(func.lower(User.username) == user_data.username.lower()).first():
        raise HTTPException(status_code=400, detail="Username already taken")

    new_user = User(
        username=user_data.username,
        email=email,
        password_hash=hash_password(user_data.password)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return new_user


@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(func.lower(User.email) == form_data.username.strip().lower()).first()

    if not user or not user.password_hash or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = create_access_token(data={"sub": str(user.id)})

    return {
        "access_token": access_token,
        "token_type": "bearer"
    }


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
    # Using one link retires every other outstanding link for this account.
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)
    ).update({"used_at": now})
    db.commit()
    return {"message": "Password updated. You can sign in now."}


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    return current_user
