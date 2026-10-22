"""Content types, headers and byte ranges for serving stored project files.

Uploaded files are untrusted. Only passive formats (raster images, PDF, audio and video) are
served inline; everything else, SVG and HTML included, is an attachment. Every response carries
nosniff and a sandboxing Content-Security-Policy, so a file opened directly in the browser cannot
run script with the API's origin.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from app.services.file_types import classify, delivery

SANDBOX_CSP = "default-src 'none'; sandbox"
RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class RangeNotSatisfiable(Exception):
    """The requested range starts past the end of the file."""


def content_type_for(path: str, head: bytes = b"") -> tuple[str, bool]:
    """(media type, whether it may be shown inline)."""
    return delivery(classify(path, head), path, download=False)


def file_headers(name: str, inline: bool) -> dict[str, str]:
    return {
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": SANDBOX_CSP,
        "Cache-Control": "private, no-store",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Referrer-Policy": "no-referrer",
        "Accept-Ranges": "bytes",
    }


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Inclusive (start, end) for a single byte range; None means send the whole file.

    Multiple ranges, other units and malformed values are ignored, which HTTP allows.
    """
    if not header or size <= 0:
        return None
    match = RANGE.fullmatch(header.strip())
    if not match or match.groups() == ("", ""):
        return None
    first, last = match.groups()
    if first:
        start = int(first)
        if last and int(last) < start:
            return None
        if start >= size:
            raise RangeNotSatisfiable()
        return start, min(int(last), size - 1) if last else size - 1
    suffix = int(last)
    if suffix == 0:
        raise RangeNotSatisfiable()
    return max(0, size - suffix), size - 1
