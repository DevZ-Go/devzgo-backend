"""Photos, videos and files in chat, plus emoji reactions.

Files are sniffed before they are stored. The key never leaves the server as a
bucket address: only the two people in a direct thread, or the members of a
group, can read it back through the API. Documents are always served as
downloads, never rendered inline.
"""

from __future__ import annotations

import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.services import storage

ALLOWED_EMOJIS = ("👍", "❤️", "😂", "🎉", "👀", "🔥", "👏", "✅")
_DOCS = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "md": "text/plain",
    "csv": "text/plain",
    "json": "text/plain",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
}
_KEY = re.compile(
    r"^chat/[0-9a-fA-F-]{36}/[0-9a-f]{32}\.(jpg|png|gif|webp|mp4|webm|pdf|txt|md|csv|json|docx|xlsx|pptx|zip)$"
)
_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "mp4": "video/mp4",
    "webm": "video/webm",
    **_DOCS,
}
_IMAGE_MAX = 8 * 1024 * 1024
_VIDEO_MAX = 25 * 1024 * 1024
_FILE_MAX = 15 * 1024 * 1024
_NAME = re.compile(r"[^A-Za-z0-9 ._()\-]+")


def _is_text(raw: bytes) -> bool:
    if b"\x00" in raw:
        return False
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _is_zip(raw: bytes) -> bool:
    return raw.startswith(b"PK\x03\x04")


def kind_for(key: str) -> str:
    ext = key.rsplit(".", 1)[-1]
    if ext in {"mp4", "webm"}:
        return "video"
    if ext in _DOCS:
        return "file"
    return "image"


def clean_name(name: Optional[str], key: str) -> str:
    """A safe download name that keeps the stored file's real extension."""
    ext = key.rsplit(".", 1)[-1]
    stem = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = _NAME.sub("", stem).strip(" .")[:100] or "attachment"
    return f"{stem}.{ext}"


def normalize_emoji(raw: str) -> str:
    value = (raw or "").strip()
    if value in {"❤", "❤️"}:
        value = "❤️"
    if value not in ALLOWED_EMOJIS:
        raise HTTPException(status_code=400, detail="Pick one of the reactions")
    return value


def clip(content: Optional[str], kind: Optional[str]) -> str:
    text = (content or "").strip()
    if text:
        return text if len(text) <= 80 else text[:80] + "…"
    if kind == "video":
        return "Video"
    if kind == "image":
        return "Photo"
    if kind == "file":
        return "File"
    return ""


def _store_document(user_id: UUID, upload: UploadFile, ext: str) -> tuple[str, str]:
    raw = upload.file.read(_FILE_MAX + 1)
    if len(raw) > _FILE_MAX:
        raise HTTPException(status_code=413, detail="Files must be 15 MB or smaller")
    sniffers = {
        "pdf": lambda data: data.startswith(b"%PDF-"),
        "docx": _is_zip,
        "xlsx": _is_zip,
        "pptx": _is_zip,
        "zip": _is_zip,
    }
    valid = sniffers.get(ext, _is_text)
    if not raw or not valid(raw):
        raise HTTPException(status_code=400, detail="That file does not match its type")
    key = f"chat/{user_id}/{uuid.uuid4().hex}.{ext}"
    storage.save(key, BytesIO(raw))
    return key, "file"


def store_upload(user_id: UUID, upload: UploadFile) -> tuple[str, str]:
    """Save one photo, video or document owned by this user. Returns (storage key, kind)."""
    ext = (upload.filename or "").rsplit(".", 1)[-1].lower()
    if ext == "jpeg":
        ext = "jpg"
    if ext == "markdown":
        ext = "md"
    if ext in _DOCS:
        return _store_document(user_id, upload, ext)
    checks = {
        "png": ("image", _IMAGE_MAX, lambda raw: raw.startswith(b"\x89PNG\r\n\x1a\n"), "image/png"),
        "jpg": ("image", _IMAGE_MAX, lambda raw: raw.startswith(b"\xff\xd8\xff"), "image/jpeg"),
        "gif": ("image", _IMAGE_MAX, lambda raw: raw[:6] in {b"GIF87a", b"GIF89a"}, "image/gif"),
        "webp": ("image", _IMAGE_MAX, lambda raw: len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP", "image/webp"),
        "mp4": ("video", _VIDEO_MAX, lambda raw: len(raw) >= 12 and raw[4:8] == b"ftyp", "video/mp4"),
        "webm": ("video", _VIDEO_MAX, lambda raw: raw.startswith(b"\x1a\x45\xdf\xa3"), "video/webm"),
    }
    if ext not in checks:
        raise HTTPException(
            status_code=400,
            detail="Use a photo (PNG, JPG, GIF, WebP), a video (MP4, WebM), or a file (PDF, TXT, MD, CSV, JSON, DOCX, XLSX, PPTX, ZIP)",
        )
    kind, limit, sniff, expected = checks[ext]
    ctype = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if ctype != expected:
        raise HTTPException(status_code=400, detail="That file type does not match its contents")
    raw = upload.file.read(limit + 1)
    if len(raw) > limit:
        label = "Videos" if kind == "video" else "Photos"
        mb = limit // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"{label} must be {mb} MB or smaller")
    if not raw or not sniff(raw):
        raise HTTPException(status_code=400, detail="That file is not a valid photo or video")
    key = f"chat/{user_id}/{uuid.uuid4().hex}.{ext}"
    storage.save(key, BytesIO(raw))
    return key, kind


def take_media(user_id: UUID, key: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Accept a key this user just uploaded. Anyone else's key is refused."""
    key = (key or "").strip()
    if not key:
        return None, None
    if not _KEY.match(key) or not key.startswith(f"chat/{user_id}/"):
        raise HTTPException(status_code=400, detail="Upload that attachment again")
    try:
        if storage.size(key) <= 0:
            raise FileNotFoundError(key)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="Upload that attachment again") from None
    return key, kind_for(key)


def media_response(key: str, name: Optional[str] = None) -> StreamingResponse:
    ext = key.rsplit(".", 1)[-1] if key else ""
    media_type = _TYPES.get(ext)
    if not media_type or not _KEY.match(key):
        raise HTTPException(status_code=404, detail="That file is gone")
    try:
        length = storage.size(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="That file is gone") from None
    document = ext in _DOCS
    filename = clean_name(name, key) if document else f"attachment.{ext}"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'{"attachment" if document else "inline"}; filename="{filename}"',
        "Cache-Control": "private, no-store",
        "Content-Length": str(length),
    }
    if document:
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    if storage.is_s3():
        body = storage.iter_range(key, 0, max(length - 1, 0))
    else:
        path = (storage.ROOT / key).resolve()
        root = storage.ROOT.resolve()
        if root not in path.parents:
            raise HTTPException(status_code=404, detail="That file is gone")
        body = _chunks(path)
    return StreamingResponse(body, media_type=media_type, headers=headers)


def _chunks(path: Path):
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            yield chunk


def reaction_map(db: Session, model, id_attr: str, ids, viewer: UUID) -> dict:
    if not ids:
        return {}
    column = getattr(model, id_attr)
    rows = db.query(model).filter(column.in_(list(ids))).all()
    grouped: dict = {}
    for row in rows:
        bucket = grouped.setdefault(getattr(row, id_attr), {})
        item = bucket.setdefault(row.emoji, {"emoji": row.emoji, "count": 0, "mine": False})
        item["count"] += 1
        if row.user_id == viewer:
            item["mine"] = True
    return {mid: list(bucket.values()) for mid, bucket in grouped.items()}


def toggle_reaction(db: Session, model, id_attr: str, message_id: UUID, user_id: UUID, emoji: str) -> None:
    column = getattr(model, id_attr)
    existing = db.query(model).filter(column == message_id, model.user_id == user_id, model.emoji == emoji).one_or_none()
    if existing:
        db.delete(existing)
    else:
        db.add(model(**{id_attr: message_id, "user_id": user_id, "emoji": emoji}))
