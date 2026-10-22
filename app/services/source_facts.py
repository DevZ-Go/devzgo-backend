"""Facts about a project's source, gathered once while the extracted files are on local disk.

Read-only and deterministic: files are listed and read as text, never run or installed. Possible
secrets are stored as file, line and kind only, never the matched value. The quality report
(app.services.project_quality) and skill matrix read these facts later without touching storage.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

from app.services.project_analysis import EXT_TO_LANGUAGE, PROSE_AND_DATA, AnalysisResult
from app.services.workspace_extract import GENERATED_DIRS, is_generated, is_os_junk

FACTS_VERSION = 1

SAMPLE = 5
SECRET_HIT_LIMIT = 20
SECRET_SCAN_BYTES = 256_000
SECRET_SCAN_FILES = 3_000

README_NAMES = ("readme.md", "readme.mdx", "readme.rst", "readme.txt", "readme")
README_SECTIONS: dict[str, tuple[str, ...]] = {
    "overview": ("about", "overview", "description", "introduction", "what is", "summary", "purpose"),
    "setup": ("install", "setup", "set up", "getting started", "quick start", "quickstart", "prerequisite",
              "requirements", "running locally", "run locally", "development", "build", "deploy"),
    "usage": ("usage", "how to use", "how it works", "features", "example", "running", "commands", "api"),
    "tech": ("tech stack", "built with", "technolog", "stack", "tools", "dependencies"),
    "screenshots": ("screenshot", "demo", "preview", "gif"),
}
SETUP_COMMANDS = re.compile(
    r"\b(?:npm (?:install|i|ci|run)|yarn(?: install| dev| start)?|pnpm (?:install|i|dev)|pip install|poetry install|"
    r"docker(?:-| )compose up|docker run|mvn |gradlew?|go run|cargo run|dotnet run|flutter run|bundle install|"
    r"composer install|python -m|uvicorn|make )"
)
README_HEADING = re.compile(r"(?m)^\s{0,3}(?:#{1,6}\s+(.+?)\s*#*\s*$|<h[1-6][^>]*>(.+?)</h[1-6]>)")
SETEXT_HEADING = re.compile(r"(?m)^([^\n#<>].{1,80})\n(?:=+|-+)\s*$")
LINK = re.compile(r"https?://[^\s)\"'>\]]+")
DEPLOY_HOSTS = ("vercel.app", "netlify.app", "github.io", "herokuapp.com", "onrender.com", "fly.dev", "pages.dev",
                "web.app", "firebaseapp.com", "azurewebsites.net", "railway.app", "surge.sh", "streamlit.app")

MANIFESTS = frozenset({
    "package.json", "requirements.txt", "pyproject.toml", "pipfile", "setup.py", "environment.yml", "pom.xml",
    "build.gradle", "build.gradle.kts", "go.mod", "cargo.toml", "gemfile", "composer.json", "pubspec.yaml",
    "package.swift", "podfile",
})
LOCKFILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock", "pipfile.lock", "cargo.lock",
    "composer.lock", "gemfile.lock", "podfile.lock", "go.sum", "pubspec.lock", "uv.lock",
})
LINT_FORMAT_PREFIXES = (
    ".eslintrc", "eslint.config.", ".prettierrc", "prettier.config.", ".editorconfig", ".stylelintrc", "biome.json",
    ".flake8", ".pylintrc", "ruff.toml", ".ruff.toml", "mypy.ini", ".rubocop.yml", "checkstyle.xml", ".swiftlint.yml",
    "analysis_options.yaml", ".golangci.", "rustfmt.toml", ".clang-format", ".pre-commit-config.yaml",
)
PYPROJECT_TOOLS = re.compile(r"(?m)^\[tool\.(ruff|black|isort|mypy|pylint|flake8)\]")
DEPLOY_FILES = frozenset({
    "vercel.json", "netlify.toml", "procfile", "render.yaml", "fly.toml", "app.yaml", "serverless.yml", "firebase.json",
    "railway.json", "amplify.yml", "now.json", "heroku.yml",
})
CI_FILES = frozenset({".gitlab-ci.yml", ".travis.yml", "jenkinsfile", "azure-pipelines.yml", "bitbucket-pipelines.yml"})
DOCKER_FILES = frozenset({"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"})
ENV_EXAMPLES = re.compile(r"^\.env\.(?:example|sample|template|dist|defaults)$|^(?:example|sample)\.env$", re.I)
SECRET_FILES = frozenset({".env", ".env.local", ".env.development", ".env.production", ".env.staging", ".env.test",
                          "credentials.json", "secrets.json", "secret.json", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
                          "service-account.json", "serviceaccountkey.json"})
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
KNOWN_DIRS = frozenset({
    "src", "app", "lib", "libs", "components", "pages", "routes", "services", "models", "controllers", "views",
    "api", "server", "client", "frontend", "backend", "core", "utils", "helpers", "hooks", "features", "modules",
    "config", "public", "assets", "static", "templates", "scripts", "docs", "tests", "test", "spec", "migrations",
    "packages", "cmd", "internal", "pkg", "include",
})
TEST_FILE = re.compile(
    r"(?:^|/)(?:tests?|__tests__|spec|specs|androidTest|e2e|cypress)/|(?:^|/)test_[^/]+\.py$|_test\.(?:py|go)$|"
    r"\.(?:test|spec)\.[cm]?[jt]sx?$|Tests?\.(?:swift|java|kt|cs)$|_spec\.rb$|Test\.php$",
    re.I,
)
TEST_TECHS = frozenset({"Jest", "Vitest", "Mocha", "Cypress", "Playwright", "Testing Library", "pytest", "JUnit",
                        "xUnit", "NUnit", "RSpec", "PHPUnit", "Selenium"})
TEST_CONFIG_PREFIXES = ("jest.config.", "vitest.config.", "playwright.config.", "cypress.config.", "pytest.ini",
                        "karma.conf.", ".mocharc", "phpunit.xml", "tox.ini", "conftest.py")
TEXT_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".properties", ".xml", ".gradle", ".kts", ".java", ".kt", ".swift", ".cs", ".go", ".rs", ".rb", ".php",
    ".dart", ".sh", ".txt", ".md", ".html", ".vue", ".svelte", ".tf", ".sql", ".plist", ".config",
})
PLACEHOLDER = re.compile(r"your|xxx|example|sample|placeholder|changeme|dummy|test|fake|<|\$\{|\{\{|process\.env|os\.environ|getenv", re.I)
SECRET_PATTERNS: list[tuple[str, re.Pattern[str], bool]] = [
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"), False),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), False),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}"), False),
    ("Slack token", re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}"), False),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}"), False),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), False),
    ("OpenAI-style API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}"), False),
    ("Database URL with a password",
     re.compile(r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp)://[^\s:/'\"@]+:([^\s@'\"/]{3,})@"), True),
    ("Hard-coded credential", re.compile(
        r"""(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key)"""
        r"""["']?\s*[:=]\s*["']([^"'\s]{12,})["']"""), True),
]


def _walk(root: Path):
    """Every file the author shipped: hidden folders included, generated folders (already left out at extract) skipped."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in GENERATED_DIRS and not is_os_junk(d))
        base = Path(dirpath)
        for name in sorted(filenames):
            path = base / name
            if is_os_junk(name) or path.is_symlink():
                continue
            yield path, path.relative_to(root).as_posix()


def _read(path: Path, limit: int) -> str:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit)
    except OSError:
        return ""
    if b"\x00" in raw[:4096]:
        return ""
    return raw.decode("utf-8", errors="ignore")


def _is_secret_file(name: str) -> bool:
    lower = name.lower()
    if ENV_EXAMPLES.match(lower):
        return False
    return lower in SECRET_FILES or lower.startswith(".env.") or lower.endswith(SECRET_SUFFIXES)


def _readme_facts(path: Path, rel: str) -> dict:
    text = _read(path, 400_000)
    headings = [(a or b).strip() for a, b in README_HEADING.findall(text)] + SETEXT_HEADING.findall(text)
    lowered = [re.sub(r"<[^>]+>|[*_`]", "", h).lower() for h in headings]
    sections = {key: any(word in h for h in lowered for word in words) for key, words in README_SECTIONS.items()}
    body = re.sub(r"```.*?```", " ", text, flags=re.S)
    words = len(re.findall(r"[A-Za-z]{2,}", body))
    sections["overview"] = sections["overview"] or words >= 40
    sections["setup"] = sections["setup"] or bool(SETUP_COMMANDS.search(text))
    links = LINK.findall(text)
    return {
        "path": rel,
        "words": words,
        "headings": len(headings),
        "sections": sections,
        "images": len(re.findall(r"!\[[^\]]*\]\(|<img\s", text, re.I)),
        "deploy_link": next((link for link in links if any(host in link for host in DEPLOY_HOSTS)), None),
    }


def _secret_hits(path: Path, rel: str) -> list[dict]:
    hits: list[dict] = []
    text = _read(path, SECRET_SCAN_BYTES)
    if not text:
        return hits
    for number, line in enumerate(text.splitlines(), start=1):
        if len(line) > 2_000:
            continue
        for kind, pattern, check_placeholder in SECRET_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            if check_placeholder and PLACEHOLDER.search(match.group(1) or ""):
                continue
            hits.append({"file": rel, "line": number, "kind": kind})
            break
    return hits


def _generated_in_archive(zip_path: Path | None) -> dict | None:
    """Dependency and cache folders inside the uploaded archive; extraction leaves them out, so count them here."""
    if zip_path is None or not zip_path.exists():
        return None
    folders: dict[str, int] = {}
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        return None
    for name in names:
        if not is_generated(name):
            continue
        parts = [p for p in name.replace("\\", "/").split("/") if p]
        folder = next((p for p in parts if p.lower() in GENERATED_DIRS), None)
        if folder:
            folders[folder] = folders.get(folder, 0) + 1
    return {"folders": sorted(folders), "entries": sum(folders.values())}


def collect(workspace_root: Path, analysis: AnalysisResult, zip_path: Path | None = None) -> dict:
    """Everything the quality report needs, as plain JSON. Paths are relative to the workspace root."""
    root = workspace_root.resolve()
    files: list[str] = []
    readme: dict | None = None
    manifests, lockfiles, lint, deploy, ci, docker, tests, test_configs = [], [], [], [], [], [], [], []
    env_examples, secret_files, docs = [], [], []
    secrets: list[dict] = []
    gitignore_text, gitignore_path = "", None
    root_sources = nested_sources = 0
    top_dirs: set[str] = set()
    max_depth = 0
    scanned = 0

    walked = list(_walk(root))
    # Archives often wrap the project in one folder ("my-app/..."); depth-based checks start inside it.
    firsts = {rel.split("/")[0] for _, rel in walked}
    wrapper = len(firsts) == 1 and all("/" in rel for _, rel in walked)

    for path, rel in walked:
        files.append(rel)
        name = path.name.lower()
        parts = rel.split("/")[1:] if wrapper else rel.split("/")
        depth = len(parts) - 1
        max_depth = max(max_depth, depth)
        if depth:
            top_dirs.add(parts[0])
        language = EXT_TO_LANGUAGE.get(path.suffix.lower())
        if language and language not in PROSE_AND_DATA:
            if depth:
                nested_sources += 1
            else:
                root_sources += 1

        if name in README_NAMES and (readme is None or depth < readme["path"].count("/")):
            readme = _readme_facts(path, rel)
        if name in MANIFESTS or (name.startswith("requirements") and name.endswith(".txt")) or \
                path.suffix.lower() in (".csproj", ".fsproj", ".vbproj"):
            manifests.append(rel)
        if name in LOCKFILES:
            lockfiles.append(rel)
        if name.startswith(LINT_FORMAT_PREFIXES) or (name == "pyproject.toml" and PYPROJECT_TOOLS.search(_read(path, 64_000))):
            lint.append(rel)
        if name in DEPLOY_FILES:
            deploy.append(rel)
        if name in CI_FILES or (".github" in parts and "workflows" in parts and name.endswith((".yml", ".yaml"))) or \
                (".circleci" in parts and name == "config.yml"):
            ci.append(rel)
        if name in DOCKER_FILES or name.startswith("dockerfile."):
            docker.append(rel)
        if TEST_FILE.search(rel) and language and language not in PROSE_AND_DATA:
            tests.append(rel)
        if name.startswith(TEST_CONFIG_PREFIXES):
            test_configs.append(rel)
        if ENV_EXAMPLES.match(name):
            env_examples.append(rel)
        if _is_secret_file(path.name):
            secret_files.append(rel)
        if name in ("contributing.md", "license", "license.md", "license.txt", "changelog.md") or parts[0].lower() == "docs":
            docs.append(rel)
        if name == ".gitignore" and depth == 0:
            gitignore_text, gitignore_path = _read(path, 64_000), rel

        scannable = path.suffix.lower() in TEXT_SUFFIXES or name in ("dockerfile", "procfile") or name.startswith(".env")
        if scannable and not _is_secret_file(path.name) and not ENV_EXAMPLES.match(name) and \
                scanned < SECRET_SCAN_FILES and len(secrets) < SECRET_HIT_LIMIT:
            scanned += 1
            secrets.extend(_secret_hits(path, rel)[: SECRET_HIT_LIMIT - len(secrets)])

    ignore_lines = {line.strip().lstrip("/") for line in gitignore_text.splitlines()}
    return {
        "version": FACTS_VERSION,
        "files": len(files),
        "source_files": root_sources + nested_sources,
        "structure": {
            "top_dirs": sorted(top_dirs)[:25],
            "known_dirs": sorted(d for d in top_dirs if d.lower() in KNOWN_DIRS),
            "root_source_files": root_sources,
            "nested_source_files": nested_sources,
            "max_depth": max_depth,
        },
        "readme": readme,
        "docs": docs[:SAMPLE],
        "env_examples": env_examples[:SAMPLE],
        "manifests": manifests[:10],
        "lockfiles": lockfiles[:SAMPLE],
        "lint": lint[:SAMPLE],
        "deploy": deploy[:SAMPLE],
        "ci": ci[:SAMPLE],
        "docker": docker[:SAMPLE],
        "tests": {
            "count": len(tests),
            "sample": tests[:SAMPLE],
            "configs": test_configs[:SAMPLE],
            "frameworks": sorted(TEST_TECHS & set(analysis.tech_names)),
        },
        "gitignore": {
            "present": gitignore_path is not None,
            "path": gitignore_path,
            "covers_env": any(line in (".env", ".env*", ".env.*", "*.env", ".env.local") for line in ignore_lines),
        },
        "security": {
            "secret_files": secret_files[:SECRET_HIT_LIMIT],
            "possible_secrets": secrets,
            "scanned_files": scanned,
        },
        "generated": _generated_in_archive(zip_path),
        "tech_evidence": analysis.evidence,
    }
