"""
Sign in with GitHub (OAuth web flow).

/auth/github/login -> GitHub consent -> /auth/github/callback -> FRONTEND_URL/auth/callback#token=<JWT>
Repository import shares /auth/github/callback; its signed state carries purpose "github_import".
New GitHub users go to FRONTEND_URL/auth/github/onboarding#token=<onboarding token>&suggested=<name> instead,
choose a username, and POST /auth/github/complete to create the account.
Tokens travel in the URL fragment, which browsers never send to servers or put in Referer headers.
"""

import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.policy import username_problem
from app.core.security import ALGORITHM, SECRET_KEY, issue_token
from app.db.session import get_db
from app.models.profile import UserProfile
from app.models.user import User
from app.routes.auth import save_new_user, username_taken
from app.routes.github_import import import_redirect
from app.services.github_import import STATE_PURPOSE as IMPORT_PURPOSE
from app.services.roles import assign_identity
from app.schemas.user import GitHubCompleteRequest

router = APIRouter(prefix="/auth/github", tags=["Auth"])

STATE_COOKIE = "gh_oauth_state"
ONBOARDING_TTL = timedelta(minutes=15)


def _frontend(path: str) -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/") + path


def _callback_url() -> str:
    return os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/") + "/auth/github/callback"


def _fail(reason: str) -> RedirectResponse:
    response = RedirectResponse(_frontend(f"/login?error={reason}"))
    response.delete_cookie(STATE_COOKIE)
    return response


def fetch_github_identity(code: str) -> dict:
    """Exchange the OAuth code and return GitHub id, login, name, avatar, profile URL and verified primary email."""
    token = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": os.getenv("GITHUB_CLIENT_ID"),
            "client_secret": os.getenv("GITHUB_CLIENT_SECRET"),
            "code": code,
            "redirect_uri": _callback_url(),
        },
        timeout=10,
    ).json().get("access_token")
    if not token:
        raise ValueError("github")
    with httpx.Client(base_url="https://api.github.com", timeout=10,
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}) as gh:
        user = gh.get("/user").raise_for_status().json()
        emails = gh.get("/user/emails").raise_for_status().json()
    primary = next((e["email"] for e in emails if e.get("primary") and e.get("verified")), None)
    return {"id": str(user["id"]), "login": user["login"], "name": user.get("name"),
            "avatar_url": user.get("avatar_url"), "html_url": user.get("html_url"), "email": primary}


def suggest_username(db: Session, login: str) -> str:
    """A free, valid username close to the GitHub login, offered as the onboarding default."""
    base = re.sub(r"[^a-z0-9_.-]", "", login.lower()).strip(".-")[:26]
    base = base if len(base) >= 3 and not username_problem(base) else f"dev{base}"[:26].strip(".-")
    name, n = base, 1
    while username_taken(db, name):
        n += 1
        name = f"{base}{n}"
    return name


def _find_user(db: Session, gh: dict) -> User | None:
    user = db.query(User).filter(User.github_id == gh["id"]).first()
    if not user and gh["email"]:
        # Link only through an email GitHub has verified, so nobody can claim another person's account.
        user = db.query(User).filter(func.lower(User.email) == gh["email"].lower()).first()
        if user and user.github_id:
            raise ValueError("github_linked")  # that account belongs to a different GitHub user
        if user:
            user.github_id = gh["id"]
    if not user and not gh["email"]:
        raise ValueError("github_email")
    return user


def _fill_profile(db: Session, user: User, gh: dict) -> None:
    profile = user.profile or UserProfile(user_id=user.id)
    if not user.profile:
        db.add(profile)
        user.profile = profile
    profile.github_url = profile.github_url or gh["html_url"]
    profile.avatar_url = profile.avatar_url or gh["avatar_url"]
    profile.full_name = profile.full_name or gh["name"]
    db.commit()


@router.get("/login", include_in_schema=False)
def github_login():
    client_id = os.getenv("GITHUB_CLIENT_ID")
    if not client_id or not os.getenv("GITHUB_CLIENT_SECRET"):
        raise HTTPException(status_code=503, detail="GitHub login is not configured")
    nonce = secrets.token_urlsafe(24)
    state = jwt.encode(
        {"nonce": nonce, "purpose": "github", "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        SECRET_KEY, algorithm=ALGORITHM,
    )
    query = urlencode({"client_id": client_id, "redirect_uri": _callback_url(), "scope": "read:user user:email", "state": state})
    response = RedirectResponse(f"https://github.com/login/oauth/authorize?{query}")
    # The signed state must match this browser's cookie, so a login link cannot be replayed in someone else's browser.
    response.set_cookie(STATE_COOKIE, nonce, max_age=600, httponly=True, samesite="lax",
                        secure=_callback_url().startswith("https://"))
    return response


@router.get("/callback", include_in_schema=False)
def github_callback(request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    try:
        claims = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return _fail("github_state")
    if claims.get("purpose") == IMPORT_PURPOSE:
        return import_redirect(code, state, error)
    if claims.get("purpose") != "github" or claims.get("nonce") != request.cookies.get(STATE_COOKIE) or not code:
        return _fail("github_state")

    try:
        gh = fetch_github_identity(code)
        user = _find_user(db, gh)
    except ValueError as exc:
        db.rollback()
        return _fail(str(exc) if str(exc).startswith("github_") else "github")
    except httpx.HTTPError:
        return _fail("github")

    if user:
        _fill_profile(db, user, gh)
        target = f"/auth/callback#token={issue_token(user)}"
    else:  # first visit: the account is created once the person picks a username
        onboarding = jwt.encode({"purpose": "github_onboarding", "gh": gh, "exp": datetime.now(timezone.utc) + ONBOARDING_TTL},
                                SECRET_KEY, algorithm=ALGORITHM)
        target = f"/auth/github/onboarding#{urlencode({'token': onboarding, 'suggested': suggest_username(db, gh['login'])})}"
    response = RedirectResponse(_frontend(target))
    response.delete_cookie(STATE_COOKIE)
    return response


@router.post("/complete")
def github_complete(data: GitHubCompleteRequest, db: Session = Depends(get_db)):
    try:
        claims = jwt.decode(data.onboarding_token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        claims = {}
    if claims.get("purpose") != "github_onboarding":
        raise HTTPException(status_code=400, detail="This sign-up link has expired. Sign in with GitHub again.")
    if data.role == "Recruiter" and len(" ".join((data.company or "").split())) < 2:
        raise HTTPException(status_code=400, detail="Add the company or agency you recruit for")
    gh = claims["gh"]
    user = db.query(User).filter(User.github_id == gh["id"]).first()  # completed already in another tab
    if not user:
        if username_taken(db, data.username):
            raise HTTPException(status_code=409, detail="Username already taken")
        if db.query(User.id).filter(func.lower(User.email) == gh["email"].lower()).first():
            raise HTTPException(status_code=409, detail="Email already registered")
        user = save_new_user(db, User(username=data.username, email=gh["email"].lower(), github_id=gh["id"], password_hash=None))
    _fill_profile(db, user, gh)
    if data.role or data.company:
        db.refresh(user)
        profile = user.profile or UserProfile(user_id=user.id)
        if user.profile is None:
            db.add(profile)
            user.profile = profile
        assign_identity(profile, data.role, data.company, company_sent=data.company is not None or data.role == "Recruiter", role_sent=data.role is not None)
        db.commit()
    return {"access_token": issue_token(user), "token_type": "bearer"}
