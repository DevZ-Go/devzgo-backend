"""Tests for deterministic project analysis (languages + manifests)."""

from __future__ import annotations

from pathlib import Path

from app.services.project_analysis import analyze_languages, analyze_workspace


def _write(root: Path, rel: str, content: str | bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def test_language_percentages_skip_node_modules(tmp_path: Path):
    _write(tmp_path, "src/app.ts", "x" * 700)
    _write(tmp_path, "src/app.css", "y" * 300)
    _write(tmp_path, "node_modules/pkg/index.js", "z" * 5000)

    langs = analyze_languages(tmp_path)
    names = {r["name"] for r in langs}
    assert "TypeScript" in names
    assert "CSS" in names
    assert "JavaScript" not in names  # only inside node_modules
    ts = next(r for r in langs if r["name"] == "TypeScript")
    css = next(r for r in langs if r["name"] == "CSS")
    assert ts["percentage"] == 70.0
    assert css["percentage"] == 30.0


def test_package_json_detects_react_vite_tailwind(tmp_path: Path):
    _write(
        tmp_path,
        "package.json",
        """
        {
          "dependencies": { "react": "^18.0.0", "react-dom": "^18.0.0" },
          "devDependencies": { "vite": "^5.0.0", "tailwindcss": "^3.0.0", "typescript": "^5.0.0" }
        }
        """,
    )
    _write(tmp_path, "src/main.tsx", "export {}\n")

    result = analyze_workspace(tmp_path)
    assert "React" in result.tech_names
    assert "Vite" in result.tech_names
    assert "Tailwind CSS" in result.tech_names
    assert "TypeScript" in result.tech_names
    assert "Node.js" in result.tech_names


def test_requirements_detects_fastapi(tmp_path: Path):
    _write(tmp_path, "requirements.txt", "fastapi==0.110.0\nuvicorn\n")
    _write(tmp_path, "main.py", "print('hi')\n")

    result = analyze_workspace(tmp_path)
    assert "FastAPI" in result.tech_names
    assert "Python" in result.tech_names
    assert any(r["name"] == "Python" for r in result.languages)


def test_dockerfile_marker(tmp_path: Path):
    _write(tmp_path, "Dockerfile", "FROM python:3.11\n")
    result = analyze_workspace(tmp_path)
    assert "Docker" in result.tech_names


def test_swiftui_app_without_manifests_ignores_finder_twins(tmp_path: Path):
    _write(tmp_path, "App/EntryFormView.swift", "import SwiftUI\nimport CoreData\nstruct EntryFormView: View {}\n")
    _write(tmp_path, "App.xcodeproj/project.pbxproj", "// !$*UTF8*$!\n")
    _write(tmp_path, "App/._EntryFormView.swift", b"\x00\x05\x16\x07" + b"\x00" * 4000)
    _write(tmp_path, "__MACOSX/App/._Other.py", b"\x00" * 4000)
    _write(tmp_path, "README.md", "# Journal\n" * 200)

    result = analyze_workspace(tmp_path)
    assert {"Swift", "SwiftUI", "Core Data", "Xcode"} <= set(result.tech_names)
    assert "Python" not in result.tech_names
    assert result.languages == [{"name": "Swift", "percentage": 100.0}]


def test_imports_lockfiles_and_cdn_links(tmp_path: Path):
    _write(tmp_path, "model.py", "import pandas as pd\nfrom sklearn.linear_model import Ridge\nimport cv2\n")
    _write(tmp_path, "explore.ipynb", '{"cells": [{"source": ["import torch\\n", "from matplotlib import pyplot"]}]}')
    _write(tmp_path, "site/index.html", '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5/dist/css/bootstrap.min.css">')
    _write(tmp_path, "web/app.js", "import { gsap } from 'gsap';\nconst io = require('socket.io-client');\n")
    _write(tmp_path, "web/package-lock.json", "{}" * 50_000)
    _write(tmp_path, "pyproject.toml", '[project]\ndescription = "get started with mysql"\ndependencies = ["fastapi>=0.1"]\n')

    result = analyze_workspace(tmp_path)
    names = set(result.tech_names)
    assert {"pandas", "scikit-learn", "OpenCV", "PyTorch", "Matplotlib", "Jupyter", "Bootstrap", "Socket.IO", "FastAPI"} <= names
    assert "MySQL" not in names  # words in a description are not dependencies
    assert "JSON" not in {r["name"] for r in result.languages}


def test_matlab_and_objective_c_share_an_extension(tmp_path: Path):
    _write(tmp_path, "solve.m", "x = linspace(0, 1);\nplot(x, x.^2)\n")
    _write(tmp_path, "ios/View.m", "#import <UIKit/UIKit.h>\n@implementation View\n@end\n")
    names = {r["name"] for r in analyze_languages(tmp_path)}
    assert names == {"MATLAB", "Objective-C"}


def test_upload_skips_finder_files_and_grows_the_catalog(client, make_user, tmp_path: Path):
    import io
    import zipfile

    headers, _ = make_user()
    pid = client.post("/projects", headers=headers, json={"title": "Journal", "category": "Mobile", "visibility": "Public"}).json()["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Journal/EntryFormView.swift", "import SwiftUI\nimport SwiftData\n")
        z.writestr("__MACOSX/Journal/._EntryFormView.swift", b"\x00\x05\x16\x07" + b"\x00" * 64)
        z.writestr("Journal/._Notes.swift", b"\x00" * 64)
        z.writestr("Journal/.DS_Store", b"\x00" * 64)
    up = client.post(f"/projects/{pid}/workspace/upload", headers=headers,
                     files={"file": ("Journal.zip", buf.getvalue(), "application/zip")})
    assert up.status_code == 200, up.text
    assert {"SwiftUI", "SwiftData", "Swift"} <= set(up.json()["detected_tech_stacks"])

    paths = {f["file_path"] for f in client.get(f"/projects/{pid}/files", headers=headers).json()}
    assert paths == {"Journal", "Journal/EntryFormView.swift"}
    catalog = client.get("/projects/techstacks").text
    assert "SwiftData" in catalog
    project = client.get(f"/projects/{pid}", headers=headers).json()
    assert len(project["detected_tech_stack_ids"]) == len(up.json()["detected_tech_stack_ids"])
