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


def is_zip_symlink(member: zipfile.ZipInfo) -> bool:
    """Detect Unix symlink entries in a ZIP (mode bits in external_attr)."""
    mode = (member.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """
    Extract archive under dest_dir; block zip-slip, symlinks, and size bombs.
    Each member is resolved and must stay under dest_dir.resolve().
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
            if not raw_name:
                continue

            if is_zip_symlink(member):
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
