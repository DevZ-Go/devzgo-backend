"""The signed-in user's GitHub App connection. Tokens stay encrypted and never leave this module's return value."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.workspace import GitHubConnection
from app.services import crypto, github_app as gh


class GitHubAccountTaken(Exception):
    """This GitHub account is already linked to a different DevZ-Go user."""


def status_of(row: GitHubConnection | None) -> dict:
    if row is None:
        return {"connected": False, "configured": gh.configured() and crypto.configured(), "status": None, "login": None}
    return {
        "connected": row.status == "active",
        "configured": True,
        "status": row.status,
        "login": row.login,
        "expires_at": row.access_expires_at,
    }


def save_tokens(db: Session, user: User, body: dict, profile: dict) -> GitHubConnection:
    github_id = profile.get("id")
    if github_id is not None:
        taken = db.query(GitHubConnection).filter(
            GitHubConnection.github_user_id == int(github_id),
            GitHubConnection.user_id != user.id,
        ).first()
        if taken is not None:
            raise GitHubAccountTaken()
    row = db.query(GitHubConnection).filter_by(user_id=user.id).one_or_none()
    if row is None:
        row = GitHubConnection(user_id=user.id)
        db.add(row)
    row.github_user_id = int(github_id) if github_id is not None else None
    row.login = (profile.get("login") or "")[:100] or None
    row.access_token_enc = crypto.encrypt(body["access_token"])
    refresh = body.get("refresh_token")
    row.refresh_token_enc = crypto.encrypt(refresh) if refresh else None
    row.access_expires_at = gh.expires_at(body.get("expires_in"))
    row.refresh_expires_at = gh.expires_at(body.get("refresh_token_expires_in"))
    row.status = "active"
    row.last_error = None
    db.commit()
    db.refresh(row)
    return row


def access_token(db: Session, user: User) -> str:
    """A live user access token, refreshed once when it is close to expiry."""
    if not crypto.configured():
        raise HTTPException(status_code=503, detail="Editing isn't configured on this server.")
    row = db.query(GitHubConnection).filter_by(user_id=user.id).one_or_none()
    if row is None or row.status == "revoked" or not row.access_token_enc:
        raise HTTPException(status_code=409, detail="Connect GitHub for editing first.")
    soon = datetime.now(timezone.utc) + timedelta(minutes=2)
    expires = row.access_expires_at
    if expires is not None and expires <= soon:
        if not row.refresh_token_enc:
            row.status, row.last_error = "expired", "GitHub authorization expired."
            db.commit()
            raise HTTPException(status_code=401, detail="GitHub authorization expired. Connect GitHub again.")
        try:
            body = gh.refresh(crypto.decrypt(row.refresh_token_enc))
            profile = gh.user(body["access_token"])
        except (gh.GitHubError, crypto.EncryptionNotConfigured):
            row.status, row.last_error = "expired", "GitHub authorization expired."
            db.commit()
            raise HTTPException(status_code=401, detail="GitHub authorization expired. Connect GitHub again.")
        save_tokens(db, user, body, profile)
        return body["access_token"]
    try:
        return crypto.decrypt(row.access_token_enc)
    except crypto.EncryptionNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def disconnect(db: Session, user: User) -> None:
    row = db.query(GitHubConnection).filter_by(user_id=user.id).one_or_none()
    if row is None:
        return
    token = None
    try:
        token = crypto.decrypt(row.access_token_enc) if row.access_token_enc else None
    except crypto.EncryptionNotConfigured:
        token = None
    if token:
        gh.revoke(token)
    db.delete(row)
    db.commit()
