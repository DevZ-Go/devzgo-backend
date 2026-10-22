"""Project quality report: statuses, team-only security findings, no secret values, refresh and re-analysis."""

import io
import json
import uuid
import zipfile

from app.db.session import SessionLocal

AWS_KEY = "AKIA" + "QRSTUVWXYZABCDEF"


def _project(client, headers, visibility="Public", **extra):
    return client.post("/projects", headers=headers, json={
        "title": "Report", "category": "Web Development", "visibility": visibility, **extra,
    }).json()["id"]


def _upload(client, headers, pid, files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    r = client.post(f"/projects/{pid}/workspace/upload", headers=headers,
                    files={"file": ("src.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text


def _check(report, section, key):
    sec = next(s for s in report["sections"] if s["key"] == section)
    return next(c for c in sec["checks"] if c["key"] == key)


SOURCE = {
    "README.md": "# Tracker\n\n## Installation\n\npip install -r requirements.txt\n\n## Usage\n\nRun it.\n",
    "requirements.txt": "fastapi\npytest\n",
    "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
    "tests/test_main.py": "def test_ok():\n    assert True\n",
    ".env": "SECRET=abc\n",
    "app/settings.py": f"AWS = '{AWS_KEY}'\n",
}


def test_team_sees_findings_and_recommendations_others_see_safe_checks(client, make_user, make_recruiter):
    owner, _ = make_user()
    recruiter, _ = make_recruiter()
    pid = _project(client, owner)
    _upload(client, owner, pid, SOURCE)

    mine = client.get(f"/projects/{pid}/quality-report", headers=owner).json()
    assert mine["audience"] == "team" and mine["source_recorded"] and mine["can_refresh"]
    assert _check(mine, "documentation", "readme")["status"] == "found"
    assert _check(mine, "documentation", "setup")["status"] == "found"
    assert _check(mine, "testing", "test_files")["status"] == "found"
    assert _check(mine, "testing", "test_framework")["detail"] == "Uses pytest."
    assert _check(mine, "testing", "test_results")["status"] == "not_assessed"
    assert _check(mine, "practices", "ci")["status"] == "not_detected"
    assert _check(mine, "security", "secret_files")["status"] == "needs_review"
    hits = _check(mine, "security", "possible_secrets")
    assert hits["status"] == "needs_review" and {"file": "app/settings.py", "line": 1, "label": "AWS access key",
                                                 "url": None} in hits["evidence"]
    assert any(r["check"] == "ci" for r in mine["recommendations"])

    for viewer in (recruiter, {}):
        theirs = client.get(f"/projects/{pid}/quality-report", headers=viewer).json()
        security = next(s for s in theirs["sections"] if s["key"] == "security")
        assert theirs["audience"] == "viewer" and security["restricted"] and security["checks"] == []
        assert theirs["recommendations"] == [] and not theirs["can_refresh"]
        assert _check(theirs, "documentation", "readme")["status"] == "found"

    for body in (mine, client.get(f"/projects/{pid}/quality-report", headers=recruiter).json()):
        assert AWS_KEY not in json.dumps(body) and "abc" not in json.dumps(body["sections"][4])


def test_private_projects_stay_hidden(client, make_user):
    owner, _ = make_user()
    stranger, _ = make_user()
    pid = _project(client, owner, visibility="Private")
    assert client.get(f"/projects/{pid}/quality-report", headers=stranger).status_code == 404
    assert client.get(f"/projects/{pid}/quality-report").status_code == 404
    assert client.get(f"/projects/{pid}/quality-report", headers=owner).status_code == 200


def test_projects_without_recorded_source_are_not_assessed(client, make_user):
    owner, _ = make_user()
    pid = _project(client, owner, live_url="https://report.example.com", impact_note="Used by 40 students in my program.")
    report = client.get(f"/projects/{pid}/quality-report", headers=owner).json()
    assert not report["source_recorded"] and not report["has_source"] and not report["can_refresh"]
    assert _check(report, "documentation", "readme")["status"] == "not_assessed"
    assert _check(report, "security", "secret_files")["status"] == "not_assessed"
    demo = _check(report, "impact", "demo")
    assert demo["status"] == "found" and demo["evidence"][0]["url"] == "https://report.example.com"
    outcomes = _check(report, "impact", "outcomes")
    assert outcomes["status"] == "found" and outcomes["evidence"] == [
        {"label": "Reported by the developer", "file": None, "line": None, "url": None}]
    assert client.post(f"/projects/{pid}/quality-report/refresh", headers=owner).status_code == 409


def test_new_uploads_update_the_report_and_refresh_reanalyses(client, make_user):
    from app.models.project import Project

    owner, _ = make_user()
    stranger, _ = make_user()
    pid = _project(client, owner)
    _upload(client, owner, pid, {"main.py": "print('hi')\n"})
    assert _check(client.get(f"/projects/{pid}/quality-report", headers=owner).json(), "documentation", "readme")["status"] == "not_detected"

    _upload(client, owner, pid, {"main.py": "print('hi')\n", "README.md": "# Hi\n"})
    assert _check(client.get(f"/projects/{pid}/quality-report", headers=owner).json(), "documentation", "readme")["status"] == "found"

    db = SessionLocal()
    project = db.get(Project, uuid.UUID(pid))
    project.source_facts, project.source_analyzed_at = None, None
    db.commit()
    db.close()
    assert client.post(f"/projects/{pid}/quality-report/refresh", headers=stranger).status_code == 403
    refreshed = client.post(f"/projects/{pid}/quality-report/refresh", headers=owner).json()
    assert refreshed["source_recorded"] and _check(refreshed, "documentation", "readme")["status"] == "found"


def test_live_link_and_outcomes_are_validated_and_kept_on_partial_updates(client, make_user):
    owner, _ = make_user()
    bad = client.post("/projects", headers=owner, json={
        "title": "X", "category": "Web Development", "visibility": "Public", "live_url": "javascript:alert(1)"})
    assert bad.status_code == 422
    pid = _project(client, owner, live_url="https://x.example.com", impact_note="  Shipped to 3 clubs.  ")
    project = client.get(f"/projects/{pid}", headers=owner).json()
    assert project["live_url"] == "https://x.example.com" and project["impact_note"] == "Shipped to 3 clubs."
    client.put(f"/projects/{pid}", headers=owner, json={"title": "X2", "category": "Web Development", "visibility": "Public"})
    assert client.get(f"/projects/{pid}", headers=owner).json()["live_url"] == "https://x.example.com"
    client.put(f"/projects/{pid}", headers=owner, json={
        "title": "X2", "category": "Web Development", "visibility": "Public", "live_url": "", "impact_note": ""})
    cleared = client.get(f"/projects/{pid}", headers=owner).json()
    assert cleared["live_url"] is None and cleared["impact_note"] is None
