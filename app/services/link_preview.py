"""SSRF-safe link preview fetching with DevZ-Go project URL detection."""

from __future__ import annotations

import ipaddress
import re
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.models.link_preview import LinkPreview
from app.models.project import Project
from app.services.social import display_name

_PROJECT_PATH = re.compile(r"^/project/([0-9a-fA-F-]{36})/?$")
_MAX_BYTES = 512_000
_TIMEOUT = 5.0


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: Optional[str] = None
        self.description: Optional[str] = None
        self.image: Optional[str] = None
        self._in_title = False
        self._title_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs_d = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "title":
            self._in_title = True
            self._title_buf = []
        if tag.lower() == "meta":
            prop = (attrs_d.get("property") or attrs_d.get("name") or "").lower()
            content = attrs_d.get("content") or ""
            if prop in ("og:title", "twitter:title") and content and not self.title:
                self.title = content.strip()[:500]
            elif prop in ("og:description", "description", "twitter:description") and content and not self.description:
                self.description = content.strip()[:1000]
            elif prop in ("og:image", "twitter:image") and content and not self.image:
                self.image = content.strip()[:1000]

    def handle_endtag(self, tag):
        if tag.lower() == "title" and self._in_title:
            self._in_title = False
            if not self.title and self._title_buf:
                self.title = "".join(self._title_buf).strip()[:500]

    def handle_data(self, data):
        if self._in_title:
            self._title_buf.append(data)


def _normalize(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http(s) URLs are supported")
    if not parsed.netloc:
        raise ValueError("Invalid URL")
    # Strip fragment
    return parsed._replace(fragment="").geturl()


def _assert_public_host(hostname: str) -> None:
    host = (hostname or "").strip("[]").lower()
    if not host or host in {"localhost", "metadata.google.internal"}:
        raise ValueError("URL host is not allowed")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError("Could not resolve host") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("URL resolves to a private or reserved address")


def _internal_project(db: Session, url: str) -> Optional[dict]:
    parsed = urlparse(url)
    path = parsed.path or ""
    # Support absolute app paths and same-origin project links
    m = _PROJECT_PATH.match(path)
    if not m and "/project/" in path:
        tail = path.split("/project/", 1)[-1].strip("/")
        if re.fullmatch(r"[0-9a-fA-F-]{36}", tail):
            m = type("M", (), {"group": lambda _s, i: tail})()
    if not m:
        return None
    try:
        pid = UUID(m.group(1))
    except Exception:
        return None
    project = db.query(Project).filter(Project.id == pid).first()
    if not project:
        return None
    owner = project.owner
    return {
        "url": url,
        "title": project.title,
        "description": project.short_description,
        "image_url": project.cover_image_url,
        "domain": parsed.netloc or "devzgo",
        "kind": "project",
        "project_id": str(project.id),
        "creator": display_name(owner) if owner else None,
    }


def get_link_preview(db: Session, url: str) -> dict:
    normalized = _normalize(url)
    cached = db.query(LinkPreview).filter(LinkPreview.normalized_url == normalized).first()
    if cached and cached.fetched_at:
        age = (datetime.now(timezone.utc) - cached.fetched_at.replace(tzinfo=timezone.utc)).total_seconds()
        if age < 86400:
            return {
                "url": cached.url,
                "title": cached.title,
                "description": cached.description,
                "image_url": cached.image_url,
                "domain": cached.domain,
                "kind": cached.kind,
                "project_id": str(cached.project_id) if cached.project_id else None,
            }

    internal = _internal_project(db, normalized)
    if internal:
        _upsert(db, normalized, internal)
        return internal

    parsed = urlparse(normalized)
    _assert_public_host(parsed.hostname or "")

    headers = {"User-Agent": "DevZGoLinkPreview/1.0", "Accept": "text/html,application/xhtml+xml"}
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True, max_redirects=3) as client:
        # Re-check redirect hosts
        resp = client.get(normalized, headers=headers)
        final = str(resp.url)
        final_parsed = urlparse(final)
        if final_parsed.scheme not in ("http", "https"):
            raise ValueError("Redirected to unsupported scheme")
        _assert_public_host(final_parsed.hostname or "")
        ctype = (resp.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text/" not in ctype:
            raise ValueError("URL did not return HTML")
        body = resp.content[:_MAX_BYTES].decode(resp.encoding or "utf-8", errors="ignore")

    parser = _MetaParser()
    parser.feed(body)
    payload = {
        "url": final,
        "title": parser.title,
        "description": parser.description,
        "image_url": parser.image,
        "domain": final_parsed.netloc,
        "kind": "external",
        "project_id": None,
    }
    _upsert(db, normalized, payload)
    return payload


def _upsert(db: Session, normalized: str, payload: dict) -> None:
    row = db.query(LinkPreview).filter(LinkPreview.normalized_url == normalized).first()
    project_id = None
    if payload.get("project_id"):
        try:
            project_id = UUID(str(payload["project_id"]))
        except Exception:
            project_id = None
    if not row:
        row = LinkPreview(url=payload["url"], normalized_url=normalized)
        db.add(row)
    row.url = payload["url"]
    row.title = payload.get("title")
    row.description = payload.get("description")
    row.image_url = payload.get("image_url")
    row.domain = payload.get("domain")
    row.kind = payload.get("kind") or "external"
    row.project_id = project_id
    row.fetched_at = datetime.now(timezone.utc)
    db.commit()
