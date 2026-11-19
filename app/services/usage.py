"""Turn a path into a named visit, and summarize those visits for the admin area.

The stored name is a short key. The address, the query string, and anything a person typed are not kept.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.usage import UsageEvent
from app.models.user import User

# key, label shown in the admin area
VISIT_LABELS: tuple[tuple[str, str], ...] = (
    ("visit.home", "Home"),
    ("visit.gallery", "Gallery"),
    ("visit.tv", "TV World"),
    ("visit.cards", "Cards"),
    ("visit.catalog", "Catalog"),
    ("visit.developers", "Developers"),
    ("visit.network", "Network"),
    ("visit.connections", "Connections"),
    ("visit.post", "A post"),
    ("visit.messages", "Messages"),
    ("visit.profile", "Profile"),
    ("visit.project", "A project"),
    ("visit.workspace", "Project workspace"),
    ("visit.activity", "Activity"),
    ("visit.wrap", "Monthly wrap"),
    ("visit.add_project", "Add project"),
    ("visit.import", "GitHub import"),
    ("visit.settings", "Settings"),
    ("visit.opportunities", "Opportunities"),
    ("visit.assessments", "Assessments"),
    ("visit.recruit", "Recruiting"),
    ("visit.about", "About"),
    ("visit.other", "Other pages"),
)

SCROLL_LABELS = {
    "scroll.horizontal": "Horizontal",
    "scroll.vertical": "Vertical",
}

_LABELS = dict(VISIT_LABELS)
_LABELS.update(SCROLL_LABELS)
_SKIP = ("/admin", "/auth", "/login", "/register", "/forgot-password", "/reset-password")


def _clean_path(path: str) -> str | None:
    text = (path or "").split("?", 1)[0].split("#", 1)[0].strip()
    if not text.startswith("/") or ".." in text or len(text) > 180 or " " in text:
        return None
    if len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text


def event_for_path(path: str) -> str | None:
    """The visit key for a page, or None when the page should not be counted."""
    clean = _clean_path(path)
    if clean is None or clean.startswith(_SKIP):
        return None
    if clean.startswith("/project/") and clean.endswith("/workspace"):
        return "visit.workspace"
    if clean.endswith("/activity"):
        return "visit.activity"
    exact = {
        "/": "visit.home",
        "/home": "visit.home",
        "/explore": "visit.gallery",
        "/explore/studio": "visit.gallery",
        "/explore/tv": "visit.tv",
        "/explore/cards": "visit.cards",
        "/explore/catalog": "visit.catalog",
        "/explore/folio": "visit.catalog",
        "/explore/lanterns": "visit.catalog",
        "/explore/developers": "visit.developers",
        "/network": "visit.network",
        "/network/connections": "visit.connections",
        "/messages": "visit.messages",
        "/settings": "visit.settings",
        "/wrap": "visit.wrap",
        "/add-project": "visit.add_project",
        "/import/github": "visit.import",
        "/opportunities": "visit.opportunities",
        "/assessments": "visit.assessments",
        "/recruit": "visit.recruit",
        "/about-developers": "visit.about",
        "/profile": "visit.profile",
    }
    if clean in exact:
        return exact[clean]
    if clean.startswith("/network/post/"):
        return "visit.post"
    if clean.startswith("/project/"):
        return "visit.project"
    if clean.startswith("/profile/"):
        return "visit.profile"
    if clean.startswith("/recruit"):
        return "visit.recruit"
    if clean.startswith("/assessments"):
        return "visit.assessments"
    if clean.startswith("/network"):
        return "visit.network"
    if clean.startswith("/import"):
        return "visit.import"
    return "visit.other"


def event_for_scroll(mode: str) -> str | None:
    if mode == "horizontal":
        return "scroll.horizontal"
    if mode == "vertical":
        return "scroll.vertical"
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(db: Session, user: User, name: str | None) -> bool:
    """Store one visit. Repeats of the same key within a few seconds are ignored."""
    if name not in _LABELS:
        return False
    since = _now() - timedelta(seconds=20)
    recent = (
        db.query(UsageEvent.id)
        .filter(UsageEvent.user_id == user.id, UsageEvent.name == name, UsageEvent.created_at >= since)
        .first()
    )
    if recent is not None:
        return False
    minute_ago = _now() - timedelta(minutes=1)
    burst = db.query(UsageEvent.id).filter(UsageEvent.user_id == user.id, UsageEvent.created_at >= minute_ago).count()
    if burst >= 40:
        return False
    db.add(UsageEvent(user_id=user.id, name=name))
    db.commit()
    return True


def _counts(db: Session, since: datetime) -> dict[str, tuple[int, int]]:
    rows = (
        db.query(UsageEvent.name, func.count(UsageEvent.id), func.count(func.distinct(UsageEvent.user_id)))
        .filter(UsageEvent.created_at >= since)
        .group_by(UsageEvent.name)
        .all()
    )
    return {name: (int(total), int(people)) for name, total, people in rows}


def _stat(counts: dict[str, tuple[int, int]], key: str, label: str) -> dict:
    total, people = counts.get(key, (0, 0))
    return {"key": key, "label": label, "count": total, "people": people}


def summarize(db: Session, days: int) -> dict:
    days = min(max(days, 1), 90)
    since = _now() - timedelta(days=days)
    counts = _counts(db, since)
    pages = [_stat(counts, key, label) for key, label in VISIT_LABELS if counts.get(key, (0, 0))[0] > 0]
    pages.sort(key=lambda item: (-item["count"], item["label"]))
    visits = sum(item["count"] for item in pages)
    people = (
        db.query(func.count(func.distinct(UsageEvent.user_id)))
        .filter(UsageEvent.created_at >= since, UsageEvent.name.like("visit.%"))
        .scalar()
    )
    return {
        "days": days,
        "visits": int(visits),
        "people": int(people or 0),
        "pages": pages,
        "gallery": _stat(counts, "visit.gallery", "Gallery"),
        "tv": _stat(counts, "visit.tv", "TV World"),
        "horizontal": _stat(counts, "scroll.horizontal", "Horizontal"),
        "vertical": _stat(counts, "scroll.vertical", "Vertical"),
    }
