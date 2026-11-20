"""Quality gates and README cleaning for Project Intelligence briefs."""

from __future__ import annotations

from pathlib import Path

from app.services.project_intelligence.analyzer import analyze_tree
from app.services.project_intelligence.composer import compose_insights
from app.services.project_intelligence.quality import (
    QUALITY_REJECTED,
    QUALITY_VALID,
    developer_brief,
    is_valid_ai_explanation,
    score_explanation_quality,
    strip_readme_noise,
)


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_strip_readme_drops_vite_and_install_noise():
    raw = """# React + Vite

This template provides a minimal setup to get React working in Vite with HMR.

## Expanding the ESLint configuration

## Getting Started

```bash
npm install
npm run dev
```

Visit http://localhost:5173

Study Circle helps classmates organize group study sessions across courses.
"""
    cleaned = strip_readme_noise(raw)
    assert "npm install" not in cleaned.lower()
    assert "localhost" not in cleaned.lower()
    assert "study circle" in cleaned.lower() or "group study" in cleaned.lower()


def test_rejects_title_only_and_install_dump():
    bad = {
        "headline": "GoFishGame",
        "overview": "GoFishGame",
        "workflow": "GoFishGame, imported from GitHub.",
    }
    scored = score_explanation_quality(bad, title="GoFishGame")
    assert scored["status"] in (QUALITY_REJECTED, "low_quality")
    assert not is_valid_ai_explanation(bad, title="GoFishGame")

    dump = {
        "headline": "Frontend",
        "overview": "Run npm install then npm run dev. See the Vite + React template docs.",
        "workflow": "```bash\nnpm install\n```\n| Package | Version |\n| --- | --- |\n| vite | 5 |",
    }
    scored2 = score_explanation_quality(dump, title="devzgo-frontend")
    assert "install_instructions" in scored2["reasons"] or "raw_markdown" in scored2["reasons"] or "template_boilerplate" in scored2["reasons"]


def test_accepts_specific_brief():
    good = {
        "headline": "A card game you can play in the browser",
        "overview": "Go Fish lets players ask each other for matching cards until someone collects the most books.",
        "features": ["Deal cards to players", "Ask opponents for ranks", "Score completed books"],
        "purpose": "Practice a classic matching-card game online.",
        "audience": "People who want a simple multiplayer card game.",
        "workflow": "Players take turns asking for cards; when the deck runs out, the highest score wins.",
        "category": "game",
        "stack_nature": "frontend",
    }
    assert score_explanation_quality(good, title="GoFishGame")["status"] == QUALITY_VALID
    assert is_valid_ai_explanation(good, title="GoFishGame")


def test_developer_brief_skips_title_only():
    assert developer_brief("GoFishGame", "GoFishGame", "") == {}
    brief = developer_brief("GoFishGame", "A browser card game based on Go Fish.", "")
    assert brief.get("overview")
    assert "Go Fish" in brief["overview"]


def test_vite_readme_does_not_dominate_documented_purpose(tmp_path: Path):
    _write(
        tmp_path,
        "README.md",
        "# Vite + React\n\n"
        "This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.\n\n"
        "## Expanding the ESLint configuration\n\n"
        "```bash\nnpm install\nnpm run dev\n```\n",
    )
    _write(tmp_path, "package.json", '{"dependencies":{"react":"^18.0.0"},"devDependencies":{"vite":"^5.0.0"}}')
    _write(tmp_path, "src/pages/Lobby.tsx", "export default function Lobby(){return null}\n")
    bundle = analyze_tree(tmp_path)
    documented = [c for c in bundle.claims if c.claim_type == "documented_purpose"]
    # Prefer no documented_purpose over dumping Vite boilerplate
    assert all("npm install" not in c.wording.lower() for c in documented)
    assert all("eslint configuration" not in c.wording.lower() for c in documented)
    composed = compose_insights(
        title="Party Lobby",
        claims=bundle.claims,
        tech_names=bundle.tech_names,
        truncated=False,
        coverage_pct=100,
    )
    overview = (composed.get("project_brief") or {}).get("overview", "").lower()
    assert "npm" not in overview
    assert "vite with hmr" not in overview
