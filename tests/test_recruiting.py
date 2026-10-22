"""Talent filters, candidate evaluation, private shortlists, and contact requests."""

import uuid
from datetime import date
from uuid import UUID

from app.db.session import SessionLocal
from app.models.monthly_wrap import MonthlyWrap
from app.models.network import NetworkActivity
from app.models.project import Project

PITCH = "We are hiring for a small product team and your projects look like a strong fit."


def _name(client, headers) -> str:
    return client.get("/auth/me", headers=headers).json()["username"]


def _project(client, headers, title, visibility="Public", tech_ids=()):
    return client.post("/projects", headers=headers, json={
        "title": title, "category": "Web Development", "visibility": visibility, "tech_stack_ids": list(tech_ids),
    }).json()["id"]


def _new_tech() -> tuple[int, str]:
    from app.models.techstack import TechStack

    db = SessionLocal()
    tech = TechStack(name="Qwik" + uuid.uuid4().hex[:6])
    db.add(tech)
    db.commit()
    found = (tech.id, tech.name)
    db.close()
    return found


def _set_github(project_id, commits, pushes=0):
    db = SessionLocal()
    row = db.query(Project).filter(Project.id == UUID(project_id)).one()
    row.github_url = "https://github.com/example/repo"
    row.github_commit_count, row.github_push_count = commits, pushes
    db.commit()
    db.close()


def test_talent_filters_explain_matches_and_never_use_hidden_data(client, make_user, make_recruiter):
    recruiter, _ = make_recruiter()
    shown, shown_id = make_user()
    skills_only, skills_id = make_user()
    hidden, hidden_id = make_user()
    react, tech = _new_tech()

    built = _project(client, shown, "Shown Work", tech_ids=[react])
    _set_github(built, 12, 3)
    client.put("/profiles/me", headers=shown, json={"open_to_work": True, "work_types": ["internship"]})
    client.put("/profiles/me", headers=skills_only, json={"skills": tech})
    client.put("/profiles/me", headers=hidden, json={"skills": f"{tech}, Hidden Skill", "profile_visibility": "connections",
                                                     "open_to_work": True})
    _project(client, hidden, "Hidden Work", tech_ids=[react])

    board = client.get("/profiles/talent", headers=recruiter, params={"tech": tech}).json()
    ids = [card["id"] for card in board]
    assert shown_id in ids and skills_id in ids and hidden_id not in ids
    assert ids.index(shown_id) < ids.index(skills_id)  # project evidence ranks above a listed skill
    card = next(c for c in board if c["id"] == shown_id)
    assert card["relevant_projects"] == 1 and card["source_projects"] == 1 and card["github_history"] is True
    assert any(f"Uses {tech} in 1 public project" in reason for reason in card["match"])
    skill_card = next(c for c in board if c["id"] == skills_id)
    assert any("as a skill" in reason for reason in skill_card["match"])

    evidence = client.get("/profiles/talent", headers=recruiter, params={"tech": tech, "evidence": "projects"}).json()
    assert [c["id"] for c in evidence if c["id"] in (shown_id, skills_id)] == [shown_id]
    assert client.get("/profiles/talent", headers=recruiter, params={"q": "Hidden Skill"}).json() == []

    working = client.get("/profiles/talent", headers=recruiter, params={"open_to_work": True, "work_type": "internship"}).json()
    assert shown_id in [c["id"] for c in working] and hidden_id not in [c["id"] for c in working]
    with_github = [c["id"] for c in client.get("/profiles/talent", headers=recruiter, params={"github": True}).json()]
    assert shown_id in with_github and skills_id not in with_github

    hidden_name = _name(client, hidden)
    restricted = client.get("/profiles/talent", headers=recruiter, params={"q": hidden_name}).json()[0]
    assert restricted["restricted"] is True and restricted["projects"] == 0 and restricted["skills"] is None
    assert restricted["open_to_work"] is False and restricted["tech"] == []

    no_history = _project(client, skills_only, "Plain Upload", tech_ids=[react])
    assert no_history
    plain = next(c for c in client.get("/profiles/talent", headers=recruiter, params={"tech": tech}).json() if c["id"] == skills_id)
    assert plain["github_history"] is False
    assert any("not counted against them" in reason for reason in plain["match"])
    recent = [c["id"] for c in client.get("/profiles/talent", headers=recruiter, params={"sort": "recent", "tech": tech}).json()]
    assert recent.index(shown_id) < recent.index(skills_id)
    assert client.get("/profiles/talent", headers=recruiter, params={"sort": "score"}).status_code == 400


def test_candidate_view_shows_only_permitted_evidence(client, make_user, make_recruiter):
    recruiter, _ = make_recruiter()
    developer, developer_id = make_user()
    _project(client, developer, "Public Build")
    _project(client, developer, "Private Build", visibility="Private")

    db = SessionLocal()
    post = NetworkActivity(user_id=UUID(developer_id), activity_type="wrap_up_published",
                           title="My March wrap", content="2 new project(s).")
    db.add(post)
    db.flush()
    db.add(MonthlyWrap(user_id=UUID(developer_id), month=date(2026, 3, 1), timezone="America/Toronto",
                       metrics={}, persona={"label": "The Builder"}, shared_activity_id=post.id))
    db.add(MonthlyWrap(user_id=UUID(developer_id), month=date(2026, 4, 1), timezone="America/Toronto", metrics={}))
    db.commit()
    db.close()

    view = client.get(f"/recruiting/candidates/{developer_id}", headers=recruiter).json()
    assert [p["title"] for p in view["projects"]] == ["Public Build"]
    assert [w["month"] for w in view["wraps"]] == ["2026-03"] and view["wraps"][0]["persona"] == "The Builder"
    assert view["contact"]["allowed"] is True and view["shortlists"] == []

    client.put("/profiles/me", headers=developer, json={"activity_visibility": "connections"})
    assert client.get(f"/recruiting/candidates/{developer_id}", headers=recruiter).json()["wraps"] == []

    other, _ = make_user()
    assert client.get(f"/recruiting/candidates/{developer_id}", headers=other).status_code == 403
    _, recruiter_id = make_recruiter()
    assert client.get(f"/recruiting/candidates/{recruiter_id}", headers=recruiter).status_code == 404


def test_shortlists_and_notes_are_private_to_their_owner(client, make_user, make_recruiter):
    owner, _ = make_recruiter()
    rival, _ = make_recruiter()
    candidate, candidate_id = make_user()

    shortlist = client.post("/recruiting/shortlists", headers=owner, json={"name": "  Spring   interns "}).json()
    assert shortlist["name"] == "Spring interns"
    entry = client.post(f"/recruiting/shortlists/{shortlist['id']}/entries", headers=owner,
                        json={"candidate_id": candidate_id, "note": "Strong tests"}).json()
    assert entry["status"] == "new" and entry["note"] == "Strong tests"
    again = client.post(f"/recruiting/shortlists/{shortlist['id']}/entries", headers=owner, json={"candidate_id": candidate_id})
    assert again.status_code == 409
    updated = client.patch(f"/recruiting/shortlists/{shortlist['id']}/entries/{entry['id']}", headers=owner,
                           json={"status": "interviewing"}).json()
    assert updated["status"] == "interviewing" and updated["note"] == "Strong tests"

    assert client.get("/recruiting/shortlists", headers=rival).json() == []
    assert client.get(f"/recruiting/shortlists/{shortlist['id']}", headers=rival).status_code == 404
    assert client.patch(f"/recruiting/shortlists/{shortlist['id']}/entries/{entry['id']}", headers=rival,
                        json={"note": "mine now"}).status_code == 404
    assert client.get("/recruiting/shortlists", headers=candidate).status_code == 403
    assert client.get(f"/recruiting/candidates/{candidate_id}", headers=rival).json()["shortlists"] == []
    mine = client.get(f"/recruiting/candidates/{candidate_id}", headers=owner).json()["shortlists"]
    assert mine[0]["status"] == "interviewing" and mine[0]["note"] == "Strong tests"
    profile = client.get(f"/profiles/{candidate_id}", headers=owner).text
    assert "Strong tests" not in profile

    dashboard = client.get("/recruiting/dashboard", headers=owner).json()
    assert dashboard["shortlists"] == 1 and dashboard["candidates"] == 1 and dashboard["statuses"] == {"interviewing": 1}
    assert client.delete(f"/recruiting/shortlists/{shortlist['id']}", headers=rival).status_code == 404
    assert client.delete(f"/recruiting/shortlists/{shortlist['id']}", headers=owner).status_code == 204


def test_contact_requests_respect_messaging_privacy_and_limits(client, make_user, make_recruiter):
    recruiter, recruiter_id = make_recruiter()
    closed, closed_id = make_user()
    client.put("/profiles/me", headers=closed, json={"message_policy": "connections"})
    body = {"candidate_id": closed_id, "role_title": "Junior Developer", "work_type": "internship", "message": PITCH}

    assert client.post("/recruiting/requests", headers=recruiter, json=body).status_code == 403
    client.put("/profiles/me", headers=closed, json={"open_to_work": True})
    sent = client.post("/recruiting/requests", headers=recruiter, json=body)
    assert sent.status_code == 201
    assert client.post("/recruiting/requests", headers=recruiter, json=body).status_code == 409

    developer, _ = make_user()
    assert client.post("/recruiting/requests", headers=developer, json=body).status_code == 403
    assert any(n["type"] == "recruitment_request" for n in client.get("/notifications", headers=closed).json())

    incoming = client.get("/recruiting/requests/incoming", headers=closed).json()
    assert incoming[0]["role_title"] == "Junior Developer" and incoming[0]["recruiter_company"] == "North Agency"
    assert client.put(f"/recruiting/requests/{incoming[0]['id']}/accept", headers=developer).status_code == 404
    accepted = client.put(f"/recruiting/requests/{incoming[0]['id']}/accept", headers=closed).json()
    assert accepted["status"] == "accepted"
    thread = client.get(f"/messages/conversations/{recruiter_id}", headers=closed)
    assert thread.status_code == 200 and "Junior Developer" in thread.text
    reply = client.post("/messages", headers=recruiter, json={"receiver_id": closed_id, "content": "Thanks!"})
    assert reply.status_code == 200

    declining, declining_id = make_user()
    request = client.post("/recruiting/requests", headers=recruiter, json={**body, "candidate_id": declining_id}).json()
    assert client.put(f"/recruiting/requests/{request['id']}/decline", headers=declining).json()["status"] == "declined"
    cooldown = client.post("/recruiting/requests", headers=recruiter, json={**body, "candidate_id": declining_id})
    assert cooldown.status_code == 403 and "declined recently" in cooldown.json()["detail"]

    hidden, hidden_id = make_user()
    client.put("/profiles/me", headers=hidden, json={"profile_visibility": "connections", "open_to_work": True})
    second, _ = make_recruiter()  # attempts, even refused ones, count toward the burst limit
    assert client.post("/recruiting/requests", headers=second, json={**body, "candidate_id": hidden_id}).status_code == 403

    burst, _ = make_recruiter()
    codes = []
    for _ in range(6):
        _, target = make_user()
        codes.append(client.post("/recruiting/requests", headers=burst, json={**body, "candidate_id": target}).status_code)
    assert codes[:5] == [201] * 5 and codes[5] == 429
