"""Authorised streaming of one stored project file, plus a short-lived token for media elements.

Callers check project access and secret visibility before asking for bytes. The token lets a
<video> or <audio> element request a range without the session header, and it is bound to one
project and path for five minutes.
"""

from __future__ import annotations

import io
import os
import tarfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from jose import JWTError, jwt

from app.core.security import ALGORITHM, SECRET_KEY
from app.services.file_responses import RangeNotSatisfiable, file_headers, parse_range
from app.services.file_types import (
    ARCHIVE_ENTRY_CAP,
    ARCHIVE_LIST_BYTES,
    Classified,
    classify,
    delivery,
    dimensions,
    preview_limit,
)
from app.services.workspace_files import iter_workspace_file, read_workspace_head, workspace_file_size

TOKEN_MINUTES = 5


def issue_stream_token(user_id: UUID | None, project_id: UUID, path: str, download: bool = False) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "purpose": "file_stream",
            "sub": str(user_id) if user_id else "anon",
            "pid": str(project_id),
            "path": path,
            "dl": download,
            "exp": now + timedelta(minutes=TOKEN_MINUTES),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def read_stream_token(token: str) -> dict:
    try:
        claims = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="This preview link has expired.") from exc
    if claims.get("purpose") != "file_stream":
        raise HTTPException(status_code=401, detail="This preview link has expired.")
    return claims


def stream_url(project_id: UUID, path: str, token: str, download: bool = False) -> str:
    base = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
    flag = "&download=1" if download else ""
    return f"{base}/projects/{project_id}/files/stream?path={quote(path)}{flag}&token={token}"


def describe(project_id: UUID, relative: str) -> tuple[int, bytes, Classified]:
    try:
        size = workspace_file_size(project_id, relative)
        head = read_workspace_head(project_id, relative, size, 4096)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to read file") from exc
    return size, head, classify(relative, head)


def serve_stored_file(project_id: UUID, relative: str, range_header: str | None, download: bool) -> Response:
    size, head, info = describe(project_id, relative)
    media, inline = delivery(info, relative, download)
    headers = file_headers(Path(relative).name, inline)
    try:
        span = parse_range(range_header, size)
    except RangeNotSatisfiable:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    if size == 0:
        return Response(b"", media_type=media, headers=headers)
    start, end = span or (0, size - 1)
    headers["Content-Length"] = str(end - start + 1)
    if span:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        iter_workspace_file(project_id, relative, start, end),
        status_code=206 if span else 200,
        media_type=media,
        headers=headers,
    )


def meta_payload(project_id: UUID, relative: str, user_id: UUID | None, secret: bool) -> dict:
    size, head, info = describe(project_id, relative)
    limit = preview_limit(info.kind)
    width, height = dimensions(head) if info.kind == "image" else (None, None)
    token = issue_stream_token(user_id, project_id, relative, False)
    download = issue_stream_token(user_id, project_id, relative, True)
    message = None
    if limit is not None and size > limit and info.kind != "text":
        message = "This file is too large to preview. You can download it."
    return {
        "path": relative,
        "name": Path(relative).name,
        "size": size,
        "kind": info.kind,
        "mime": info.mime,
        "is_secret": secret,
        "width": width,
        "height": height,
        "editable": info.kind == "text" and size <= 1_048_576,
        "preview_limited": bool(limit is not None and size > limit),
        "stream_url": None if message else stream_url(project_id, relative, token),
        "download_url": stream_url(project_id, relative, download, True),
        "message": message,
    }


def _entry(name: str, size: int | None, link: bool) -> dict:
    return {"name": name, "size": size, "symlink": link}


def list_archive(project_id: UUID, relative: str) -> dict:
    size, head, info = describe(project_id, relative)
    if info.kind != "archive":
        raise HTTPException(status_code=400, detail="This file is not an archive.")
    ext = Path(relative).suffix.lower()
    if ext in (".7z", ".rar") or head.startswith(b"7z\xbc\xaf\x27\x1c") or head.startswith(b"Rar!"):
        return {"kind": "archive", "size": size, "entries": [], "truncated": False,
                "message": "This archive type can be downloaded, not listed."}
    if size > ARCHIVE_LIST_BYTES:
        return {"kind": "archive", "size": size, "entries": [], "truncated": True,
                "message": "This archive is too large to list. You can download it."}
    data = b"".join(iter_workspace_file(project_id, relative, 0, size - 1)) if size else b""
    entries: list[dict] = []
    truncated = False
    try:
        if head.startswith(b"PK") or ext == ".zip":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for item in archive.infolist():
                    if len(entries) >= ARCHIVE_ENTRY_CAP:
                        truncated = True
                        break
                    mode = (item.external_attr >> 16) & 0o170000
                    entries.append(_entry(item.filename, item.file_size, mode == 0o120000 or item.filename.startswith("/")))
        else:
            mode = "r:gz" if head.startswith(b"\x1f\x8b") or ext in (".gz", ".tgz") else "r:"
            with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as archive:
                for item in archive:
                    if len(entries) >= ARCHIVE_ENTRY_CAP:
                        truncated = True
                        break
                    entries.append(_entry(item.name, item.size, item.issym() or item.islnk() or item.name.startswith("/")))
    except (zipfile.BadZipFile, tarfile.TarError, OSError):
        return {"kind": "archive", "size": size, "entries": [], "truncated": False,
                "message": "This archive could not be read. You can download it."}
    return {"kind": "archive", "size": size, "entries": entries, "truncated": truncated, "message": None}
