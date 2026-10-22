"""Read and write one linked repository with the caller's own GitHub token.

Every write re-checks that this token can push and that the repository id still matches the
project. Protected branches and rulesets are left to GitHub.
"""

from __future__ import annotations

import base64
import re
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.project import Project
from app.services import github_app as gh

BRANCH_MAX = 100
_BAD = re.compile(r"[\x00-\x1f\x7f ~^:?*\[\\]")


class GitDenied(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def branch_problem(name: str) -> str | None:
    """git check-ref-format, for a single branch name."""
    if not name or len(name) > BRANCH_MAX:
        return "Enter a branch name of at most 100 characters."
    if name.startswith(("-", "/", ".")) or name.endswith(("/", ".", ".lock")) or "@{" in name or ".." in name or "//" in name:
        return "That branch name isn't allowed."
    if _BAD.search(name) or any(part.startswith(".") or part.endswith(".lock") or not part for part in name.split("/")):
        return "That branch name isn't allowed."
    return None


def _full_name(project: Project) -> str:
    if not project.github_full_name:
        raise GitDenied(400, "This project is not linked to a GitHub repository.")
    return project.github_full_name


def require_push(project: Project, token: str) -> dict:
    """The repository as GitHub reports it to this token, after checking push and the stored id."""
    try:
        body = gh.repo(token, _full_name(project))
    except gh.GitHubError as exc:
        raise GitDenied(exc.status, exc.message) from exc
    if project.github_repo_id and body.get("id") != project.github_repo_id:
        raise GitDenied(409, "This repository no longer matches the linked GitHub repository.")
    if not (body.get("permissions") or {}).get("push"):
        raise GitDenied(403, "Your GitHub account can't push to this repository.")
    return body


def can_read(project: Project, token: str) -> dict:
    try:
        body = gh.repo(token, _full_name(project))
    except gh.GitHubError as exc:
        raise GitDenied(exc.status, exc.message) from exc
    if project.github_repo_id and body.get("id") != project.github_repo_id:
        raise GitDenied(409, "This repository no longer matches the linked GitHub repository.")
    return body


def remember_repo(db: Session, project: Project, body: dict) -> None:
    project.github_repo_id = project.github_repo_id or body.get("id")
    project.github_full_name = project.github_full_name or (body.get("full_name") or "")[:200]
    project.github_default_branch = project.github_default_branch or body.get("default_branch")
    if not project.github_tracked_branch:
        project.github_tracked_branch = body.get("default_branch")
    db.commit()


def branches(token: str, full_name: str, cap: int = 300) -> list[dict]:
    rows, page = [], 1
    while len(rows) < cap and page <= 3:
        try:
            batch = gh.get(token, f"/repos/{full_name}/branches", params={"per_page": 100, "page": page}).json()
        except gh.GitHubError as exc:
            raise GitDenied(exc.status, exc.message) from exc
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        page += 1
    return rows[:cap]


def tree(token: str, full_name: str, ref: str) -> dict:
    try:
        commit = gh.get(token, f"/repos/{full_name}/commits/{ref}", headers={"Accept": "application/vnd.github+json"}).json()
        sha = commit["sha"]
        body = gh.get(token, f"/repos/{full_name}/git/trees/{sha}", params={"recursive": 1}).json()
    except gh.GitHubError as exc:
        raise GitDenied(exc.status, exc.message) from exc
    except (KeyError, TypeError) as exc:
        raise GitDenied(502, "GitHub did not return a file tree.") from exc
    return {"sha": sha, "truncated": bool(body.get("truncated")), "entries": body.get("tree") or []}


def file_at(token: str, full_name: str, path: str, ref: str) -> dict:
    try:
        response = gh.get(token, f"/repos/{full_name}/contents/{path}", params={"ref": ref})
    except gh.GitHubError as exc:
        if exc.status == 404:
            return {"exists": False, "path": path, "sha": None, "content": "", "size": 0}
        raise GitDenied(exc.status, exc.message) from exc
    body = response.json()
    if isinstance(body, list):
        raise GitDenied(400, "Choose a file, not a folder.")
    encoded = body.get("content") or ""
    try:
        text = base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        text = None
    return {"exists": True, "path": path, "sha": body.get("sha"), "content": text, "size": body.get("size") or 0,
            "encoding": "utf-8" if text is not None else None}


def create_branch(token: str, full_name: str, name: str, from_sha: str) -> dict:
    problem = branch_problem(name)
    if problem:
        raise GitDenied(400, problem)
    try:
        body = gh.send(token, "POST", f"/repos/{full_name}/git/refs", json={"ref": f"refs/heads/{name}", "sha": from_sha}).json()
    except gh.GitHubError as exc:
        if exc.status == 422:
            raise GitDenied(422, "That branch already exists, or the name was rejected.") from exc
        raise GitDenied(exc.status, exc.message) from exc
    return {"name": name, "sha": (body.get("object") or {}).get("sha") or from_sha}


def commit_file(token: str, full_name: str, branch: str, path: str, content: str, base_sha: str | None, message: str) -> dict:
    payload = {"message": message[:400], "content": base64.b64encode(content.encode()).decode(), "branch": branch}
    if base_sha:
        payload["sha"] = base_sha
    try:
        current = file_at(token, full_name, path, branch)
        if current["exists"] and current["sha"] and current["sha"] != base_sha:
            raise GitDenied(409, "This file changed on GitHub. Refresh it before committing.")
        if not current["exists"] and base_sha:
            raise GitDenied(409, "This file changed on GitHub. Refresh it before committing.")
        body = gh.send(token, "PUT", f"/repos/{full_name}/contents/{path}", json=payload).json()
    except gh.GitHubError as exc:
        raise GitDenied(exc.status, exc.message) from exc
    commit = body.get("commit") or {}
    return {"sha": commit.get("sha"), "url": commit.get("html_url"), "branch": branch, "path": path}


def commit_files(token: str, full_name: str, branch: str, files: list[dict], message: str, parent_sha: str) -> dict:
    """One commit for several files, fast-forward only. `files` items have path, content and base_blob_sha."""
    try:
        ref = gh.get(token, f"/repos/{full_name}/git/ref/heads/{branch}").json()
        head = (ref.get("object") or {}).get("sha")
        if head != parent_sha:
            raise GitDenied(409, "This branch changed on GitHub. Refresh it before committing.")
        commit = gh.get(token, f"/repos/{full_name}/git/commits/{head}").json()
        tree_sha = (commit.get("tree") or {}).get("sha")
        tree_items = []
        for item in files:
            current = file_at(token, full_name, item["path"], branch)
            expected = item.get("base_blob_sha")
            if (current["sha"] or None) != (expected or None):
                raise GitDenied(409, f"{item['path']} changed on GitHub. Refresh it before committing.")
            blob = gh.send(token, "POST", f"/repos/{full_name}/git/blobs",
                           json={"content": item["content"], "encoding": "utf-8"}).json()
            tree_items.append({"path": item["path"], "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = gh.send(token, "POST", f"/repos/{full_name}/git/trees", json={"base_tree": tree_sha, "tree": tree_items}).json()
        made = gh.send(token, "POST", f"/repos/{full_name}/git/commits",
                       json={"message": message[:400], "tree": tree["sha"], "parents": [head]}).json()
        gh.send(token, "PATCH", f"/repos/{full_name}/git/refs/heads/{branch}", json={"sha": made["sha"], "force": False})
    except gh.GitHubError as exc:
        raise GitDenied(exc.status, exc.message) from exc
    return {"sha": made["sha"], "url": made.get("html_url"), "branch": branch}


def compare(token: str, full_name: str, base: str, head: str) -> dict:
    try:
        body = gh.get(token, f"/repos/{full_name}/compare/{base}...{head}").json()
    except gh.GitHubError as exc:
        raise GitDenied(exc.status, exc.message) from exc
    files = [{"path": row.get("filename"), "status": row.get("status"), "additions": row.get("additions"),
              "deletions": row.get("deletions")} for row in (body.get("files") or [])[:100]]
    return {"status": body.get("status"), "ahead_by": body.get("ahead_by"), "behind_by": body.get("behind_by"),
            "files": files, "truncated": len(body.get("files") or []) > 100}


def open_pull(token: str, full_name: str, title: str, body: str, head: str, base: str) -> dict:
    try:
        made = gh.send(token, "POST", f"/repos/{full_name}/pulls",
                       json={"title": title[:200], "body": body[:20000], "head": head, "base": base}).json()
    except gh.GitHubError as exc:
        raise GitDenied(exc.status, exc.message) from exc
    return {"number": made.get("number"), "url": made.get("html_url"), "state": made.get("state")}
