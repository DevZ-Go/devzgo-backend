"""Username choice, project skills, and the extra privilege settings."""

import uuid


def _project(client, headers, **extra):
    body = {"title": "Piece", "category": "Web Development", "visibility": "Public"}
    body.update(extra)
    return client.post("/projects", headers=headers, json=body)


def test_an_email_username_has_to_be_replaced(client, make_user):
    from app.db.session import SessionLocal
    from app.models.user import User

    headers, user_id = make_user()
    db = SessionLocal()
    user = db.get(User, uuid.UUID(user_id))
    user.username = f"old{uuid.uuid4().hex[:6]}@example.com"
    db.commit()
    db.close()

    assert client.get("/auth/me", headers=headers).json()["username_needs_choice"] is True
    chosen = "dev" + uuid.uuid4().hex[:8]
    saved = client.put("/auth/username", headers=headers, json={"username": chosen})
    assert saved.status_code == 200
    me = client.get("/auth/me", headers=headers).json()
    assert me["username"] == chosen and me["username_needs_choice"] is False


def test_project_tech_is_added_to_profile_skills(client, make_user):
    from app.db.session import SessionLocal
    from app.models.techstack import TechStack

    headers, _ = make_user()
    assert client.put("/profiles/me", headers=headers, json={"skills": "Figma"}).json()["skills"] == "Figma"
    db = SessionLocal()
    tech = TechStack(name="Svelte" + uuid.uuid4().hex[:6])
    db.add(tech)
    db.commit()
    tech_id, tech_name = tech.id, tech.name
    db.close()

    assert _project(client, headers, tech_stack_ids=[tech_id]).status_code == 200
    skills = client.get("/profiles/me", headers=headers).json()["skills"]
    assert "Figma" in skills and tech_name in skills


def test_privileges_are_enforced(client, make_user):
    owner, owner_id = make_user()
    stranger, _ = make_user()
    name = client.get("/auth/me", headers=stranger).json()["username"]
    pid = _project(client, owner).json()["id"]

    assert client.get(f"/profiles/{owner_id}", headers=stranger).json()["email"] is None
    resume = client.post(
        "/profiles/me/resume",
        headers=owner,
        files={"file": ("cv.pdf", b"%PDF-1.4 resume", "application/pdf")},
    )
    assert resume.status_code == 200 and resume.json()["resume_url"]
    assert client.get(f"/profiles/{owner_id}", headers=stranger).json()["resume_url"] is None

    assert client.put("/profiles/me", headers=owner, json={
        "email_visibility": "public",
        "resume_visibility": "public",
        "notify_likes": False,
        "connection_visibility": "private",
    }).status_code == 200
    opened = client.get(f"/profiles/{owner_id}", headers=stranger).json()
    assert opened["email"] and opened["resume_url"] and opened["connections_visible"] is False

    assert client.put("/profiles/me", headers=stranger, json={"collaboration_policy": "none"}).status_code == 200
    assert client.post(
        f"/collaborations/projects/{pid}/invitations",
        headers=owner,
        json={"username": name},
    ).status_code == 403

    post = client.post("/network/posts", headers=owner, json={"content": "quiet note"}).json()
    assert client.post(f"/network/activities/{post['id']}/like", headers=stranger).status_code == 200
    assert "like" not in {n["type"] for n in client.get("/notifications", headers=owner).json()}
