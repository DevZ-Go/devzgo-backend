"""
Safe ZIP extraction for project workspaces (no DB / FastAPI route coupling).

Limits protect against zip bombs and oversized student uploads.
"""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

from fastapi import HTTPException

# Student-project friendly limits (zip bombs / DoS).
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB compressed upload
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024  # 200 MiB uncompressed
MAX_FILE_COUNT = 5_000

# Added by the OS when zipping, not by the author: Finder's __MACOSX folder and ._ metadata twins, plus folder caches.
OS_JUNK_DIRS = frozenset({"__macosx"})
OS_JUNK_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


# Installed dependencies, caches and VCS data: often most of an archive's files, never the author's code.
GENERATED_DIRS = frozenset(
    {
        "node_modules", "bower_components", ".git", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
        "venv", ".venv", "site-packages", ".gradle", ".dart_tool", "pods", "deriveddata", "xcuserdata",
        ".next", ".nuxt", ".turbo", ".cache", ".parcel-cache",
    }
)


def is_generated(path: str) -> bool:
    parts = [p.lower() for p in path.replace("\\", "/").split("/") if p]
    folders = parts if path.endswith("/") else parts[:-1]
    return any(p in GENERATED_DIRS for p in folders)


def is_os_junk(path: str) -> bool:
    parts = [p.lower() for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    return any(p in OS_JUNK_DIRS for p in parts) or parts[-1].startswith("._") or parts[-1] in OS_JUNK_NAMES


def is_zip_symlink(member: zipfile.ZipInfo) -> bool:
    """Detect Unix symlink entries in a ZIP (mode bits in external_attr)."""
    mode = (member.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def safe_extract_zip(zip_path: Path, dest_dir: Path, skip_symlinks: bool = False) -> None:
    """
    Extract archive under dest_dir; block zip-slip, symlinks, and size bombs.
    Each member is resolved and must stay under dest_dir.resolve().
    With skip_symlinks, symlink entries are left out instead of rejecting the archive.
    """
    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    extracted_bytes = 0
    file_count = 0

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid or corrupted ZIP file") from exc

    with zf:
        for member in zf.infolist():
            raw_name = member.filename
            if not raw_name or is_os_junk(raw_name) or is_generated(raw_name):
                continue

            if is_zip_symlink(member):
                if skip_symlinks:
                    continue
                raise HTTPException(
                    status_code=400,
                    detail="ZIP contains symlink entries, which are not allowed",
                )

            rel = Path(raw_name)
            if rel.is_absolute() or ".." in rel.parts:
                continue

            target = (dest_dir / rel).resolve()
            try:
                target.relative_to(dest_dir)
            except ValueError:
                continue

            is_dir = member.is_dir() if hasattr(member, "is_dir") else raw_name.endswith("/")
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue

            file_count += 1
            if file_count > MAX_FILE_COUNT:
                raise HTTPException(
                    status_code=400,
                    detail=f"ZIP exceeds maximum of {MAX_FILE_COUNT} files",
                )

            declared = member.file_size
            if declared and extracted_bytes + declared > MAX_EXTRACTED_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Extracted workspace exceeds maximum of "
                        f"{MAX_EXTRACTED_BYTES // (1024 * 1024)} MiB"
                    ),
                )

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as source, open(target, "wb") as out:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    extracted_bytes += len(chunk)
                    if extracted_bytes > MAX_EXTRACTED_BYTES:
                        out.close()
                        if target.exists():
                            target.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Extracted workspace exceeds maximum of "
                                f"{MAX_EXTRACTED_BYTES // (1024 * 1024)} MiB"
                            ),
                        )
                    out.write(chunk)

            if target.is_symlink():
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=400,
                    detail="ZIP produced a symlink on disk, which is not allowed",
                )
