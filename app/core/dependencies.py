from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.user import User
import os

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

if not SECRET_KEY:
    import logging

    logging.getLogger("uvicorn.error").warning(
        "SECRET_KEY is not set — JWT validation will fail and every protected route returns 401. "
        "Set SECRET_KEY in your environment (e.g. .env)."
    )

# HTTPBearer (not OAuth2PasswordBearer) so Swagger UI shows a single field: paste the JWT
# from POST /auth/login (`access_token`). Same header as before: Authorization: Bearer <token>.
http_bearer = HTTPBearer()
http_bearer_optional = HTTPBearer(auto_error=False)


def user_from_token(db: Session, token: str | None) -> User | None:
    """The user of a valid login token, else None. Tokens issued before the last password change are rejected."""
    try:
        payload = jwt.decode(token or "", SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if not payload.get("sub") or payload.get("purpose"):  # purpose tokens (OAuth state, onboarding) never log in
        return None
    user = db.query(User).filter(User.id == payload["sub"]).first()
    return user if user and payload.get("ver", 0) == (user.token_version or 0) else None


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(http_bearer),
    db: Session = Depends(get_db),
):
    user = user_from_token(db, credentials.credentials)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user


def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer_optional),
    db: Session = Depends(get_db),
):
    """Same as get_current_user but returns None if no/invalid token (for optional auth routes)."""
    return user_from_token(db, credentials.credentials) if credentials else None