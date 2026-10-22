"""Recorded source facts: per-tech evidence, README sections, tests, CI and secrets, without running anything."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.services import source_facts
from app.services.project_analysis import analyze_workspace

AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
DB_PASSWORD = "s3cr3tpassw0rd"


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sample(root: Path) -> None:
    _write(root, "package.json", json.dumps({"dependencies": {"react": "^19"}, "devDependencies": {"vitest": "^2"}}))
    _write(root, "package-lock.json", "{}")
    _write(root, "src/App.tsx", "import React from 'react'\nexport default function App() { return null }\n")
    _write(root, "src/App.test.tsx", "import { test } from 'vitest'\ntest('x', () => {})\n")
    _write(root, "README.md", "# Planner\n\nA planner for study groups that keeps tasks and deadlines in one place for "
                              "students who share work across several courses and want one view of what is due next.\n\n"
                              "## Getting started\n\n```\nnpm install\nnpm run dev\n```\n\n## Usage\n\nOpen the app.\n\n"
                              "Live at https://planner.vercel.app\n")
    _write(root, ".github/workflows/ci.yml", "on: push\njobs: {}\n")
    _write(root, ".gitignore", "node_modules\n.env\n")
    _write(root, ".env", f"DATABASE_URL=postgres://app:{DB_PASSWORD}@db/app\n")
    _write(root, ".env.example", "DATABASE_URL=\n")
    _write(root, "src/config.ts", f"export const key = '{AWS_KEY}'\n")


def test_analysis_records_why_each_tech_was_detected(tmp_path: Path):
    _sample(tmp_path)
    result = analyze_workspace(tmp_path)
    assert {"kind": "dependency", "file": "package.json"} in result.evidence["React"]
    assert {"kind": "import", "file": "src/App.tsx"} in result.evidence["React"]
    assert any(r["kind"] == "config" and r["file"].startswith(".github/workflows") for r in result.evidence["GitHub Actions"])
    assert result.tech_names == sorted(result.evidence)


def test_facts_cover_docs_tests_practices_and_never_store_secret_values(tmp_path: Path):
    _sample(tmp_path)
    facts = source_facts.collect(tmp_path, analyze_workspace(tmp_path))

    assert facts["readme"]["path"] == "README.md"
    assert facts["readme"]["sections"]["setup"] and facts["readme"]["sections"]["usage"]
    assert facts["readme"]["sections"]["overview"]
    assert facts["readme"]["deploy_link"] == "https://planner.vercel.app"
    assert facts["tests"]["count"] == 1 and facts["tests"]["frameworks"] == ["Vitest"]
    assert facts["ci"] == [".github/workflows/ci.yml"]
    assert facts["lockfiles"] == ["package-lock.json"]
    assert facts["env_examples"] == [".env.example"]
    assert facts["gitignore"] == {"present": True, "path": ".gitignore", "covers_env": True}
    assert facts["security"]["secret_files"] == [".env"]
    assert {"file": "src/config.ts", "line": 1, "kind": "AWS access key"} in facts["security"]["possible_secrets"]

    stored = json.dumps(facts)
    assert AWS_KEY not in stored and DB_PASSWORD not in stored


def test_a_single_wrapper_folder_is_treated_as_the_project_root(tmp_path: Path):
    _write(tmp_path, "planner/.gitignore", ".env\n")
    _write(tmp_path, "planner/main.py", "print(1)\n")
    _write(tmp_path, "planner/docs/guide.md", "# Guide\n")
    facts = source_facts.collect(tmp_path, analyze_workspace(tmp_path))
    assert facts["gitignore"] == {"present": True, "path": "planner/.gitignore", "covers_env": True}
    assert facts["structure"]["root_source_files"] == 1 and facts["structure"]["top_dirs"] == ["docs"]
    assert facts["docs"] == ["planner/docs/guide.md"]


def test_placeholders_are_not_flagged(tmp_path: Path):
    _write(tmp_path, "settings.py", 'API_KEY = "your-api-key-goes-here"\npassword = os.environ["PASSWORD"]\n')
    facts = source_facts.collect(tmp_path, analyze_workspace(tmp_path))
    assert facts["security"]["possible_secrets"] == []


def test_generated_folders_are_counted_from_the_archive(tmp_path: Path):
    archive = tmp_path / "src.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("app/index.js", "console.log(1)")
        zf.writestr("app/node_modules/left-pad/index.js", "module.exports = 1")
        zf.writestr("app/node_modules/left-pad/package.json", "{}")
    root = tmp_path / "w"
    _write(root, "app/index.js", "console.log(1)")
    facts = source_facts.collect(root, analyze_workspace(root), archive)
    assert facts["generated"] == {"folders": ["node_modules"], "entries": 2}
    assert source_facts.collect(root, analyze_workspace(root))["generated"] is None
