"""Recruiter accounts review work; developer project, upload, import and team flows stay as they were."""

from uuid import UUID

from app.db.session import SessionLocal
from app.models.enums import ProjectCategory, ProjectVisibility
from app.models.project import Project

PROJECT = {"title": "Open Work", "category": "Web Development", "visibility": "Public"}


def _legacy_project(owner_id: str) -> str:
    """A project a recruiter created before account types existed; it must stay readable and removable."""
    db = SessionLocal()
    row = Project(owner_id=UUID(owner_id), title="Old Recruiter Project", category=ProjectCategory.WEB_DEVELOPMENT,
                  visibility=ProjectVisibility.PUBLIC)
    db.add(row)
    db.commit()
    project_id = str(row.id)
    db.close()
    return project_id


def test_recruiters_cannot_create_upload_import_edit_or_join_projects(client, make_user, make_recruiter):
    recruiter, recruiter_id = make_recruiter()
    developer, _ = make_user()
    dev_project = client.post("/projects", headers=developer, json=PROJECT).json()["id"]
    legacy = _legacy_project(recruiter_id)

    assert client.post("/projects", headers=recruiter, json=PROJECT).status_code == 403
    assert client.put(f"/projects/{legacy}", headers=recruiter, json=PROJECT).status_code == 403
    zip_upload = {"file": ("work.zip", b"PK\x05\x06" + b"\x00" * 18, "application/zip")}
    assert client.post(f"/projects/{legacy}/workspace/upload", headers=recruiter, files=zip_upload).status_code == 403
    assert client.post(f"/projects/{legacy}/media", headers=recruiter).status_code == 403
    assert client.post("/github/import/authorize", headers=recruiter).status_code == 403
    assert client.post("/github/import/session", headers=recruiter, json={"code": "x", "state": "y"}).status_code == 403
    assert client.post(f"/projects/{legacy}/github/refresh", headers=recruiter, json={"session": "abc"}).status_code == 403
    assert client.post(f"/collaborations/projects/{dev_project}/requests", headers=recruiter,
                       json={"role": "Tester", "message": "hello"}).status_code == 403
    assert client.patch(f"/collaborations/projects/{legacy}/focus", headers=recruiter, json={"focus": "x"}).status_code == 403

    recruiter_name = client.get("/auth/me", headers=recruiter).json()["username"]
    invite = client.post(f"/collaborations/projects/{dev_project}/invitations", headers=developer,
                         json={"username": recruiter_name, "role": "Tester"})
    assert invite.status_code == 400
    search = client.get(f"/collaborations/projects/{dev_project}/invitee-search", headers=developer,
                        params={"q": recruiter_name[:6]}).json()
    assert recruiter_name not in {row["username"] for row in search}

    # Existing records are preserved: still visible, and the owner may still remove them.
    assert client.get(f"/projects/{legacy}", headers=developer).status_code == 200
    assert client.delete(f"/projects/{legacy}", headers=recruiter).status_code == 204


def test_developer_project_and_team_flows_are_unchanged(client, make_user):
    owner, _ = make_user()
    mate, _ = make_user()
    project_id = client.post("/projects", headers=owner, json=PROJECT).json()["id"]
    assert client.put(f"/projects/{project_id}", headers=owner, json={**PROJECT, "title": "Renamed"}).json()["title"] == "Renamed"
    request = client.post(f"/collaborations/projects/{project_id}/requests", headers=mate,
                          json={"role": "Tester", "message": "Can I help?"})
    assert request.status_code == 200
    assert client.put(f"/collaborations/requests/{request.json()['id']}/accept", headers=owner).status_code == 200
    assert client.patch(f"/collaborations/projects/{project_id}/focus", headers=mate, json={"focus": "tests"}).status_code == 200
    me = client.get("/profiles/me", headers=owner).json()
    assert me["account_type"] == "developer" and me["projects_count"] == 1


def test_tour_can_be_skipped_completed_and_replayed(client, make_user, make_recruiter):
    recruiter, recruiter_id = make_recruiter()
    stranger, _ = make_user()
    fresh = client.get("/profiles/me", headers=recruiter).json()
    assert fresh["tour_version"] is None and fresh["tour_state"] is None

    skipped = client.put("/profiles/me/tour", headers=recruiter, json={"version": 1, "state": "skipped"})
    assert skipped.json() == {"version": 1, "state": "skipped"}
    replayed = client.put("/profiles/me/tour", headers=recruiter, json={"version": 1, "state": "completed"}).json()
    assert replayed == {"version": 1, "state": "completed"}
    me = client.get("/profiles/me", headers=recruiter).json()
    assert me["tour_version"] == 1 and me["tour_state"] == "completed" and me["guide_seen"] is True

    assert client.put("/profiles/me/tour", headers=recruiter, json={"version": 1, "state": "maybe"}).status_code == 422
    public = client.get(f"/profiles/{recruiter_id}", headers=stranger).json()
    assert public["tour_version"] is None and public["tour_state"] is None


def test_availability_is_opt_in_and_only_for_developers(client, make_user, make_recruiter):
    developer, developer_id = make_user()
    recruiter, _ = make_recruiter()
    assert client.get(f"/profiles/{developer_id}", headers=recruiter).json()["open_to_work"] is False

    saved = client.put("/profiles/me", headers=developer, json={
        "open_to_work": True, "work_types": ["internship", "co-op", "internship"], "work_arrangements": ["remote"],
        "availability_note": "From May",
    }).json()
    assert saved["open_to_work"] is True and saved["work_types"] == ["internship", "co-op"]
    seen = client.get(f"/profiles/{developer_id}", headers=recruiter).json()
    assert seen["open_to_work"] is True and seen["work_arrangements"] == ["remote"] and seen["availability_note"] == "From May"

    client.put("/profiles/me", headers=developer, json={"open_to_work": False})
    hidden = client.get(f"/profiles/{developer_id}", headers=recruiter).json()
    assert hidden["open_to_work"] is False and hidden["work_types"] == [] and hidden["availability_note"] is None

    assert client.put("/profiles/me", headers=recruiter, json={"open_to_work": True}).status_code == 400
    assert client.put("/profiles/me", headers=developer, json={"work_types": ["astronaut"]}).status_code == 422
