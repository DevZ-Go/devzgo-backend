"""Keep a profile's skill list in step with the projects that person has published."""

from sqlalchemy.orm import Session, selectinload

from app.models.profile import UserProfile
from app.models.project import Project
from app.models.user import User


def merge_skills(existing: str | None, extra: list[str]) -> str | None:
    names: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        name = " ".join((raw or "").split())
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)

    for part in (existing or "").split(","):
        add(part)
    for name in extra:
        add(name)
    if not names:
        return None
    return ", ".join(names)[:1000]


def sync_skills_from_projects(db: Session, user: User) -> None:
    """Add tech and languages from this person's projects without dropping skills they typed."""
    projects = (
        db.query(Project)
        .options(selectinload(Project.tech_stacks))
        .filter(Project.owner_id == user.id)
        .all()
    )
    extra: list[str] = []
    for project in projects:
        extra.extend(tech.name for tech in project.tech_stacks)
        for item in project.language_stats or []:
            if isinstance(item, dict) and item.get("name"):
                extra.append(str(item["name"]))
    profile = user.profile
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
        user.profile = profile
    merged = merge_skills(profile.skills, extra)
    if (profile.skills or None) != merged:
        profile.skills = merged
