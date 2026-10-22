"""Classify an uploaded file from its extension and its first bytes.

The signature wins when the two disagree, so a file named notes.png that is actually HTML is shown
as source. HTML, SVG, XML and JavaScript are never given a type the browser would execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEXT_PREVIEW_BYTES = 2 * 1024 * 1024
EDIT_MAX_BYTES = 1 * 1024 * 1024
IMAGE_MAX_BYTES = 25 * 1024 * 1024
PDF_MAX_BYTES = 50 * 1024 * 1024
FONT_MAX_BYTES = 5 * 1024 * 1024
ARCHIVE_LIST_BYTES = 20 * 1024 * 1024
ARCHIVE_ENTRY_CAP = 1_000

ACTIVE_EXTENSIONS = {".html", ".htm", ".xhtml", ".xml", ".svg", ".js", ".mjs", ".cjs", ".xhtml"}
WORKFLOW_PREFIX = ".github/workflows/"

_IMAGE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
          ".webp": "image/webp", ".avif": "image/avif", ".bmp": "image/bmp", ".ico": "image/x-icon"}
_AUDIO = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".oga": "audio/ogg",
          ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac"}
_VIDEO = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg",
          ".mov": "video/quicktime"}
_FONT = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}
_ARCHIVE = {".zip", ".tar", ".gz", ".tgz", ".taz", ".7z", ".rar"}
_KIND_MIME = {"pdf": "application/pdf", "svg": "image/svg+xml", "archive": "application/octet-stream",
              "binary": "application/octet-stream", "unknown": "application/octet-stream", "text": "text/plain"}


@dataclass(frozen=True)
class Classified:
    kind: str
    mime: str


def _ext(path: str) -> str:
    return Path(path).suffix.lower()


def _ext_kind(ext: str) -> tuple[str, str] | None:
    if ext in _IMAGE:
        return "image", _IMAGE[ext]
    if ext in _AUDIO:
        return "audio", _AUDIO[ext]
    if ext in _VIDEO:
        return "video", _VIDEO[ext]
    if ext in _FONT:
        return "font", _FONT[ext]
    if ext == ".pdf":
        return "pdf", "application/pdf"
    if ext == ".svg":
        return "svg", "image/svg+xml"
    if ext in _ARCHIVE:
        return "archive", "application/octet-stream"
    return None


def _ftyp(head: bytes) -> str | None:
    if len(head) < 12 or head[4:8] != b"ftyp":
        return None
    brand = head[8:12]
    if brand in (b"avif", b"avis"):
        return "image"
    if brand in (b"M4A ", b"M4B "):
        return "audio"
    return "video"


def sniff_kind(head: bytes) -> str | None:
    """A kind recognised from magic bytes, or None when the sample has no known signature."""
    if len(head) >= 8 and head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if head.startswith(b"\xff\xd8\xff"):
        return "image"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image"
    if len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        return "archive"
    if head.startswith(b"\x1f\x8b"):
        return "archive"
    if head.startswith(b"7z\xbc\xaf\x27\x1c") or head.startswith(b"Rar!\x1a\x07"):
        return "archive"
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "archive"
    if head.startswith(b"wOFF"):
        return "font"
    if head.startswith(b"wOF2"):
        return "font"
    if head.startswith(b"\x00\x01\x00\x00") or head.startswith(b"OTTO"):
        return "font"
    if len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio"
    if head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio"
    if head.startswith(b"OggS"):
        return "audio"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video"
    ftyp = _ftyp(head)
    if ftyp:
        return ftyp
    if head.startswith(b"\x7fELF") or head.startswith(b"MZ") or head.startswith(b"\xfe\xed\xfa") or head.startswith(b"\xce\xfa\xed\xfe") or head.startswith(b"\xcf\xfa\xed\xfe"):
        return "binary"
    if head.startswith(b"\xca\xfe\xba\xbe") or head.startswith(b"SQLite format 3\x00"):
        return "binary"
    return None


def _is_text(head: bytes) -> bool:
    if not head:
        return True
    sample = head[:8192]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.reason == "unexpected end of data" and exc.start >= len(sample) - 3
    return True


def _mime_for(kind: str, ext: str, head: bytes, named: tuple[str, str] | None) -> str:
    if named and named[0] == kind:
        return named[1]
    if kind == "image":
        if head.startswith(b"\x89PNG"):
            return "image/png"
        if head.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if head.startswith(b"GIF"):
            return "image/gif"
        if len(head) >= 12 and head[8:12] == b"WEBP":
            return "image/webp"
        if _ftyp(head) == "image":
            return "image/avif"
    if kind == "audio":
        return "audio/wav" if len(head) >= 12 and head[8:12] == b"WAVE" else "audio/ogg" if head.startswith(b"OggS") else "audio/mpeg"
    if kind == "video":
        return "video/webm" if head.startswith(b"\x1aE\xdf\xa3") else "video/mp4"
    if kind == "font":
        if head.startswith(b"wOF2"):
            return "font/woff2"
        if head.startswith(b"wOFF"):
            return "font/woff"
        if head.startswith(b"OTTO"):
            return "font/otf"
        return "font/ttf"
    return _KIND_MIME.get(kind, "application/octet-stream")


def classify(path: str, head: bytes) -> Classified:
    """Signature first. An extension that claims a media type the bytes do not have falls back to text."""
    ext = _ext(path)
    signature = sniff_kind(head)
    named = _ext_kind(ext)
    if signature:
        kind = signature
    elif named and (not head or not _is_text(head)):
        kind = named[0]
    elif ext == ".svg" and _is_text(head):
        kind = "svg"
    elif _is_text(head):
        kind = "text"
    else:
        kind = "binary" if head else "text"
    return Classified(kind, _mime_for(kind, ext, head, named))


def blocked_edit_reason(path: str) -> str | None:
    """Paths that must not be edited or committed, whatever the caller's project role."""
    from app.services.workspace_files import is_secret_path

    lowered = path.replace("\\", "/").lstrip("/").lower()
    if lowered.startswith(WORKFLOW_PREFIX) or "/.github/workflows/" in f"/{lowered}":
        return "Workflow files can't be edited here."
    name = lowered.rsplit("/", 1)[-1]
    if name in {".env.example", ".env.sample", ".env.template", ".env.dist", ".env.defaults", "example.env", "sample.env"}:
        return None
    if is_secret_path(path):
        return "Secret files can't be edited or committed."
    return None


def preview_limit(kind: str) -> int | None:
    return {"image": IMAGE_MAX_BYTES, "svg": IMAGE_MAX_BYTES, "pdf": PDF_MAX_BYTES, "font": FONT_MAX_BYTES,
            "text": TEXT_PREVIEW_BYTES}.get(kind)


def delivery(info: Classified, path: str, download: bool) -> tuple[str, bool]:
    """(media type, inline). Active content is plain text, and a download is always an attachment."""
    ext = _ext(path)
    if download or info.kind in ("archive", "binary", "unknown"):
        return "application/octet-stream", False
    if info.kind == "svg" or ext in ACTIVE_EXTENSIONS:
        return "text/plain", True
    if info.kind in ("image", "pdf", "audio", "video", "font"):
        return info.mime, True
    return "text/plain", True


def dimensions(head: bytes) -> tuple[int, int] | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    if head.startswith((b"GIF87a", b"GIF89a")) and len(head) >= 10:
        return int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little")
    return None
