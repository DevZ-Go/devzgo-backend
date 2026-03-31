from datetime import datetime, timedelta
from pathlib import Path
import logging
from jose import jwt
from passlib.context import CryptContext
from typing import Optional

import os

try:
    # Load devzgo-backend/.env (if it exists) so SECRET_KEY can be configured.
    # security.py lives in devzgo-backend/app/core/, so parents[2] is devzgo-backend/
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:
    # If dotenv isn't available or .env is missing, we still want the app to run in dev.
    pass

SECRET_KEY = (os.getenv("SECRET_KEY") or "").strip()  # change via env for real deployments
if not SECRET_KEY:
    SECRET_KEY = "dev-insecure-secret-change-me"
    logging.warning(
        "SECRET_KEY is not set. Using a dev fallback secret. Set SECRET_KEY in devzgo-backend/.env for production."
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)