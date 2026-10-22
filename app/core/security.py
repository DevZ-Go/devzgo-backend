from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext
from typing import Optional

import os
SECRET_KEY = os.getenv("SECRET_KEY")  # change later via env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    """bcrypt stores a fresh salt in every hash, so the same password never repeats a hash."""
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

def issue_token(user) -> str:
    """Login token; `ver` ties it to the current password so changing it signs out other sessions."""
    return create_access_token({"sub": str(user.id), "ver": user.token_version or 0})
