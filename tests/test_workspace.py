"""Drafts, commits, pull requests and sync. GitHub is mocked; nothing here talks to a real repository."""

from uuid import UUID

import pytest

from app.db.session import SessionLocal
from app.models.project import Project
from app.models.workspace import GitHubConnection, WorkspaceDraft, WorkspaceSyncJob
from app.services import crypto, github_app
from app.services.workspace_git import branch_problem
import app.routes.workspace as ws


def test_branch_names_and_token_scrubbing():
    assert branch_problem("feature/editor") is None
    for name in ("../x", "-wip", "a.lock", "has space", "a@{1}", "a..b", "a//b"):
        assert branch_problem(name)
    assert "gho_secretvalue" not in github_app.scrub("denied gho_secretvalue for Bearer abc.def")
    assert "[redacted]" in github_app.scrub("gho_secretvalue")


def _project(client, headers, **fields):
    pid = client.post("/projects", headers=headers, json={"title": "Edit", "category": "Gaming", "visibility": "Private"}).json()["id"]
    if fields:
        with SessionLocal() as db:
            row = db.query(Project).filter(Project.id == pid).one()
            for key, value in fields.items():
                setattr(row, key, value)
            db.commit()
    return pid


def _connect(user_id: str):
    with SessionLocal() as db:
        db.add(GitHubConnection(
            user_id=UUID(user_id), login="octo", status="active", github_user_id=1,
            access_token_enc=crypto.encrypt("gho_testtoken"),
        ))
        db.commit()


def _invite(client, owner, pid, member, access):
    name = client.get("/auth/me", headers=member).json()["username"]
    inv = client.post(f"/collaborations/projects/{pid}/invitations", headers=owner, json={"username": name, "access": access}).json()
    assert client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=member).status_code == 200


@pytest.fixture
def github(monkeypatch):
    calls = {"commit": 0, "commits": 0, "pull": 0, "job": 0}

    def repo(_token, name):
        return {"id": 42, "full_name": name, "default_branch": "main", "permissions": {"push": True}}

    monkeypatch.setattr(ws, "require_push", lambda project, token: repo(token, project.github_full_name))
    monkeypatch.setattr(ws, "can_read", lambda project, token: repo(token, project.github_full_name))
    monkeypatch.setattr(ws, "branches", lambda token, full: [{"name": "main", "commit": {"sha": "a" * 40}, "protected": False}])
    monkeypatch.setattr(ws, "tree", lambda token, full, ref: {"sha": "a" * 40, "truncated": False, "entries": [{"path": "a.txt", "type": "blob"}]})
    monkeypatch.setattr(ws, "file_at", lambda token, full, path, ref: {"exists": True, "path": path, "sha": "b" * 40, "content": "old\n", "size": 4})
    monkeypatch.setattr(ws, "run_job", lambda job_id: calls.__setitem__("job", calls["job"] + 1))

    def one(*_args, **_kwargs):
        calls["commit"] += 1
        return {"sha": "c" * 40, "url": "https://github.com/octo/shop/commit/c", "branch": "main", "path": "a.txt"}

    def many(*_args, **_kwargs):
        calls["commits"] += 1
        return {"sha": "d" * 40, "url": "https://github.com/octo/shop/commit/d", "branch": "main"}

    def pull(*_args, **_kwargs):
        calls["pull"] += 1
        return {"number": 7, "url": "https://github.com/octo/shop/pull/7", "state": "open"}

    monkeypatch.setattr(ws, "commit_file", one)
    monkeypatch.setattr(ws, "commit_files", many)
    monkeypatch.setattr(ws, "open_pull", pull)
    monkeypatch.setattr(ws, "create_branch", lambda token, full, name, sha: {"name": name, "sha": sha})
    return calls


GITHUB = {"source_kind": "github", "github_repo_id": 42, "github_full_name": "octo/shop", "github_default_branch": "main", "github_tracked_branch": "main"}


def test_zip_projects_and_the_wrong_roles_cannot_edit(client, make_user, make_recruiter):
    owner, _ = make_user()
    recruiter, _ = make_recruiter()
    commenter, _ = make_user()
    pid = _project(client, owner)
    _invite(client, owner, pid, commenter, "commenter")
    body = {"branch": "main", "path": "a.txt", "content": "hi"}
    assert client.put(f"/projects/{pid}/workspace/drafts", headers=owner, json=body).status_code == 403
    assert client.put(f"/projects/{pid}/workspace/drafts", headers=recruiter, json=body).status_code == 403
    github_id = _project(client, owner, **GITHUB)
    _invite(client, owner, github_id, commenter, "commenter")
    assert client.put(f"/projects/{github_id}/workspace/drafts", headers=commenter, json=body).status_code == 403
    assert client.put(f"/projects/{github_id}/workspace/drafts", headers=recruiter, json=body).status_code == 403


def test_drafts_are_encrypted_and_blocked_paths_are_refused(client, make_user):
    owner, uid = make_user()
    pid = _project(client, owner, **GITHUB)
    saved = client.put(f"/projects/{pid}/workspace/drafts", headers=owner, json={
        "branch": "main", "path": "a.txt", "content": "hello draft", "base_blob_sha": "b" * 40,
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["content"] == "hello draft"
    with SessionLocal() as db:
        row = db.query(WorkspaceDraft).filter_by(project_id=pid).one()
        assert "hello draft" not in row.content_enc
    assert client.put(f"/projects/{pid}/workspace/drafts", headers=owner, json={"branch": "main", "path": ".env", "content": "x"}).status_code == 400
    assert client.put(f"/projects/{pid}/workspace/drafts", headers=owner, json={"branch": "main", "path": ".github/workflows/ci.yml", "content": "x"}).status_code == 400
    listed = client.get(f"/projects/{pid}/workspace/drafts", headers=owner, params={"branch": "main"}).json()
    assert listed[0]["path"] == "a.txt"
    assert client.delete(f"/projects/{pid}/workspace/drafts", headers=owner, params={"branch": "main", "path": "a.txt"}).status_code == 200
    assert uid


def test_commit_is_idempotent_and_a_retry_does_not_commit_again(client, make_user, github):
    owner, uid = make_user()
    _connect(uid)
    pid = _project(client, owner, **GITHUB)
    body = {
        "branch": "main", "path": "a.txt", "content": "next\n", "message": "Update a",
        "base_blob_sha": "b" * 40, "idempotency_key": "same-key-01",
    }
    first = client.post(f"/projects/{pid}/workspace/commit", headers=owner, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["committed"] is True and first.json()["replayed"] is False
    assert first.json()["sync"] == "pending"
    second = client.post(f"/projects/{pid}/workspace/commit", headers=owner, json=body)
    assert second.json()["replayed"] is True
    assert github["commit"] == 1 and github["job"] == 1

    many = client.post(f"/projects/{pid}/workspace/commits", headers=owner, json={
        "branch": "main", "message": "Two files", "parent_sha": "a" * 40, "idempotency_key": "multi-key-1",
        "files": [{"path": "a.txt", "content": "one", "base_blob_sha": "b" * 40}, {"path": "b.txt", "content": "two", "base_blob_sha": None}],
    })
    assert many.status_code == 200, many.text
    assert github["commits"] == 1 and many.json()["sha"] == "d" * 40

    denied = client.post(f"/projects/{pid}/workspace/branches", headers=owner, json={"name": "../evil", "from_sha": "a" * 40})
    assert denied.status_code == 400

    opened = client.post(f"/projects/{pid}/workspace/pulls", headers=owner, json={"title": "Editor", "body": "", "head": "feature", "base": "main"})
    assert opened.status_code == 200, opened.text
    assert opened.json()["number"] == 7 and "merge" not in opened.json()
    assert github["pull"] == 1


def test_sync_retry_does_not_create_a_commit(client, make_user, github):
    owner, uid = make_user()
    _connect(uid)
    pid = _project(client, owner, **GITHUB)
    with SessionLocal() as db:
        job = WorkspaceSyncJob(project_id=pid, user_id=UUID(uid), trigger="commit", branch="main", status="failed", error="stopped", commit_sha="c" * 40, path="a.txt")
        db.add(job)
        db.commit()
        job_id = str(job.id)
    res = client.post(f"/projects/{pid}/workspace/sync/{job_id}/retry", headers=owner)
    assert res.status_code == 200 and res.json()["status"] == "pending"
    assert github["job"] == 1 and github["commit"] == 0


def test_push_permission_is_required(client, make_user, monkeypatch):
    owner, uid = make_user()
    _connect(uid)
    pid = _project(client, owner, **GITHUB)
    monkeypatch.setattr(ws, "require_push", lambda project, token: (_ for _ in ()).throw(ws.GitDenied(403, "Your GitHub account can't push to this repository.")))
    res = client.post(f"/projects/{pid}/workspace/commit", headers=owner, json={
        "branch": "main", "path": "a.txt", "content": "x", "message": "no", "idempotency_key": "nopush-key",
    })
    assert res.status_code == 403


def test_editing_stays_disabled_until_the_app_is_configured(client, make_user, monkeypatch):
    owner, _ = make_user()
    assert client.post("/github/connection/authorize", headers=owner).status_code == 503
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GITHUB_APP_SLUG", "devzgo")
    res = client.post("/github/connection/authorize", headers=owner)
    assert res.status_code == 200, res.text
    assert res.json()["url"].startswith("https://github.com/login/oauth/authorize")
    assert res.cookies.get("gh_app_state")
