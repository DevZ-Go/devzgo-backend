"""Technical challenges: roles, invitations, UTC deadlines, resubmission, manual review and consent-based sharing."""

import uuid
from datetime import datetime, timedelta, timezone

from app.db.session import SessionLocal

KEYS = ["correctness", "code_quality", "problem_solving", "testing", "documentation", "communication"]


def _future(days=7) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _challenge(client, recruiter, **extra):
    body = {
        "title": "Build a task API", "challenge_type": "backend", "technologies": ["FastAPI", "PostgreSQL", "fastapi"],
        "difficulty": "intermediate", "estimated_minutes": 180, "description": "A small REST API for tasks.",
        "instructions": "Create endpoints to add, list and complete tasks. Include tests.", "deadline_at": _future(),
        **extra,
    }
    r = client.post("/challenges", headers=recruiter, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _open(client, recruiter, **extra):
    ch = _challenge(client, recruiter, **extra)
    assert client.post(f"/challenges/{ch['id']}/publish", headers=recruiter).status_code == 200
    return ch["id"]


def _open_to_work(client, headers):
    client.put("/profiles/me", headers=headers, json={"open_to_work": True})


def _invite_and_accept(client, recruiter, dev, dev_id, cid):
    assert client.post(f"/challenges/{cid}/invitations", headers=recruiter,
                       json={"developer_ids": [dev_id]}).json()["invited"] == [dev_id]
    invite = client.get("/assessments", headers=dev).json()["invitations"][0]
    assert client.post(f"/assessments/invitations/{invite['id']}/accept", headers=dev).json()["status"] == "accepted"


def test_only_recruiters_create_and_drafts_stay_private(client, make_user, make_recruiter):
    dev, _ = make_user()
    recruiter, _ = make_recruiter()
    assert client.post("/challenges", headers=dev, json={}).status_code == 403
    ch = _challenge(client, recruiter)
    assert ch["status"] == "draft" and ch["technologies"] == ["FastAPI", "PostgreSQL"] and len(ch["criteria"]) == 6
    assert client.get(f"/challenges/{ch['id']}", headers=dev).status_code == 404
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert client.post("/challenges", headers=recruiter, json={**ch, "deadline_at": past}).status_code == 400
    assert client.post("/challenges", headers=recruiter, json={**ch, "deadline_at": "2030-01-01T00:00:00"}).status_code == 422


def test_full_flow_from_invitation_to_shared_result(client, make_user, make_recruiter):
    recruiter, _ = make_recruiter("Maple Labs")
    other_recruiter, _ = make_recruiter()
    dev, dev_id = make_user()
    viewer, _ = make_user()
    _open_to_work(client, dev)
    cid = _open(client, recruiter)

    assert client.put(f"/challenges/{cid}/submission", headers=dev,
                      json={"repo_url": "https://github.com/x/y", "explanation": "My solution"}).status_code == 404
    _invite_and_accept(client, recruiter, dev, dev_id, cid)
    again = client.post(f"/challenges/{cid}/invitations", headers=recruiter, json={"developer_ids": [dev_id]}).json()
    assert again["skipped"] == {dev_id: "Already invited"}

    private = client.post("/projects", headers=dev, json={
        "title": "Tasks", "category": "Web Development", "visibility": "Private"}).json()["id"]
    blocked = client.put(f"/challenges/{cid}/submission", headers=dev, json={"project_id": private, "explanation": "See project"})
    assert blocked.status_code == 400 and "cannot open" in blocked.json()["detail"]
    assert client.put(f"/challenges/{cid}/submission", headers=dev,
                      json={"repo_url": "http://insecure.example", "explanation": "Plain link"}).status_code == 422

    first = client.put(f"/challenges/{cid}/submission", headers=dev,
                       json={"repo_url": "https://github.com/x/tasks", "explanation": "FastAPI with tests."}).json()
    second = client.put(f"/challenges/{cid}/submission", headers=dev,
                        json={"repo_url": "https://github.com/x/tasks", "explanation": "Added pagination."}).json()
    assert first["revision"] == 1 and second["revision"] == 2 and second["id"] == first["id"]
    sid = second["id"]

    assert client.get(f"/challenges/{cid}/submissions", headers=other_recruiter).status_code == 404
    subs = client.get(f"/challenges/{cid}/submissions", headers=recruiter).json()
    assert [s["id"] for s in subs] == [sid] and subs[0]["explanation"] == "Added pagination."

    draft = client.put(f"/challenges/{cid}/submissions/{sid}/review", headers=recruiter,
                       json={"ratings": {"correctness": "strong"}, "feedback": "Good start"}).json()
    assert draft["status"] == "in_review" and draft["review"]["status"] == "draft"
    mine = client.get("/assessments", headers=dev).json()["submissions"][0]
    assert mine["status"] == "in_review" and mine["review"] is None
    assert client.put(f"/challenges/{cid}/submission", headers=dev,
                      json={"repo_url": "https://github.com/x/t2", "explanation": "Late change"}).status_code == 400
    assert client.delete(f"/challenges/{cid}/submission", headers=dev).status_code == 400
    incomplete = client.put(f"/challenges/{cid}/submissions/{sid}/review", headers=recruiter,
                            json={"ratings": {"correctness": "strong"}, "feedback": "x", "release": True})
    assert incomplete.status_code == 400
    assert client.put(f"/challenges/{cid}/submissions/{sid}/review", headers=recruiter,
                      json={"ratings": {"made_up": "strong"}}).status_code == 400

    ratings = {k: "meets_expectations" for k in KEYS} | {"correctness": "strong"}
    released = client.put(f"/challenges/{cid}/submissions/{sid}/review", headers=recruiter,
                          json={"ratings": ratings, "feedback": "Clear structure; add more edge-case tests.", "release": True}).json()
    assert released["status"] == "reviewed" and released["review"]["status"] == "released"
    assert client.put(f"/challenges/{cid}/submissions/{sid}/review", headers=recruiter,
                      json={"ratings": ratings, "feedback": "Changed"}).status_code == 400

    mine = client.get("/assessments", headers=dev).json()["submissions"][0]
    assert mine["review"]["ratings"]["correctness"] == "strong"
    assert client.get(f"/profiles/{dev_id}/assessments", headers=viewer).json() == []

    shared = client.put(f"/assessments/submissions/{sid}/sharing", headers=dev,
                        json={"share_ratings": False, "share_feedback": True}).json()
    assert not shared["share_ratings"] and not shared["share_feedback"]
    client.put(f"/assessments/submissions/{sid}/sharing", headers=dev, json={"share_ratings": True})
    public = client.get(f"/profiles/{dev_id}/assessments", headers=viewer).json()
    assert public[0]["company"] == "Maple Labs" and public[0]["feedback"] is None
    assert public[0]["ratings"][0] == {"label": "Correctness", "rating": "strong"}
    client.put(f"/assessments/submissions/{sid}/sharing", headers=dev, json={"share_ratings": True, "share_feedback": True})
    assert client.get(f"/profiles/{dev_id}/assessments").json()[0]["feedback"].startswith("Clear structure")

    assert client.delete(f"/challenges/{cid}", headers=recruiter).status_code == 409


def test_deadlines_close_submissions_in_utc(client, make_user, make_recruiter):
    from app.models.challenge import TechnicalChallenge

    recruiter, _ = make_recruiter()
    dev, dev_id = make_user()
    _open_to_work(client, dev)
    cid = _open(client, recruiter)
    _invite_and_accept(client, recruiter, dev, dev_id, cid)

    db = SessionLocal()
    db.get(TechnicalChallenge, uuid.UUID(cid)).deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    db.close()
    late = client.put(f"/challenges/{cid}/submission", headers=dev,
                      json={"repo_url": "https://github.com/x/late", "explanation": "Too late now"})
    assert late.status_code == 400 and "no longer accepting" in late.json()["detail"]


def test_public_challenges_need_no_invitation(client, make_user, make_recruiter):
    recruiter, _ = make_recruiter()
    dev, _ = make_user()
    other_recruiter, _ = make_recruiter()
    cid = _open(client, recruiter, visibility="public")

    listed = client.get("/assessments", headers=dev).json()["open_challenges"]
    assert cid in [c["id"] for c in listed]
    assert client.get("/assessments", headers=other_recruiter).status_code == 403
    sub = client.put(f"/challenges/{cid}/submission", headers=dev,
                     json={"repo_url": "https://github.com/x/pub", "explanation": "Public attempt"})
    assert sub.status_code == 200
    assert cid not in [c["id"] for c in client.get("/assessments", headers=dev).json()["open_challenges"]]
    assert client.delete(f"/challenges/{cid}/submission", headers=dev).status_code == 204
    assert client.get(f"/challenges/{cid}/submissions", headers=recruiter).json() == []

    assert client.post(f"/challenges/{cid}/close", headers=recruiter).json()["status"] == "closed"
    assert client.put(f"/challenges/{cid}/submission", headers=dev,
                      json={"repo_url": "https://github.com/x/pub", "explanation": "After closing"}).status_code in (400, 404)


def test_invitations_respect_privacy_and_deleted_projects_unlink(client, make_user, make_recruiter):
    recruiter, _ = make_recruiter()
    closed, closed_id = make_user()
    client.put("/profiles/me", headers=closed, json={"profile_visibility": "connections"})
    dev, dev_id = make_user()
    _open_to_work(client, dev)
    cid = _open(client, recruiter)

    result = client.post(f"/challenges/{cid}/invitations", headers=recruiter,
                         json={"developer_ids": [closed_id, dev_id]}).json()
    assert result["invited"] == [dev_id] and closed_id in result["skipped"]

    invite = client.get("/assessments", headers=dev).json()["invitations"][0]
    client.post(f"/assessments/invitations/{invite['id']}/accept", headers=dev)
    pid = client.post("/projects", headers=dev, json={
        "title": "Public tasks", "category": "Web Development", "visibility": "Public"}).json()["id"]
    sub = client.put(f"/challenges/{cid}/submission", headers=dev, json={"project_id": pid, "explanation": "See my project"}).json()
    assert sub["project"]["id"] == pid
    assert client.delete(f"/projects/{pid}", headers=dev).status_code in (200, 204)
    detail = client.get(f"/challenges/{cid}/submissions/{sub['id']}", headers=recruiter).json()
    assert detail["project"] is None and detail["explanation"] == "See my project"
