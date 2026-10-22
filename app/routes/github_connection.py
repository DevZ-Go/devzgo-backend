"""Connect and disconnect the GitHub App used for editing. Separate from sign-in and from import."""

import os
import secrets
from datetime import datetime, timedelta, timezone

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_developer
from app.core.rate_limit import rate_limit
from app.core.security import ALGORITHM, SECRET_KEY
from app.db.session import get_db
from app.models.user import User
from app.models.workspace import WorkspaceAuditEvent
from app.services import crypto, github_app as gh
from app.services.github_connection import disconnect, save_tokens, status_of
from app.models.workspace import GitHubConnection

router = APIRouter(prefix="/github/connection", tags=["GitHub connection"])
COOKIE = "gh_app_state"


def _front(query: str) -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/") + "/settings?section=github&" + query


def _audit(db: Session, user: User, action: str, outcome: str, error: str | None = None) -> None:
    db.add(WorkspaceAuditEvent(user_id=user.id, action=action, outcome=outcome, error_code=error))
    db.commit()


@router.get("")
def connection(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    row = db.query(GitHubConnection).filter_by(user_id=me.id).one_or_none()
    return status_of(row)


@router.post("/authorize", dependencies=[rate_limit("github_connect", 10, 60)])
def authorize(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    if not gh.configured() or not crypto.configured():
        raise HTTPException(status_code=503, detail="Editing isn't configured on this server.")
    nonce = secrets.token_urlsafe(24)
    state = jwt.encode(
        {"purpose": "github_app", "sub": str(me.id), "nonce": nonce, "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        SECRET_KEY, algorithm=ALGORITHM,
    )
    ready = JSONResponse({"url": gh.authorize_url(state)})
    ready.set_cookie(COOKIE, nonce, max_age=600, httponly=True, samesite="lax", secure=gh.callback_url().startswith("https://"))
    return ready


@router.get("/callback", include_in_schema=False)
def callback(request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    failed = RedirectResponse(_front("error=denied" if error else "error=1"))
    try:
        claims = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return failed
    if claims.get("purpose") != "github_app" or claims.get("nonce") != request.cookies.get(COOKIE) or not code:
        return failed
    try:
        user_id = UUID(str(claims.get("sub")))
    except ValueError:
        return failed
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None or user.account_type == "recruiter":
        return failed
    try:
        body = gh.exchange_code(code)
        profile = gh.user(body["access_token"])
        save_tokens(db, user, body, profile)
        _audit(db, user, "connect", "ok")
    except (gh.GitHubError, crypto.EncryptionNotConfigured):
        return failed
    done = RedirectResponse(_front("connected=1"))
    done.delete_cookie(COOKIE)
    return done


@router.delete("", dependencies=[rate_limit("github_connect", 10, 60)])
def remove(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    disconnect(db, me)
    _audit(db, me, "disconnect", "ok")
    return {"connected": False}


callback_router = APIRouter()


@callback_router.get("/github/app/callback", include_in_schema=False)
def app_callback(request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    """The callback registered on the GitHub App. Same handler as /github/connection/callback."""
    return callback(request, code, state, error, db)
