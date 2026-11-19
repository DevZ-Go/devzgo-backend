"""Turn a path into a named visit, and summarize those visits for the admin area.

The stored name is a short key. The address, the query string, and anything a person typed are not kept.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.usage import UsageEvent
from app.models.user import User

# key, label, area, kind. Every row is returned even when the count is zero.
FEATURES: tuple[tuple[str, str, str, str], ...] = (
    ("visit.gallery", "Gallery", "Explore", "page"),
    ("visit.tv", "TV World", "Explore", "page"),
    ("visit.cards", "Cards", "Explore", "page"),
    ("visit.catalog", "Catalog", "Explore", "page"),
    ("visit.developers", "Developers", "Explore", "page"),
    ("scroll.horizontal", "Horizontal scrolling", "Explore", "feature"),
    ("scroll.vertical", "Vertical scrolling", "Explore", "feature"),
    ("visit.home", "Home", "Community", "page"),
    ("visit.network", "Network", "Community", "page"),
    ("visit.connections", "Connections", "Community", "page"),
    ("visit.post", "A post", "Community", "page"),
    ("visit.messages", "Messages", "Community", "page"),
    ("visit.profile", "Profile", "Community", "page"),
    ("visit.about", "About", "Community", "page"),
    ("visit.project", "A project", "Projects", "page"),
    ("visit.workspace", "Project workspace", "Projects", "page"),
    ("visit.activity", "Activity", "Projects", "page"),
    ("visit.add_project", "Add project", "Projects", "page"),
    ("visit.import", "GitHub import", "Projects", "page"),
    ("visit.recruit", "Recruiting", "Recruiting", "page"),
    ("visit.opportunities", "Opportunities", "Recruiting", "page"),
    ("visit.assessments", "Assessments", "Recruiting", "page"),
    ("visit.wrap", "Monthly wrap", "Account", "page"),
    ("visit.settings", "Settings", "Account", "page"),
    ("visit.other", "Other pages", "Account", "page"),
)

_LABELS = {key: label for key, label, _group, _kind in FEATURES}
AREAS = ("Explore", "Community", "Projects", "Recruiting", "Account")
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


def _counts(db: Session, since: datetime | None, audience: str) -> dict[str, tuple[int, int]]:
    query = db.query(UsageEvent.name, func.count(UsageEvent.id), func.count(func.distinct(UsageEvent.user_id)))
    if audience in {"developer", "recruiter"}:
        query = query.join(User, User.id == UsageEvent.user_id).filter(User.account_type == audience)
    if since is not None:
        query = query.filter(UsageEvent.created_at >= since)
    rows = query.group_by(UsageEvent.name).all()
    return {name: (int(total), int(people)) for name, total, people in rows}


def _share(count: int, total: int) -> float:
    if total <= 0:
        return 0
    return round(count * 1000 / total) / 10


def summarize(db: Session, days: int, audience: str = "all") -> dict:
    """Every page and feature, with its own visit count. Zero means nobody used it in this stretch."""
    if audience not in {"all", "developer", "recruiter"}:
        audience = "all"
    bounded = 0 if days <= 0 else min(days, 365)
    since = None if bounded == 0 else _now() - timedelta(days=bounded)
    counts = _counts(db, since, audience)
    page_total = sum(counts.get(key, (0, 0))[0] for key, _label, _group, kind in FEATURES if kind == "page")
    feature_total = sum(counts.get(key, (0, 0))[0] for key, _label, _group, kind in FEATURES if kind == "feature")
    rows = []
    for key, label, group, kind in FEATURES:
        total, people = counts.get(key, (0, 0))
        base = page_total if kind == "page" else feature_total
        rows.append({
            "key": key,
            "label": label,
            "group": group,
            "kind": kind,
            "count": total,
            "people": people,
            "share": _share(total, base),
        })
    people_query = db.query(func.count(func.distinct(UsageEvent.user_id))).filter(UsageEvent.name.like("visit.%"))
    if audience in {"developer", "recruiter"}:
        people_query = people_query.join(User, User.id == UsageEvent.user_id).filter(User.account_type == audience)
    if since is not None:
        people_query = people_query.filter(UsageEvent.created_at >= since)
    return {
        "days": bounded,
        "audience": audience,
        "areas": list(AREAS),
        "visits": int(page_total),
        "people": int(people_query.scalar() or 0),
        "rows": rows,
    }
