"""Canonical public URLs for OAuth redirects, password-reset links, and file streams.

Local defaults stay for docker-compose. On Render, BACKEND_URL falls back to
RENDER_EXTERNAL_URL so stream/OAuth callbacks do not silently become localhost.
FRONTEND_URL must be set explicitly for production (Pages origin).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("uvicorn.error")


def _is_local(url: str) -> bool:
    return "localhost" in url or "127.0.0.1" in url


def frontend_base() -> str:
    explicit = (os.getenv("FRONTEND_URL") or "").rstrip("/")
    if explicit and not (_is_local(explicit) and os.getenv("RENDER", "").lower() in {"true", "1"}):
        return explicit
    # On Render, never fall back to localhost for reset links / OAuth returns.
    if os.getenv("RENDER", "").lower() in {"true", "1"}:
        return explicit or "https://www.devzgo.com"
    return explicit or "http://localhost:5173"


def backend_base() -> str:
    """Public API origin for OAuth callbacks and file stream links.

    Prefer BACKEND_URL when it is a real public URL. On Render, ignore a localhost
    BACKEND_URL (common when .env was copied) and use RENDER_EXTERNAL_URL instead.
    """
    explicit = (os.getenv("BACKEND_URL") or "").rstrip("/")
    render = (os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    on_render = os.getenv("RENDER", "").lower() in {"true", "1"}
    if explicit and not (on_render and _is_local(explicit)):
        return explicit
    if render:
        return render
    return explicit or "http://localhost:8000"


def warn_if_localhost_in_production() -> None:
    """Log once at startup when hosted deploy still points at localhost."""
    if os.getenv("RENDER", "").lower() not in {"true", "1"}:
        return
    fe, be = frontend_base(), backend_base()
    if "localhost" in fe or "127.0.0.1" in fe:
        log.warning(
            "FRONTEND_URL is %s — set it to your Cloudflare Pages origin "
            "(CORS, OAuth redirects, password-reset links).",
            fe,
        )
    if "localhost" in be or "127.0.0.1" in be:
        log.warning(
            "BACKEND_URL is %s — set it to https://devzgo-backend.onrender.com "
            "(or rely on RENDER_EXTERNAL_URL).",
            be,
        )
