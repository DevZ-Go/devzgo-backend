import io
import zipfile
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from app.services import github_import as gh


def _repo(repo_id, name, private=False, language="Python", topics=None, description="A tool"):
    return {
        "id": repo_id, "name": name, "full_name": f"octo/{name}", "owner": {"login": "octo"},
        "description": description, "private": private, "fork": False, "archived": False,
        "language": language, "topics": topics or [], "stargazers_count": 3, "size": 10,
        "pushed_at": "2026-01-01T00:00:00Z", "html_url": f"https://github.com/octo/{name}",
        "default_branch": "main",
    }


@pytest.fixture
def fake_github(monkeypatch):
    repos = {
        1: _repo(1, "shop-api", topics=["fastapi"]),
        2: _repo(2, "secret-game", private=True, language="C#", topics=["unity"]),
        3: _repo(3, "empty-repo", description=None),
    }
    revoked = []

    def archive(token, full_name, ref, dest):
        if full_name.endswith("empty-repo"):
            return False
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(f"{full_name.replace('/', '-')}-abc123/main.py", "print('hi')\n" * 20)
            z.writestr(f"{full_name.replace('/', '-')}-abc123/README.md", "# Shop\n")
        dest.write_bytes(buf.getvalue())
        return True

    monkeypatch.setattr(gh, "configured", lambda: True)
    monkeypatch.setattr(gh, "exchange_code", lambda code: f"tok-{code}")
    monkeypatch.setattr(gh, "revoke_token", lambda token: revoked.append(token))
    monkeypatch.setattr(gh, "github_login", lambda token: "octo")
    monkeypatch.setattr(gh, "list_repos", lambda token: list(repos.values()))
    monkeypatch.setattr(gh, "get_repo", lambda token, rid: repos.get(rid))
    monkeypatch.setattr(gh, "get_readme", lambda token, full: "# Title\n\n![badge](x.svg) A **great** [project](http://x).\n")
    monkeypatch.setattr(gh, "download_archive", archive)
    monkeypatch.setattr(gh, "repo_activity", lambda token, full: {"commits": 12, "pushes": 3})
    return revoked


def _session(client, headers):
    url = client.post("/github/import/authorize", headers=headers).json()["url"]
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["repo read:user"]
    assert query["redirect_uri"][0].endswith("/auth/github/callback")
    res = client.post("/github/import/session", headers=headers, json={"code": "c1", "state": query["state"][0]})
    assert res.status_code == 200, res.text
    return res.json()


def test_shared_callback_hands_import_code_to_the_import_page(client, make_user, fake_github):
    headers, _ = make_user()
    url = client.post("/github/import/authorize", headers=headers).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    res = client.get("/auth/github/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"].endswith("/import/github#" + urlencode({"code": "abc", "state": state}))
    denied = client.get("/auth/github/callback", params={"error": "access_denied", "state": state}, follow_redirects=False)
    assert denied.headers["location"].endswith("/import/github?error=denied")


def test_state_from_another_user_is_rejected(client, make_user, fake_github):
    a, _ = make_user()
    b, _ = make_user()
    url = client.post("/github/import/authorize", headers=a).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    res = client.post("/github/import/session", headers=b, json={"code": "c1", "state": state})
    assert res.status_code == 400


def test_import_creates_projects_matching_github_visibility(client, make_user, fake_github):
    headers, _ = make_user()
    other, _ = make_user()
    data = _session(client, headers)
    assert data["login"] == "octo"
    assert [r["name"] for r in data["repos"]] == ["shop-api", "secret-game", "empty-repo"]
    assert all(r["imported_project_id"] is None for r in data["repos"])
    session = data["session"]

    assert client.post("/github/import/repos/1", headers=other, json={"session": session}).status_code == 410

    first = client.post("/github/import/repos/1", headers=headers, json={"session": session}).json()
    assert first["visibility"] == "Public" and first["code"] == "analysed"
    assert first["total_files"] == 2 and "Python" in {lang["name"] for lang in first["languages"]}

    project = client.get(f"/projects/{first['project_id']}", headers=headers).json()
    assert project["category"] == "Web Development"
    assert project["github_url"] == "https://github.com/octo/shop-api"
    assert project["full_description"] == "Title\n\nA great project."
    files = client.get(f"/projects/{first['project_id']}/files", headers=headers).json()
    assert "main.py" in {f["file_path"] for f in files}

    private = client.post("/github/import/repos/2", headers=headers, json={"session": session}).json()
    assert private["visibility"] == "Private"
    assert client.get(f"/projects/{private['project_id']}", headers=other).status_code in (403, 404)

    empty = client.post("/github/import/repos/3", headers=headers, json={"session": session}).json()
    assert empty["code"] == "empty"

    again = client.post("/github/import/repos/1", headers=headers, json={"session": session})
    assert again.status_code == 409

    relisted = _session(client, headers)
    assert all(r["imported_project_id"] for r in relisted["repos"])

    assert client.post("/github/import/session/close", headers=headers, json={"session": session}).status_code == 200
    assert "tok-c1" in fake_github
    assert client.post("/github/import/repos/1", headers=headers, json={"session": session}).status_code == 410

    for pid in (first["project_id"], private["project_id"], empty["project_id"]):
        client.delete(f"/projects/{pid}", headers=headers)


def test_category_and_readme_helpers():
    assert gh.guess_category({"name": "x", "topics": ["pytorch"]}).value == "AI/ML"
    assert gh.guess_category({"name": "x", "language": "Kotlin"}).value == "Mobile"
    assert gh.guess_category({"name": "x", "language": "Rust"}).value == "Other"
    assert gh.readme_to_text("<p align=center><img src=a></p>\n# Hi\n\n\n\nText") == "Hi\n\nText"
