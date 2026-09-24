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
