"""API tests for Project Intelligence (requires TEST_DATABASE_URL)."""

from __future__ import annotations

import io
import os
import zipfile

import pytest

FEATURE = os.getenv("FEATURE_PROJECT_INTELLIGENCE", "").lower() in {"1", "true", "yes", "on"}


@pytest.fixture(autouse=True)
def _enable_flag(monkeypatch):
    monkeypatch.setenv("FEATURE_PROJECT_INTELLIGENCE", "true")
    # Reload flag function reads os.getenv each call — no reload needed.


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_flag_off_returns_404(client, make_user, monkeypatch):
    monkeypatch.setenv("FEATURE_PROJECT_INTELLIGENCE", "false")
    headers, _ = make_user()
    r = client.get("/settings/project-intelligence", headers=headers)
    assert r.status_code == 404


def test_global_pref_and_analyze_flow(client, make_user, tmp_path):
    headers, _ = make_user()
    r = client.get("/settings/project-intelligence", headers=headers)
    assert r.status_code == 200
    assert "auto_analyze_default" in r.json()

    r = client.patch("/settings/project-intelligence", headers=headers, json={"auto_analyze_default": True})
    assert r.status_code == 200
    assert r.json()["auto_analyze_default"] is True

    created = client.post(
        "/projects",
        headers=headers,
        json={
            "title": "PI Demo",
            "short_description": "Short",
            "full_description": "Full description for tests.",
            "category": "Gaming",
            "visibility": "Public",
        },
    )
    assert created.status_code in (200, 201), created.text
    project_id = created.json()["id"]

    raw = _zip_bytes({
        "README.md": "# Demo\n\nA small demo API for checking Project Intelligence.\n",
        "requirements.txt": "fastapi==0.110.0\n",
        "main.py": "from fastapi import FastAPI\napp=FastAPI()\n@app.get('/')\ndef root():\n return {}\n",
    })
    up = client.post(
        f"/projects/{project_id}/workspace/upload",
        headers=headers,
        files={"file": ("demo.zip", raw, "application/zip")},
    )
    assert up.status_code == 200, up.text

    # Explicit analyze (also covered by auto enqueue when flag on)
    analyze = client.post(f"/projects/{project_id}/intelligence/analyze", headers=headers, json={"force": True})
    assert analyze.status_code == 200, analyze.text
    assert analyze.json()["status"] in {"ready", "incomplete", "analyzing", "queued"}

    insights = client.get(f"/projects/{project_id}/intelligence/insights")
    assert insights.status_code == 200
    body = insights.json()
    # May be available after inline processing
    if body.get("available"):
        assert "everyday" in body
        assert body["everyday"].get("what_it_does")

    # Generate without consent should fail
    gen = client.post(
        f"/projects/{project_id}/intelligence/generate",
        headers=headers,
        json={"grant_consent": False},
    )
    assert gen.status_code in (403, 502)

    # With consent grant flag — may still 502 without OPENAI_API_KEY; must not 500 crash
    gen2 = client.post(
        f"/projects/{project_id}/intelligence/generate",
        headers=headers,
        json={"grant_consent": True},
    )
    assert gen2.status_code in (200, 403, 429, 502)

    # Original descriptions must still be intact
    project = client.get(f"/projects/{project_id}", headers=headers).json()
    assert project["short_description"] == "Short"
    assert "Full description" in project["full_description"]
