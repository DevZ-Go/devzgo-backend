"""GitHub App user-to-server tokens.

The token is the signed-in person's own access, so GitHub enforces the App's permissions, the
repositories they installed it on, and their own rights. Installation tokens are not used.
Nothing here logs a token, and error text has Authorization headers removed.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import httpx

API = "https://api.github.com"
TOKEN_URL = "https://github.com/login/oauth/access_token"
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_SECRET = re.compile(r"(ghu_|gho_|github_pat_)[A-Za-z0-9_]+|Bearer [A-Za-z0-9._\-]+")


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def configured() -> bool:
    return bool(os.getenv("GITHUB_APP_CLIENT_ID") and os.getenv("GITHUB_APP_CLIENT_SECRET") and os.getenv("GITHUB_APP_SLUG"))


def callback_url() -> str:
    return os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/") + "/github/app/callback"


def authorize_url(state: str) -> str:
    from urllib.parse import urlencode
    query = urlencode({
        "client_id": os.getenv("GITHUB_APP_CLIENT_ID"),
        "redirect_uri": callback_url(),
        "state": state,
        "allow_signup": "false",
    })
    return f"{AUTHORIZE_URL}?{query}"


def scrub(text: str) -> str:
    return _SECRET.sub("[redacted]", text)[:300]


def _map(response: httpx.Response) -> GitHubError:
    status = response.status_code
    if status in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
        reset = response.headers.get("x-ratelimit-reset")
        when = ""
        if reset and reset.isdigit():
            when = datetime.fromtimestamp(int(reset), timezone.utc).strftime(" Try again at %H:%M UTC.")
        return GitHubError(status, f"GitHub rate limit.{when}")
    if status == 401:
        return GitHubError(401, "GitHub authorization expired. Connect GitHub again.")
    if status == 403:
        return GitHubError(403, "GitHub did not allow that.")
    if status == 404:
        return GitHubError(404, "GitHub could not find that repository or branch.")
    if status in (409, 422):
        detail = ""
        try:
            body = response.json()
            detail = body.get("message") if isinstance(body, dict) else ""
        except ValueError:
            detail = ""
        text = scrub(detail or "GitHub rejected the change. The branch may be protected, or the file changed.")
        return GitHubError(status, text)
    if status >= 500:
        return GitHubError(status, "GitHub is unavailable. Try again in a moment.")
    return GitHubError(status, "GitHub did not accept that request.")


def _request(method: str, url: str, token: str | None = None, **kwargs) -> httpx.Response:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(kwargs.pop("headers", {}) or {})
    try:
        response = httpx.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 20), **kwargs)
    except httpx.HTTPError as exc:
        raise GitHubError(503, "GitHub is unavailable. Try again in a moment.") from exc
    if response.status_code >= 400:
        raise _map(response)
    return response


def exchange_code(code: str) -> dict:
    try:
        response = httpx.post(
            TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": os.getenv("GITHUB_APP_CLIENT_ID"),
                "client_secret": os.getenv("GITHUB_APP_CLIENT_SECRET"),
                "code": code,
                "redirect_uri": callback_url(),
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise GitHubError(503, "GitHub is unavailable. Try again in a moment.") from exc
    body = response.json() if response.content else {}
    if response.status_code >= 400 or not body.get("access_token"):
        raise GitHubError(400, "GitHub did not accept the connection. Start it again.")
    return body


def refresh(refresh_token: str) -> dict:
    try:
        response = httpx.post(
            TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": os.getenv("GITHUB_APP_CLIENT_ID"),
                "client_secret": os.getenv("GITHUB_APP_CLIENT_SECRET"),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise GitHubError(503, "GitHub is unavailable. Try again in a moment.") from exc
    body = response.json() if response.content else {}
    if response.status_code >= 400 or not body.get("access_token"):
        raise GitHubError(401, "GitHub authorization expired. Connect GitHub again.")
    return body


def revoke(access_token: str) -> None:
    client_id, secret = os.getenv("GITHUB_APP_CLIENT_ID"), os.getenv("GITHUB_APP_CLIENT_SECRET")
    if not (client_id and secret and access_token):
        return
    try:
        httpx.request(
            "DELETE",
            f"{API}/applications/{client_id}/grant",
            auth=(client_id, secret),
            json={"access_token": access_token},
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
    except httpx.HTTPError:
        pass


def user(token: str) -> dict:
    return _request("GET", f"{API}/user", token).json()


def repo(token: str, full_name: str) -> dict:
    return _request("GET", f"{API}/repos/{full_name}", token).json()


def get(token: str, path: str, **kwargs) -> httpx.Response:
    return _request("GET", f"{API}{path}", token, **kwargs)


def send(token: str, method: str, path: str, **kwargs) -> httpx.Response:
    return _request(method, f"{API}{path}", token, **kwargs)


def expires_at(seconds: int | None) -> datetime | None:
    if not seconds:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(seconds))
