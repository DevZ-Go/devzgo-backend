"""
GitHub repository import: OAuth with the `repo` scope, repo listing, archive download, and metadata mapping.

The access token stays on the server, in memory, under a random handle tied to the DevZ-Go user.
Handles expire after SESSION_TTL of inactivity; closing or expiring a handle revokes the token at GitHub.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from pathlib import Path
from uuid import UUID

import httpx

from app.models.enums import ProjectCategory
from app.services.workspace_extract import MAX_UPLOAD_BYTES

API = "https://api.github.com"
SCOPE = "repo read:user"
SESSION_TTL = 15 * 60
MAX_REPO_PAGES = 5

_sessions: dict[str, tuple[UUID, str, float]] = {}
_lock = threading.Lock()


class ArchiveTooLarge(Exception):
    pass


STATE_PURPOSE = "github_import"


def import_callback_url() -> str:
    # An OAuth App only accepts its registered callback URL, so import shares the sign-in callback
    # and is told apart by the purpose inside the signed state.
    return os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/") + "/auth/github/callback"


def configured() -> bool:
    return bool(os.getenv("GITHUB_CLIENT_ID") and os.getenv("GITHUB_CLIENT_SECRET"))


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API,
        timeout=30,
        follow_redirects=True,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )


def exchange_code(code: str) -> str:
    token = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": os.getenv("GITHUB_CLIENT_ID"),
            "client_secret": os.getenv("GITHUB_CLIENT_SECRET"),
            "code": code,
            "redirect_uri": import_callback_url(),
        },
        timeout=10,
    ).json().get("access_token")
    if not token:
        raise ValueError("GitHub did not accept the sign-in. Start the import again.")
    return token


def revoke_token(token: str) -> None:
    client_id, secret = os.getenv("GITHUB_CLIENT_ID"), os.getenv("GITHUB_CLIENT_SECRET")
    if not (client_id and secret):
        return
    try:
        httpx.request("DELETE", f"{API}/applications/{client_id}/token", auth=(client_id, secret),
                      json={"access_token": token}, headers={"Accept": "application/vnd.github+json"}, timeout=10)
    except httpx.HTTPError:
        pass


def _purge_expired() -> list[str]:
    now = time.monotonic()
    stale = [h for h, (_, _, exp) in _sessions.items() if exp < now]
    return [_sessions.pop(h)[1] for h in stale]


def open_session(user_id: UUID, token: str) -> str:
    handle = secrets.token_urlsafe(32)
    with _lock:
        stale = _purge_expired()
        _sessions[handle] = (user_id, token, time.monotonic() + SESSION_TTL)
    for old in stale:
        revoke_token(old)
    return handle


def session_token(handle: str, user_id: UUID) -> str | None:
    """The token behind a live handle owned by this user; each use extends the handle's life."""
    with _lock:
        stale = _purge_expired()
        entry = _sessions.get(handle)
        token = None
        if entry and entry[0] == user_id:
            token = entry[1]
            _sessions[handle] = (user_id, token, time.monotonic() + SESSION_TTL)
    for old in stale:
        revoke_token(old)
    return token


def close_session(handle: str, user_id: UUID) -> None:
    with _lock:
        entry = _sessions.get(handle)
        if not entry or entry[0] != user_id:
            return
        del _sessions[handle]
    revoke_token(entry[1])


def github_login(token: str) -> str:
    with _client(token) as gh:
        return gh.get("/user").raise_for_status().json()["login"]


def list_repos(token: str) -> list[dict]:
    repos: list[dict] = []
    with _client(token) as gh:
        for page in range(1, MAX_REPO_PAGES + 1):
            batch = gh.get("/user/repos", params={
                "per_page": 100, "page": page, "sort": "updated",
                "affiliation": "owner,collaborator,organization_member",
            }).raise_for_status().json()
            repos.extend(batch)
            if len(batch) < 100:
                break
    return repos


def get_repo(token: str, repo_id: int) -> dict | None:
    with _client(token) as gh:
        res = gh.get(f"/repositories/{repo_id}")
        if res.status_code == 404:
            return None
        return res.raise_for_status().json()


def repo_by_name(token: str, full_name: str) -> dict | None:
    with _client(token) as gh:
        res = gh.get(f"/repos/{full_name}")
        if res.status_code == 404:
            return None
        return res.raise_for_status().json()


def repo_activity(token: str, full_name: str) -> dict:
    """Commit total and recent push events. Missing permission or an empty repo leaves the counts unset."""
    commits = pushes = None
    try:
        with _client(token) as gh:
            res = gh.get(f"/repos/{full_name}/commits", params={"per_page": 1})
            if res.status_code == 200:
                match = re.search(r'page=(\d+)>;\s*rel="last"', res.headers.get("link", ""))
                commits = int(match.group(1)) if match else (1 if res.json() else 0)
            events = gh.get(f"/repos/{full_name}/events", params={"per_page": 100})
            if events.status_code == 200 and isinstance(events.json(), list):
                pushes = sum(1 for event in events.json() if event.get("type") == "PushEvent")
    except httpx.HTTPError:
        pass
    return {"commits": commits, "pushes": pushes}


def get_readme(token: str, full_name: str) -> str | None:
    with _client(token) as gh:
        res = gh.get(f"/repos/{full_name}/readme", headers={"Accept": "application/vnd.github.raw+json"})
        if res.status_code != 200:
            return None
        return res.text


def download_archive(token: str, full_name: str, ref: str, dest: Path) -> bool:
    """Save the repo's zip archive to dest. False when the repo has no commits yet."""
    with _client(token) as gh, gh.stream("GET", f"/repos/{full_name}/zipball/{ref}") as res:
        if res.status_code in (404, 409):
            return False
        res.raise_for_status()
        written = 0
        with open(dest, "wb") as out:
            for chunk in res.iter_bytes(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise ArchiveTooLarge()
                out.write(chunk)
    return True


def summarize_repo(repo: dict) -> dict:
    owner = repo.get("owner") or {}
    return {
        "id": repo["id"],
        "name": repo["name"],
        "full_name": repo["full_name"],
        "owner": owner.get("login"),
        "description": repo.get("description"),
        "private": bool(repo.get("private")),
        "fork": bool(repo.get("fork")),
        "archived": bool(repo.get("archived")),
        "language": repo.get("language"),
        "stars": repo.get("stargazers_count") or 0,
        "size_kb": repo.get("size") or 0,
        "updated_at": repo.get("pushed_at") or repo.get("updated_at"),
        "html_url": repo["html_url"],
    }


_CATEGORY_HINTS: list[tuple[ProjectCategory, set[str], set[str]]] = [
    (ProjectCategory.AI_ML,
     {"ai", "ml", "machine-learning", "deep-learning", "llm", "nlp", "pytorch", "tensorflow", "data-science",
      "computer-vision", "neural-network", "openai", "langchain", "chatbot"},
     {"Jupyter Notebook"}),
    (ProjectCategory.GAMING,
     {"game", "games", "gamedev", "unity", "godot", "pygame", "unreal-engine", "game-development"},
     {"GDScript", "ShaderLab"}),
    (ProjectCategory.Mobile,
     {"android", "ios", "flutter", "react-native", "mobile", "mobile-app", "swiftui", "expo"},
     {"Swift", "Kotlin", "Dart", "Objective-C"}),
    (ProjectCategory.EDUCATION,
     {"education", "learning", "course", "tutorial", "homework", "assignment", "school", "university"},
     set()),
    (ProjectCategory.PRODUCTIVITY,
     {"productivity", "cli", "automation", "tool", "tools", "extension", "chrome-extension", "todo", "notes"},
     set()),
    (ProjectCategory.WEB_DEVELOPMENT,
     {"web", "website", "react", "nextjs", "vue", "angular", "svelte", "frontend", "backend", "api", "django",
      "flask", "fastapi", "express", "nodejs", "webapp", "portfolio"},
     {"HTML", "CSS", "JavaScript", "TypeScript", "PHP", "Vue", "Svelte", "SCSS"}),
]


def guess_category(repo: dict) -> ProjectCategory:
    topics = {t.lower() for t in repo.get("topics") or []}
    words = set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", f"{repo.get('name', '')} {repo.get('description') or ''}".lower()))
    for found in (topics, words):
        for category, hints, _ in _CATEGORY_HINTS:
            if found & hints:
                return category
    language = repo.get("language")
    for category, _, languages in _CATEGORY_HINTS:
        if language in languages:
            return category
    return ProjectCategory.OTHER


def readme_to_text(markdown: str, limit: int = 4000) -> str:
    """README markdown flattened to readable plain text for the project description."""
    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)[ \t]*", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*\[[^\]]+\]:\s*\S+.*$", "", text, flags=re.M)
    text = re.sub(r"^```.*$", "", text, flags=re.M)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip() + "…"
    return text
