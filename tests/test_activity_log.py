"""Activity logs from imported GitHub history: who gets credited, what the viewer may see, filters and paging."""

import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.db.session import SessionLocal
from app.models.github_history import ProjectCommit, ProjectPush
from app.models.project import Project
from app.models.user import User

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _project(client, headers, title, visibility="Public"):
    return client.post("/projects", headers=headers, json={
        "title": title, "category": "Web Development", "visibility": visibility,
    }).json()["id"]


def _seed(project_id, owner_gid, commits=(), pushes=()):
    """commits: (github id, email, days ago, message); pushes: (github id, days ago, branch)."""
    db = SessionLocal()
    project = db.query(Project).filter(Project.id == UUID(project_id)).one()
    project.github_url = f"https://github.com/example/{project_id[:8]}.git"
    project.github_owner_id = owner_gid
    for gid, email, ago, message in commits:
        db.add(ProjectCommit(project_id=project.id, sha=uuid.uuid4().hex + "abcdefgh", message=message,
                             author_github_id=gid, author_login=f"gh{gid}" if gid else None, author_name="Someone",
                             author_email=email, committed_at=NOW - timedelta(days=ago)))
    for gid, ago, branch in pushes:
        db.add(ProjectPush(project_id=project.id, event_id=uuid.uuid4().hex[:20], actor_github_id=gid,
                           actor_login=f"gh{gid}", branch=branch, commit_count=2, pushed_at=NOW - timedelta(days=ago)))
    db.commit()
    db.close()


def _link_github(user_id, gid):
    db = SessionLocal()
    user = db.query(User).filter(User.id == UUID(user_id)).one()
    user.github_id = gid
    email = user.email
    db.commit()
    db.close()
    return email


def _gid():
    return str(uuid.uuid4().int)[:9]


def test_person_log_credits_their_work_and_hides_what_the_viewer_cannot_open(client, make_user, make_recruiter):
    owner, owner_id = make_user()
    mate, mate_id = make_user()
    recruiter, _ = make_recruiter()
    owner_gid, mate_gid, stranger = _gid(), _gid(), _gid()
    mate_email = _link_github(mate_id, mate_gid)

    public = _project(client, owner, "Public Repo")
    private = _project(client, owner, "Private Repo", "Private")
    _seed(public, owner_gid,
          commits=[(owner_gid, None, 0, "Add login"), (owner_gid, None, 1, "Fix navbar"), (mate_gid, None, 1, "Write tests"),
                   (None, mate_email, 3, "Docs from laptop"), (stranger, None, 2, "Outside fix")],
          pushes=[(owner_gid, 0, "main"), (owner_gid, 1, "feature/auth"), (mate_gid, 1, "tests")])
    _seed(private, owner_gid, commits=[(owner_gid, None, 0, "Secret work")])

    seen = client.get(f"/profiles/{owner_id}/activity-log", headers=recruiter).json()
    assert seen["restricted"] is False
    assert seen["summary"]["commits"] == 2 and seen["summary"]["pushes"] == 2
    assert seen["summary"]["projects"] == 1 and seen["summary"]["active_days"] == 2
    assert seen["total"] == 4
    assert {i["user"]["id"] for i in seen["items"]} == {owner_id}
    assert all("Secret" not in (i.get("message") or "") for i in seen["items"])
    times = [i["at"] for i in seen["items"]]
    assert times == sorted(times, reverse=True)
    commit = next(i for i in seen["items"] if i["type"] == "commit")
    assert commit["url"].startswith("https://github.com/example/") and "/commit/" in commit["url"]
    assert ".git/" not in commit["url"]
    assert sum(c + p for _, c, p in seen["heatmap"]) == 4

    own = client.get(f"/profiles/{owner_id}/activity-log", headers=owner).json()
    assert own["summary"]["commits"] == 3 and own["summary"]["projects"] == 2

    teammate = client.get(f"/profiles/{mate_id}/activity-log", headers=recruiter).json()
    assert teammate["summary"]["commits"] == 2 and teammate["summary"]["pushes"] == 1
    assert {i["user"]["id"] for i in teammate["items"]} == {mate_id}

    team = client.get(f"/profiles/{owner_id}/activity-log", params={"scope": "team"}, headers=recruiter).json()
    assert team["total"] == 8 and team["summary"]["contributors"] == 4
    outsider = next(i for i in team["items"] if i.get("message") == "Outside fix")
    assert outsider["user"] is None and outsider["login"] == f"gh{stranger}"


def test_filters_paging_and_project_log(client, make_user):
    owner, owner_id = make_user()
    viewer, _ = make_user()
    gid = _gid()
    repo = _project(client, owner, "Busy Repo")
    _seed(repo, gid, commits=[(gid, None, d, f"Change {d}") for d in range(7)],
          pushes=[(gid, d, "main") for d in range(0, 6, 2)])

    commits = client.get(f"/profiles/{owner_id}/activity-log", params={"kind": "commits"}, headers=viewer).json()
    assert commits["total"] == 7 and {i["type"] for i in commits["items"]} == {"commit"}
    pushes = client.get(f"/profiles/{owner_id}/activity-log", params={"kind": "pushes"}, headers=viewer).json()
    assert pushes["total"] == 3 and pushes["items"][0]["branch"] == "main"
    found = client.get(f"/profiles/{owner_id}/activity-log", params={"q": "change 3", "kind": "commits"}, headers=viewer).json()
    assert [i["message"] for i in found["items"]] == ["Change 3"]
    today = client.get(f"/profiles/{owner_id}/activity-log", params={"day": NOW.date().isoformat()}, headers=viewer).json()
    assert today["total"] == 2 and {i["type"] for i in today["items"]} == {"commit", "push"}

    first = client.get(f"/profiles/{owner_id}/activity-log", params={"limit": 4}, headers=viewer).json()
    second = client.get(f"/profiles/{owner_id}/activity-log", params={"limit": 4, "offset": 4}, headers=viewer).json()
    assert first["total"] == 10
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    assert first["summary"]["current_streak"] == 7 and first["summary"]["longest_streak"] == 7

    log = client.get(f"/projects/{repo}/github/activity-log", headers=viewer).json()
    assert log["subject"]["project"]["title"] == "Busy Repo" and log["total"] == 10
    assert log["projects"][0]["commits"] == 7 and log["projects"][0]["pushes"] == 3

    other = _project(client, owner, "Quiet Repo")
    _seed(other, gid, commits=[(gid, None, 1, "Quiet change")])
    narrowed = client.get(f"/profiles/{owner_id}/activity-log", params={"project": repo}, headers=viewer).json()
    assert narrowed["total"] == 10 and narrowed["summary"]["commits"] == 7 and narrowed["summary"]["projects"] == 1
    assert {p["title"] for p in narrowed["projects"]} == {"Busy Repo", "Quiet Repo"}

    scoped = client.get(f"/profiles/{owner_id}/activity-log", params={"project": str(uuid.uuid4())}, headers=viewer).json()
    assert scoped["total"] == 0


def test_hidden_profiles_and_private_projects_stay_hidden(client, make_user):
    owner, owner_id = make_user()
    viewer, _ = make_user()
    gid = _gid()
    private = _project(client, owner, "Closed Repo", "Private")
    _seed(private, gid, commits=[(gid, None, 0, "Private change")])
    assert client.get(f"/projects/{private}/github/activity-log", headers=viewer).status_code == 404

    public = _project(client, owner, "Open Repo")
    _seed(public, gid, commits=[(gid, None, 0, "Public change")])
    client.put("/profiles/me", headers=owner, json={"profile_visibility": "connections"})
    hidden = client.get(f"/profiles/{owner_id}/activity-log", headers=viewer).json()
    assert hidden["restricted"] is True and hidden["total"] == 0 and hidden["items"] == []
    assert hidden["summary"]["commits"] == 0
