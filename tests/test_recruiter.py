"""Recruiter accounts, and profile lists that follow privacy settings."""

from uuid import UUID

from app.db.session import SessionLocal
from app.models.project import Project


def _login(client, email):
    token = client.post("/auth/login", data={"username": email, "password": "Password1!"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_recruiter_signup_asks_for_a_company_and_shows_it(client, make_user):
    stranger, _ = make_user()
    missing = client.post("/auth/register", json={
        "username": "recruiterone", "email": "recruiterone@example.com", "password": "Password1!", "role": "Recruiter",
    })
    assert missing.status_code == 400

    created = client.post("/auth/register", json={
        "username": "recruiterone", "email": "recruiterone@example.com", "password": "Password1!",
        "role": "Recruiter", "company": "North Agency",
    })
    assert created.status_code == 200
    headers = _login(client, "recruiterone@example.com")
    me = client.get("/profiles/me", headers=headers).json()
    assert me["role"] == "Recruiter" and me["company"] == "North Agency" and me["guide_seen"] is False
    assert client.post("/profiles/me/guide", headers=headers).json()["guide_seen"] is True

    public = client.get(f"/profiles/{me['user_id']}", headers=stranger).json()
    assert public["role"] == "Recruiter" and public["company"] == "North Agency" and public["guide_seen"] is None
    assert client.get("/profiles/talent", headers=stranger, params={"q": "recruiterone"}).json() == []

    client.put("/profiles/me", headers=headers, json={"profile_visibility": "connections"})
    hidden = client.get(f"/profiles/{me['user_id']}", headers=stranger).json()
    assert hidden["restricted"] is True and hidden["company"] is None and hidden["role"] == "Recruiter"


def test_profile_role_cannot_cross_the_account_type(client, make_user, make_recruiter):
    developer, _ = make_user()
    refused = client.put("/profiles/me", headers=developer, json={"role": "Recruiter", "company": "Campus Hire"})
    assert refused.status_code == 400
    assert client.get("/auth/me", headers=developer).json()["account_type"] == "developer"
    assert client.put("/profiles/me", headers=developer, json={"role": "Designer"}).json()["role"] == "Designer"

    recruiter, _ = make_recruiter()
    assert client.get("/auth/me", headers=recruiter).json()["account_type"] == "recruiter"
    assert client.put("/profiles/me", headers=recruiter, json={"role": "Developer"}).status_code == 400
    assert client.put("/profiles/me", headers=recruiter, json={"company": ""}).status_code == 400
    saved = client.put("/profiles/me", headers=recruiter, json={"company": "Campus Hire"}).json()
    assert saved["role"] == "Recruiter" and saved["company"] == "Campus Hire" and saved["account_type"] == "recruiter"


def test_project_and_team_lists_follow_visibility(client, make_user):
    owner, owner_id = make_user()
    guest, guest_id = make_user()
    stranger, _ = make_user()
    public_id = client.post("/projects", headers=owner, json={
        "title": "Open Work", "category": "Web Development", "visibility": "Public",
    }).json()["id"]
    private_id = client.post("/projects", headers=owner, json={
        "title": "Secret Work", "category": "Web Development", "visibility": "Private",
    }).json()["id"]

    db = SessionLocal()
    row = db.query(Project).filter(Project.id == UUID(public_id)).one()
    row.github_commit_count = 40
    row.github_push_count = 5
    db.commit()
    db.close()

    assert {p["title"] for p in client.get(f"/profiles/{owner_id}/projects", headers=owner).json()} == {"Open Work", "Secret Work"}
    assert [p["title"] for p in client.get(f"/profiles/{owner_id}/projects", headers=stranger).json()] == ["Open Work"]

    guest_name = client.get("/auth/me", headers=guest).json()["username"]
    invitation = client.post(
        f"/collaborations/projects/{private_id}/invitations", headers=owner,
        json={"username": guest_name, "role": "Designer"},
    ).json()
    assert client.put(f"/collaborations/invitations/{invitation['id']}/accept", headers=guest).status_code == 200

    assert "Secret Work" in {p["title"] for p in client.get(f"/profiles/{owner_id}/projects", headers=guest).json()}
    own_groups = client.get(f"/profiles/{guest_id}/groups", headers=guest).json()
    assert own_groups[0]["title"] == "Secret Work" and own_groups[0]["role"] == "Designer"
    assert any(member["role"] == "Owner" for member in own_groups[0]["members"])
    assert client.get(f"/profiles/{guest_id}/groups", headers=stranger).json() == []
    assert client.get(f"/profiles/{guest_id}/collaborations", headers=stranger).json() == []

    owner_name = client.get("/auth/me", headers=owner).json()["username"]
    client.put("/profiles/me", headers=owner, json={"skills": "Zig"})
    ranked = client.get("/profiles/talent", headers=stranger, params={"q": owner_name, "tech": "Zig", "sort": "commits"}).json()
    assert ranked[0]["id"] == owner_id
    assert ranked[0]["commits"] == 40 and ranked[0]["pushes"] == 5 and ranked[0]["projects"] == 1

    client.put("/profiles/me", headers=owner, json={"connection_visibility": "private", "activity_visibility": "connections"})
    hidden = client.get("/profiles/talent", headers=stranger, params={"q": owner_name}).json()[0]
    assert hidden["connections"] is None and hidden["activity"] is None
    snapshot = client.get(f"/profiles/{owner_id}/snapshot", headers=stranger).json()
    assert snapshot["projects"] == 1 and snapshot["commits"] == 40 and snapshot["connections"] is None
    assert snapshot["top"]["commits"] >= 40 and set(snapshot["top"]) == {"projects", "commits", "pushes", "connections", "activity"}
