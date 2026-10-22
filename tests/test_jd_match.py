"""Job description matching: deterministic extraction, editable requirements, explainable ranking, privacy."""

import io
import json
import uuid
import zipfile

from app.services.jd_match import extract

JD = """Senior Frontend Developer

About the role
You will build dashboards with our design team.

Requirements:
- 3+ years with React and TypeScript
- Experience with Postgres

Nice to have
- Docker, k8s
- Go is a plus

We move fast and react to feedback. We go to conferences.
"""


def _names(rows):
    return [r["name"] for r in rows]


def test_extraction_separates_required_preferred_and_ambiguous_terms():
    found = extract(JD, [])
    assert set(_names(found["required"])) == {"React", "TypeScript", "PostgreSQL"}
    assert set(_names(found["preferred"])) == {"Docker", "Kubernetes"}
    assert _names(found["ambiguous"]) == ["Go"]
    react = next(r for r in found["required"] if r["name"] == "React")
    assert react["reason"] == "Listed under \u201cRequirements\u201d" and "React and TypeScript" in react["excerpt"]


def test_mentions_are_not_turned_into_requirements():
    found = extract("We use React Native and Node for our apps.\nStrong knowledge of SQL Server is required.\n"
                    "Docs should be less wordy; we react quickly and bootstrap new services.", [])
    assert _names(found["required"]) == ["SQL Server"]
    assert set(_names(found["preferred"])) == {"React Native", "Node.js"}
    assert "React" not in _names(found["preferred"]) and "SQL" not in _names(found["required"])
    assert extract(JD, []) == extract(JD, [])


def test_cues_apply_to_their_own_sentence():
    found = extract("Requirements: TypeScript, React and PostgreSQL. Nice to have: Docker and Node.js experience.", [])
    assert set(_names(found["required"])) == {"TypeScript", "React", "PostgreSQL"}
    assert set(_names(found["preferred"])) == {"Docker", "Node.js"}


def _project(client, headers, title, visibility="Public", tech_ids=()):
    return client.post("/projects", headers=headers, json={
        "title": title, "category": "Web Development", "visibility": visibility, "tech_stack_ids": list(tech_ids),
    }).json()["id"]


def _upload(client, headers, pid, files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    assert client.post(f"/projects/{pid}/workspace/upload", headers=headers,
                       files={"file": ("src.zip", buf.getvalue(), "application/zip")}).status_code == 200


def _tech_id(name):
    from app.db.session import SessionLocal
    from app.models.techstack import TechStack

    db = SessionLocal()
    tech = db.query(TechStack).filter(TechStack.name == name).first()
    if tech is None:
        tech = TechStack(name=name)
        db.add(tech)
        db.commit()
    tid = tech.id
    db.close()
    return tid


def _result(body, user_id):
    return next((r for r in body["results"] if r["id"] == user_id), None)


def test_ranking_uses_visible_evidence_and_explains_each_requirement(client, make_user, make_recruiter):
    recruiter, _ = make_recruiter()
    tag = "Zq" + uuid.uuid4().hex[:6]
    tag_id = _tech_id(tag)

    evidenced, evidenced_id = make_user()
    pid = _project(client, evidenced, "Shop", tech_ids=[tag_id])
    _upload(client, evidenced, pid, {
        "package.json": json.dumps({"dependencies": {"react": "^19"}}),
        "src/App.jsx": "import React from 'react'\n",
    })
    from app.db.session import SessionLocal
    from app.models.project import Project
    db = SessionLocal()
    project = db.get(Project, uuid.UUID(pid))
    project.detected_tech_stack_ids = list({*(project.detected_tech_stack_ids or []), tag_id})
    db.commit()
    db.close()

    declared, declared_id = make_user()
    _project(client, declared, "Notes", tech_ids=[tag_id, _tech_id("React")])

    profile_only, profile_only_id = make_user()
    client.put("/profiles/me", headers=profile_only, json={"skills": f"{tag}, React"})

    hidden, hidden_id = make_user()
    secret = _project(client, hidden, "Hidden", visibility="Private")
    _upload(client, hidden, secret, {"src/App.jsx": "import React from 'react'\n"})
    db = SessionLocal()
    project = db.get(Project, uuid.UUID(secret))
    project.detected_tech_stack_ids = [tag_id]
    db.commit()
    db.close()

    body = client.post("/recruiting/match", headers=recruiter, json={"required": [tag], "preferred": ["react"]}).json()
    assert body["required"] == [tag] and body["preferred"] == ["React"] and "twice" in body["formula"]
    top = _result(body, evidenced_id)
    assert top["percent"] == 100 and top["required_met"] == 1 and top["preferred_met"] == 1
    req = next(r for r in top["requirements"] if r["name"] == tag)
    assert req["status"] == "evidenced" and req["projects"][0]["title"] == "Shop"

    second = _result(body, declared_id)
    assert second["percent"] == 50 and {r["status"] for r in second["requirements"]} == {"declared_project"}
    ids = [r["id"] for r in body["results"]]
    assert ids.index(evidenced_id) < ids.index(declared_id)
    assert _result(body, profile_only_id) is None  # a profile skill alone earns nothing
    assert _result(body, hidden_id) is None  # private projects never count

    strict = client.post("/recruiting/match", headers=recruiter, json={
        "required": [tag], "preferred": [], "require_all_required": True, "min_relevant_projects": 1}).json()
    assert {evidenced_id, declared_id} <= {r["id"] for r in strict["results"]}


def test_matching_is_recruiter_only_and_needs_requirements(client, make_user, make_recruiter):
    dev, _ = make_user()
    recruiter, _ = make_recruiter()
    assert client.post("/recruiting/match/extract", headers=dev, json={"text": JD}).status_code == 403
    assert client.post("/recruiting/match", headers=dev, json={"required": ["React"]}).status_code == 403
    assert client.post("/recruiting/match", json={"required": ["React"]}).status_code == 401
    assert client.post("/recruiting/match", headers=recruiter, json={"required": [" "], "preferred": []}).status_code == 400
    found = client.post("/recruiting/match/extract", headers=recruiter, json={"text": JD}).json()
    assert "React" in _names(found["required"])


def test_closed_profiles_are_left_out_and_counted(client, make_user, make_recruiter):
    recruiter, _ = make_recruiter()
    dev, dev_id = make_user()
    pid = _project(client, dev, "Closed")
    _upload(client, dev, pid, {"src/App.jsx": "import React from 'react'\n"})
    client.put("/profiles/me", headers=dev, json={"profile_visibility": "connections"})
    body = client.post("/recruiting/match", headers=recruiter, json={"required": ["React"]}).json()
    assert _result(body, dev_id) is None and body["restricted"] >= 1
