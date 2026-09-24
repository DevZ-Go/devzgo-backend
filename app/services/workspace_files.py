"""
Shared workspace path resolution, language hints, binary/secret detection.

Used by file list/content/raw routes so permissioned reads stay consistent.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

STORAGE_ROOT = Path("storage")

# Extensions treated as binary (never decode as UTF-8 text).
BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".ico",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".class",
        ".o",
        ".a",
        ".wasm",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".mov",
        ".avi",
        ".webm",
        ".pyc",
        ".pyo",
        ".db",
        ".sqlite",
        ".sqlite3",
    }
)

IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
)

EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".json": "json",
    ".md": "markdown",
    ".markdown": "markdown",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".sh": "bash",
    ".bash": "bash",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".rb": "ruby",
    ".swift": "swift",
    ".kt": "kotlin",
    ".dart": "dart",
    ".sql": "sql",
    ".xml": "xml",
    ".toml": "toml",
    ".ini": "ini",
    ".env": "dotenv",
}

# Filenames that should not be shown in public previews (owner may still read).
SECRET_FILENAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.staging",
        ".env.test",
        "credentials.json",
        "secrets.json",
        "secret.json",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
    }
)

SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
SECRET_NAME_PREFIXES = (".env.",)


def workspace_dir_for_project(project_id: UUID) -> Path:
    return STORAGE_ROOT / f"project_{project_id}"


def normalize_relative_path(path: str) -> str:
    """Normalize client path to a posix relative path; raise ValueError if invalid."""
    raw = path.strip().replace("\\", "/").lstrip("/")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError("Invalid file path")
    return "/".join(parts)


def resolve_workspace_file(project_id: UUID, relative: str) -> Path:
    """
    Resolve a relative path under the project workspace.
    Raises ValueError on traversal/symlinks; FileNotFoundError if missing/not a file.
    """
    workspace_root = workspace_dir_for_project(project_id).resolve()
    cur = workspace_root
    for part in Path(relative).parts:
        cur = cur / part
        if cur.is_symlink():
            raise ValueError("Symlinks are not allowed")

    if not cur.exists() or not cur.is_file():
        raise FileNotFoundError("File not found")

    resolved = cur.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError("Invalid file path") from exc
    return resolved


def extension_of(path: str) -> str:
    return Path(path).suffix.lower()


def language_for_path(path: str) -> str | None:
    return EXT_TO_LANGUAGE.get(extension_of(path))


def is_image_path(path: str) -> bool:
    return extension_of(path) in IMAGE_EXTENSIONS


def is_secret_path(path: str) -> bool:
    name = Path(path).name
    lower = name.lower()
    if lower in SECRET_FILENAMES or name in SECRET_FILENAMES:
        return True
    if lower.startswith(SECRET_NAME_PREFIXES):
        return True
    if lower.endswith(SECRET_SUFFIXES):
        return True
    return False


def sniff_is_binary(data: bytes, path: str) -> bool:
    ext = extension_of(path)
    if ext in BINARY_EXTENSIONS:
        return True
    # SVG is text; other images are binary.
    if ext == ".svg":
        return False
    if b"\x00" in data[:8192]:
        return True
    return False


def is_text_displayable(path: str, sample: bytes) -> bool:
    if is_secret_path(path):
        return True  # secret is text-capable; access policy is separate
    return not sniff_is_binary(sample, path)
