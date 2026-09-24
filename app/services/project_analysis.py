"""Project analysis: languages + frameworks from workspace files.

Deterministic only (no LLM). Called after ZIP extract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Directories ignored for language stats and most file walks.
SKIP_DIR_NAMES = frozenset(
    {
        "node_modules",
        ".git",
        "dist",
        "build",
        "coverage",
        ".next",
        "__pycache__",
        "venv",
        ".venv",
        "vendor",
        "target",
        ".turbo",
        ".cache",
        "out",
        "Pods",
        ".idea",
        ".vscode",
    }
)

# Extension → language display name (for percentages).
EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".swift": "Swift",
    ".php": "PHP",
    ".rb": "Ruby",
    ".dart": "Dart",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "CSS",
    ".sass": "CSS",
    ".less": "CSS",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".sql": "SQL",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".md": "Markdown",
    ".json": "JSON",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
}

# Extension → catalog tech names (languages / simple signals).
EXT_TO_TECHS: dict[str, list[str]] = {
    ".py": ["Python"],
    ".js": ["JavaScript"],
    ".jsx": ["JavaScript", "React"],
    ".ts": ["TypeScript"],
    ".tsx": ["TypeScript", "React"],
    ".java": ["Java"],
    ".kt": ["Kotlin"],
    ".cs": ["C#"],
    ".go": ["Go"],
    ".php": ["PHP"],
    ".swift": ["Swift"],
    ".dart": ["Dart", "Flutter"],
    ".html": ["HTML"],
    ".css": ["CSS"],
    ".scss": ["CSS"],
    ".vue": ["Vue.js"],
}

# npm package name → catalog tech name
NPM_PACKAGE_TO_TECH: dict[str, str] = {
    "react": "React",
    "react-dom": "React",
    "next": "Next.js",
    "vue": "Vue.js",
    "@vue/runtime-dom": "Vue.js",
    "vite": "Vite",
    "express": "Express.js",
    "tailwindcss": "Tailwind CSS",
    "@angular/core": "Angular",
    "react-native": "React Native",
    "@ionic/react": "Ionic",
    "@ionic/angular": "Ionic",
    "graphql": "GraphQL",
    "mongodb": "MongoDB",
    "mongoose": "MongoDB",
    "pg": "PostgreSQL",
    "mysql": "MySQL",
    "mysql2": "MySQL",
    "redis": "Redis",
    "ioredis": "Redis",
    "typescript": "TypeScript",
}

# Python requirement token → catalog name
PY_REQ_TO_TECH: dict[str, str] = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "tensorflow": "TensorFlow",
    "psycopg2": "PostgreSQL",
    "psycopg2-binary": "PostgreSQL",
    "asyncpg": "PostgreSQL",
    "sqlalchemy": "Python",
    "redis": "Redis",
    "pymongo": "MongoDB",
}

# Map alternate detected names onto seeded catalog names.
CATALOG_ALIASES: dict[str, str] = {
    "vue": "Vue.js",
    "express": "Express.js",
    "next": "Next.js",
    "nextjs": "Next.js",
    "tailwind": "Tailwind CSS",
    "tailwindcss": "Tailwind CSS",
    "fastapi": "FastAPI",
    "nodejs": "Node.js",
    "node": "Node.js",
    "spring": "Spring Boot",
    "springboot": "Spring Boot",
    "reactnative": "React Native",
}


@dataclass
class AnalysisResult:
    languages: list[dict] = field(default_factory=list)
    tech_names: list[str] = field(default_factory=list)


def _should_skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def _iter_source_files(root: Path):
    root = root.resolve()
    for dirpath, dirnames, filenames in __import__("os").walk(root, followlinks=False):
        # Prune vendor / generated dirs in-place.
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        base = Path(dirpath)
        for fname in filenames:
            path = base / fname
            if path.is_symlink():
                continue
            yield path


def _normalize_catalog_name(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "", name.lower())
    if name in (
        "Python",
        "JavaScript",
        "TypeScript",
        "HTML",
        "CSS",
        "JSON",
        "Java",
        "C#",
        "Go",
        "PHP",
        "Swift",
        "Kotlin",
        "React",
        "Angular",
        "Vue.js",
        "Django",
        "Flask",
        "FastAPI",
        "Spring Boot",
        "Express.js",
        "Docker",
        "Flutter",
        "React Native",
        "Node.js",
        "Next.js",
        "Vite",
        "Tailwind CSS",
        "PostgreSQL",
        "MySQL",
        "MongoDB",
        "Redis",
        "GraphQL",
        "TensorFlow",
        "Ionic",
        "AWS",
        "Azure",
        "Kubernetes",
        "Git",
        "CI/CD",
        "REST",
        "ASP.NET",
        "Laravel",
    ):
        return name
    return CATALOG_ALIASES.get(key, name)


def analyze_languages(workspace_root: Path) -> list[dict]:
    """Byte-weighted language percentages (0–100, one decimal)."""
    totals: dict[str, int] = {}
    for path in _iter_source_files(workspace_root):
        lang = EXT_TO_LANGUAGE.get(path.suffix.lower())
        if not lang:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= 0:
            continue
        totals[lang] = totals.get(lang, 0) + size

    grand = sum(totals.values())
    if grand <= 0:
        return []

    rows = []
    for name, nbytes in totals.items():
        pct = round((nbytes / grand) * 1000) / 10  # one decimal
        rows.append({"name": name, "percentage": pct, "bytes": nbytes})
    rows.sort(key=lambda r: (-r["percentage"], r["name"]))

    # Re-normalize so percentages sum ~100 after rounding.
    if rows:
        drift = round(1000 - sum(int(r["percentage"] * 10) for r in rows)) / 10
        rows[0]["percentage"] = round((rows[0]["percentage"] + drift) * 10) / 10
        for r in rows:
            r.pop("bytes", None)
    return rows


def _techs_from_extensions(workspace_root: Path) -> set[str]:
    found: set[str] = set()
    for path in _iter_source_files(workspace_root):
        for tech in EXT_TO_TECHS.get(path.suffix.lower(), []):
            found.add(_normalize_catalog_name(tech))
    return found


def _read_text_capped(path: Path, limit: int = 512_000) -> str:
    try:
        data = path.read_bytes()[:limit]
        return data.decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _find_files_named(workspace_root: Path, names: set[str]) -> list[Path]:
    lower_names = {n.lower() for n in names}
    hits: list[Path] = []
    for path in _iter_source_files(workspace_root):
        if path.name.lower() in lower_names:
            hits.append(path)
    return hits


def _techs_from_package_json(workspace_root: Path) -> set[str]:
    found: set[str] = set()
    for path in _find_files_named(workspace_root, {"package.json"}):
        text = _read_text_capped(path)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        deps: dict = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            block = data.get(key) or {}
            if isinstance(block, dict):
                deps.update(block)
        if deps:
            found.add("Node.js")
        for pkg in deps:
            mapped = NPM_PACKAGE_TO_TECH.get(pkg.lower()) or NPM_PACKAGE_TO_TECH.get(pkg)
            if mapped:
                found.add(_normalize_catalog_name(mapped))
            # scoped packages already handled via exact keys above
            low = pkg.lower()
            if low in NPM_PACKAGE_TO_TECH:
                found.add(_normalize_catalog_name(NPM_PACKAGE_TO_TECH[low]))
    return found


def _parse_requirement_token(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("-"):
        return None
    # Strip extras / version pins: fastapi[standard]==0.1 → fastapi
    token = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
    token = token.replace('"', "").replace("'", "")
    return token.lower() if token else None


def _techs_from_python_deps(workspace_root: Path) -> set[str]:
    found: set[str] = set()
    for path in _find_files_named(
        workspace_root, {"requirements.txt", "requirements-dev.txt", "Pipfile"}
    ):
        text = _read_text_capped(path)
        if path.name.lower() == "pipfile":
            # Rough: collect lines under [packages] / [dev-packages]
            in_block = False
            for line in text.splitlines():
                if line.strip().startswith("["):
                    in_block = line.strip().lower() in ("[packages]", "[dev-packages]")
                    continue
                if not in_block:
                    continue
                m = re.match(r'^"?([A-Za-z0-9_.-]+)"?\s*=', line.strip())
                if m:
                    tok = m.group(1).lower()
                    if tok in PY_REQ_TO_TECH:
                        found.add(_normalize_catalog_name(PY_REQ_TO_TECH[tok]))
            continue
        for line in text.splitlines():
            tok = _parse_requirement_token(line)
            if tok and tok in PY_REQ_TO_TECH:
                found.add(_normalize_catalog_name(PY_REQ_TO_TECH[tok]))

    for path in _find_files_named(workspace_root, {"pyproject.toml"}):
        text = _read_text_capped(path).lower()
        for pkg, tech in PY_REQ_TO_TECH.items():
            if pkg in text:
                found.add(_normalize_catalog_name(tech))
    return found


def _techs_from_java_build(workspace_root: Path) -> set[str]:
    found: set[str] = set()
    for path in _find_files_named(workspace_root, {"pom.xml", "build.gradle", "build.gradle.kts"}):
        text = _read_text_capped(path).lower()
        found.add("Java")
        if "spring-boot" in text or "springframework.boot" in text:
            found.add("Spring Boot")
        if "hibernate" in text:
            found.add("Java")
    return found


def _techs_from_markers(workspace_root: Path) -> set[str]:
    found: set[str] = set()
    for path in _iter_source_files(workspace_root):
        name = path.name.lower()
        if name == "dockerfile" or name.startswith("dockerfile."):
            found.add("Docker")
        elif name == "pubspec.yaml":
            found.add("Flutter")
            found.add("Dart")
        elif name == "androidmanifest.xml":
            found.add("Java")
    return found


def analyze_workspace(workspace_root: Path) -> AnalysisResult:
    """Full analysis: language percentages + merged tech names."""
    root = workspace_root.resolve()
    languages = analyze_languages(root)
    techs: set[str] = set()
    techs |= _techs_from_extensions(root)
    techs |= _techs_from_package_json(root)
    techs |= _techs_from_python_deps(root)
    techs |= _techs_from_java_build(root)
    techs |= _techs_from_markers(root)
    # Drop pure language-ish duplicates that are not in catalog sometimes — keep all normalized.
    names = sorted(techs)
    return AnalysisResult(languages=languages, tech_names=names)


def resolve_tech_stacks_from_names(db, names: list[str]):
    """
    Map detected names to TechStack ORM rows that exist in the catalog.
    Returns (linked_rows, linked_names, linked_ids).
    """
    from app.models.techstack import TechStack

    if not names:
        return [], [], []

    existing = db.query(TechStack).all()
    by_lower = {t.name.lower(): t for t in existing}
    # Also index alias → catalog
    for alias, canon in CATALOG_ALIASES.items():
        row = by_lower.get(canon.lower())
        if row is not None:
            by_lower[alias] = row

    linked = []
    seen: set[int] = set()
    for name in names:
        key = name.lower()
        tech = by_lower.get(key)
        if tech is None:
            # try stripped alias key
            compact = re.sub(r"[^a-z0-9]+", "", key)
            tech = by_lower.get(CATALOG_ALIASES.get(compact, "").lower()) if compact in CATALOG_ALIASES else None
            if tech is None and compact in by_lower:
                tech = by_lower[compact]
        if tech is None:
            continue
        tid = int(tech.id)
        if tid in seen:
            continue
        seen.add(tid)
        linked.append(tech)
    return linked, [t.name for t in linked], [int(t.id) for t in linked]
