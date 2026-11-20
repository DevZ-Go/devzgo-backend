"""Safe file selection for Project Intelligence. Never executes code; excludes secrets and junk."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.services.project_analysis import SKIP_DIR_NAMES, SKIP_FILE_NAMES, SKIP_FILE_SUFFIXES
from app.services.project_intelligence.constants import MAX_FILE_BYTES, MAX_SCAN_FILES, MAX_TOTAL_BYTES
from app.services.workspace_extract import GENERATED_DIRS, is_os_junk
from app.services.workspace_files import is_secret_path

TEXT_SUFFIXES = frozenset({
    ".py", ".pyw", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts",
    ".json", ".yml", ".yaml", ".toml", ".md", ".mdx", ".txt", ".html", ".css",
    ".scss", ".vue", ".svelte", ".sql", ".graphql", ".gql", ".prisma", ".sh",
    ".dockerfile", ".env.example", ".env.sample",
})
TEXT_NAMES = frozenset({
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "makefile", "procfile", "readme", "readme.md", "readme.mdx", "readme.txt",
    "package.json", "requirements.txt", "pyproject.toml", "pipfile", "go.mod", "cargo.toml",
    "gemfile", "composer.json", "pubspec.yaml", "tsconfig.json", "vite.config.ts", "vite.config.js",
    "next.config.js", "next.config.mjs", "fastapi", "main.py", "app.py", "manage.py",
})
BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar",
    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".webm", ".mov", ".sqlite", ".db",
    ".exe", ".dll", ".so", ".dylib", ".class", ".jar", ".pyc", ".pyo",
})


@dataclass
class SelectedFile:
    path: Path
    relative: str
    size: int


@dataclass
class SelectionResult:
    files: list[SelectedFile] = field(default_factory=list)
    skipped: int = 0
    total_bytes: int = 0
    eligible_count: int = 0
    coverage_pct: float = 100.0
    content_hash: str = ""
    truncated: bool = False


def _should_skip_dir(name: str) -> bool:
    lower = name.lower()
    if lower in SKIP_DIR_NAMES or lower in GENERATED_DIRS:
        return True
    if name.startswith(".") and lower not in {".github"}:
        return True
    return is_os_junk(name)


def _is_candidate(rel: str, name: str) -> bool:
    lower = name.lower()
    if is_os_junk(name) or is_secret_path(rel) or is_secret_path(lower):
        return False
    if lower in SKIP_FILE_NAMES:
        return False
    if any(lower.endswith(suf) for suf in SKIP_FILE_SUFFIXES):
        return False
    if any(lower.endswith(suf) for suf in BINARY_SUFFIXES):
        return False
    suffix = Path(lower).suffix
    if suffix in TEXT_SUFFIXES or lower in TEXT_NAMES:
        return True
    # Config-ish names without suffix
    if lower in {"dockerfile", "makefile", "procfile", "gemfile"}:
        return True
    return False


def select_files(root: Path) -> SelectionResult:
    """Walk workspace and pick bounded text sources for analysis."""
    root = root.resolve()
    candidates: list[SelectedFile] = []
    skipped = 0

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not _should_skip_dir(d))
        base = Path(dirpath)
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink():
                skipped += 1
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                skipped += 1
                continue
            if not _is_candidate(rel, name):
                skipped += 1
                continue
            try:
                size = path.stat().st_size
            except OSError:
                skipped += 1
                continue
            if size <= 0 or size > MAX_FILE_BYTES:
                skipped += 1
                continue
            candidates.append(SelectedFile(path=path, relative=rel, size=size))

    eligible = len(candidates)
    # Prefer smaller source files first for broader coverage under budgets
    candidates.sort(key=lambda f: (f.size, f.relative))
    selected: list[SelectedFile] = []
    total = 0
    for item in candidates:
        if len(selected) >= MAX_SCAN_FILES:
            break
        if total + item.size > MAX_TOTAL_BYTES:
            continue
        selected.append(item)
        total += item.size

    truncated = len(selected) < eligible
    coverage = (100.0 * len(selected) / eligible) if eligible else 100.0

    h = hashlib.sha256()
    for item in selected:
        h.update(item.relative.encode())
        h.update(b"\0")
        h.update(str(item.size).encode())
        h.update(b"\0")
        try:
            with open(item.path, "rb") as fh:
                h.update(fh.read(min(item.size, 65_536)))
        except OSError:
            pass
    return SelectionResult(
        files=selected,
        skipped=skipped + (eligible - len(selected)),
        total_bytes=total,
        eligible_count=eligible,
        coverage_pct=round(coverage, 1),
        content_hash=h.hexdigest(),
        truncated=truncated,
    )


def read_text(path: Path, limit: int = MAX_FILE_BYTES) -> str:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit)
    except OSError:
        return ""
    if b"\x00" in raw[:4096]:
        return ""
    return raw.decode("utf-8", errors="ignore")
