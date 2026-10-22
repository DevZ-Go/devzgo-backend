"""Skill matrix: evidence classes, file evidence, privacy and team projects."""

import io
import json
import uuid
import zipfile

from app.db.session import SessionLocal


def _project(client, headers, title="Planner", visibility="Public", tech_ids=()):
    return client.post("/projects", headers=headers, json={
        "title": title, "category": "Web Development", "visibility": visibility, "tech_stack_ids": list(tech_ids),
    }).json()["id"]


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _upload(client, headers, pid, files):
    r = client.post(f"/projects/{pid}/workspace/upload", headers=headers,
                    files={"file": ("src.zip", _zip(files), "application/zip")})
    assert r.status_code == 200, r.text
    return r.json()


REACT_APP = {
    "package.json": json.dumps({"dependencies": {"react": "^19", "react-dom": "^19"}}),
    "src/App.tsx": "import React from 'react'\nexport default function App() { return null }\n",
}


def _skill(matrix, name):
    return next((s for c in matrix["categories"] for s in c["skills"] if s["name"] == name), None)


def _tech_id(name: str) -> int:
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


def test_detected_skills_show_their_files_and_category(client, make_user):
    dev, dev_id = make_user()
    pid = _project(client, dev)
    _upload(client, dev, pid, REACT_APP)

    matrix = client.get(f"/profiles/{dev_id}/skill-matrix").json()
    react = _skill(matrix, "React")
    assert react["category"] == "frontend" and react["classes"] == ["detected"]
    row = react["projects"][0]
    assert row["project_id"] == pid and row["relation"] == "owner" and row["detected"]
    assert {"kind": "dependency", "file": "package.json"} in row["files"]
    assert "dependency" in react["sources"] and "import" in react["sources"]
    assert _skill(matrix, "TypeScript")["category"] == "languages"
    assert matrix["projects_with_file_evidence"] == 1
    assert "level" not in json.dumps(matrix).lower()


def test_declared_skills_are_kept_apart_from_detected(client, make_user):
    dev, dev_id = make_user()
    name = "Qwik" + uuid.uuid4().hex[:5]
    _project(client, dev, tech_ids=[_tech_id(name)])
    client.put("/profiles/me", headers=dev, json={"skills": "Haskell"})

    matrix = client.get(f"/profiles/{dev_id}/skill-matrix").json()
    added = _skill(matrix, name)
    assert added["classes"] == ["declared"] and "project" in added["sources"] and not added["projects"][0]["detected"]
    haskell = _skill(matrix, "Haskell")
    assert haskell["classes"] == ["declared"] and haskell["projects"] == [] and haskell["sources"] == ["profile"]
    assert matrix["totals"]["declared"] >= 2 and matrix["totals"]["detected"] == 0


def test_profile_skills_use_the_same_names_as_analysis(client, make_user):
    dev, dev_id = make_user()
    pid = _project(client, dev)
    _upload(client, dev, pid, {"Dockerfile": "FROM python:3.12\n", "app.py": "print(1)\n"})
    client.put("/profiles/me", headers=dev, json={"skills": "Dockerfile, Makefile, react"})

    matrix = client.get(f"/profiles/{dev_id}/skill-matrix").json()
    names = [s["name"] for c in matrix["categories"] for s in c["skills"]]
    assert "Dockerfile" not in names and "Makefile" not in names
    assert "profile" in _skill(matrix, "Docker")["sources"] and _skill(matrix, "React")["classes"] == ["declared"]


def test_private_projects_only_count_for_people_who_can_open_them(client, make_user):
    dev, dev_id = make_user()
    stranger, _ = make_user()
    pid = _project(client, dev, visibility="Private")
    _upload(client, dev, pid, REACT_APP)

    assert _skill(client.get(f"/profiles/{dev_id}/skill-matrix", headers=dev).json(), "React")["projects"][0]["visibility"] == "private"
    for viewer in (stranger, {}):
        matrix = client.get(f"/profiles/{dev_id}/skill-matrix", headers=viewer).json()
        assert _skill(matrix, "React") is None and matrix["projects_considered"] == 0


def test_closed_profiles_hide_projects_and_declared_skills(client, make_user):
    dev, dev_id = make_user()
    stranger, _ = make_user()
    pid = _project(client, dev)
    _upload(client, dev, pid, REACT_APP)
    client.put("/profiles/me", headers=dev, json={"skills": "Haskell", "profile_visibility": "connections"})

    matrix = client.get(f"/profiles/{dev_id}/skill-matrix", headers=stranger).json()
    assert matrix["restricted"] and matrix["categories"] == []


def test_team_projects_and_credited_commits(client, make_user):
    from app.models.project import Project

    owner, owner_id = make_user()
    member, member_id = make_user()
    pid = _project(client, owner, title="Shared")
    _upload(client, owner, pid, REACT_APP)
    name = client.get("/auth/me", headers=member).json()["username"]
    inv = client.post(f"/collaborations/projects/{pid}/invitations", headers=owner,
                      json={"username": name, "role": "Frontend", "access": "viewer"}).json()
    client.put(f"/collaborations/invitations/{inv['id']}/accept", headers=member)

    db = SessionLocal()
    project = db.get(Project, uuid.UUID(pid))
    project.github_url, project.github_commit_count = "https://github.com/example/shared", 12
    db.commit()
    db.close()

    owner_react = _skill(client.get(f"/profiles/{owner_id}/skill-matrix").json(), "React")
    assert owner_react["classes"] == ["contribution_linked", "detected"] and owner_react["commits"] == 12

    member_react = _skill(client.get(f"/profiles/{member_id}/skill-matrix").json(), "React")
    row = member_react["projects"][0]
    assert row["relation"] == "member" and row["role"] == "Frontend" and row["commits"] == 0
    assert member_react["classes"] == ["detected"] and member_react["team_projects"] == 1


def test_recruiters_have_no_skill_matrix(client, make_recruiter):
    _, rid = make_recruiter()
    assert client.get(f"/profiles/{rid}/skill-matrix").status_code == 404
