"""
What a viewer is allowed to know about someone's work, used by profile lists and the recruiter board.

Private projects, hidden connections, and hidden activity never affect a number someone else can see.
Commits and pushes come from repositories imported from GitHub, credited to each person who made them
(see services.github_history). Projects imported before per-person history was kept credit the owner.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models.collaboration import ProjectCollaborator
from app.models.connection import Connection
from app.models.network import NetworkActivity
from app.models.profile import UserProfile
from app.models.project import Project
from app.models.user import User
from app.services.github_history import credit
from app.services.social import audience_open, profile_open_to, public_project_filter, visible_project_filter

SORTS = ("projects", "commits", "pushes", "connections", "activity")
_NO_PUSH = datetime.min.replace(tzinfo=timezone.utc)


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return _NO_PUSH
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _connection_count(db: Session, user_id: UUID) -> int:
    return db.query(Connection.id).filter(
        or_(Connection.requester_id == user_id, Connection.receiver_id == user_id),
        Connection.status == "accepted",
    ).count()


def _activity_count(db: Session, user_id: UUID) -> int:
    return db.query(NetworkActivity.id).filter(NetworkActivity.user_id == user_id).count()


def projects_for(db: Session, subject_id: UUID, viewer_id: UUID | None) -> list[Project]:
    """Every project this person owns that the viewer is allowed to open."""
    return (
        db.query(Project)
        .options(selectinload(Project.tech_stacks), selectinload(Project.owner))
        .filter(Project.owner_id == subject_id, visible_project_filter(viewer_id))
        .order_by(Project.updated_at.desc())
        .all()
    )


def _member(user: User, role: str, access: str, focus: str | None = None) -> dict:
    profile = user.profile
    return {
        "user_id": user.id,
        "username": user.username,
        "full_name": profile.full_name if profile else None,
        "avatar_url": profile.avatar_url if profile else None,
        "role": role,
        "access": access,
        "focus": focus,
    }


def _team(project: Project) -> list[dict]:
    members = [_member(project.owner, "Owner", "owner", project.owner_focus)]
    members += [_member(c.user, c.role, c.access, c.focus) for c in project.collaborators if c.user]
    return members


def _team_query(db: Session):
    return db.query(Project).options(
        selectinload(Project.owner).selectinload(User.profile),
        selectinload(Project.tech_stacks),
        selectinload(Project.collaborators).selectinload(ProjectCollaborator.user).selectinload(User.profile),
    )


def groups_for(db: Session, subject_id: UUID, viewer_id: UUID | None) -> list[dict]:
    """Teams (a project plus its members) this person owns or has joined, limited to what the viewer may see."""
    rows = (
        _team_query(db)
        .filter(
            or_(
                Project.owner_id == subject_id,
                Project.collaborators.any(ProjectCollaborator.user_id == subject_id),
            ),
            Project.collaborators.any(),
            visible_project_filter(viewer_id),
        )
        .order_by(Project.updated_at.desc())
        .all()
    )
    groups = []
    for project in rows:
        if project.owner_id == subject_id:
            role = "Owner"
        else:
            role = next((c.role for c in project.collaborators if c.user_id == subject_id), "Collaborator")
        groups.append({
            "project_id": project.id,
            "title": project.title,
            "visibility": project.visibility.value if hasattr(project.visibility, "value") else str(project.visibility),
            "role": role,
            "members": _team(project),
        })
    return groups


def joined_for(db: Session, subject_id: UUID, viewer_id: UUID | None) -> list[tuple[Project, ProjectCollaborator]]:
    """Projects this person joined (accepted memberships only) that the viewer may open, newest membership first."""
    rows = (
        _team_query(db)
        .join(ProjectCollaborator, ProjectCollaborator.project_id == Project.id)
        .filter(ProjectCollaborator.user_id == subject_id, visible_project_filter(viewer_id))
        .order_by(ProjectCollaborator.created_at.desc())
        .all()
    )
    return [(p, next(c for c in p.collaborators if c.user_id == subject_id)) for p in rows]


def joined_project(project: Project, membership: ProjectCollaborator, stats: dict) -> dict:
    category = project.category.value if hasattr(project.category, "value") else project.category
    visibility = project.visibility.value if hasattr(project.visibility, "value") else project.visibility
    owner = project.owner
    return {
        "id": project.id,
        "title": project.title,
        "short_description": project.short_description,
        "cover_image_url": project.cover_image_url,
        "tech_stacks": [t.name for t in project.tech_stacks],
        "category": str(category) if category else None,
        "github_url": project.github_url,
        "contribution_info": membership.role,
        "owner_username": owner.username,
        "owner_avatar_url": owner.profile.avatar_url if owner.profile else None,
        "visibility": str(visibility) if visibility else None,
        "access": membership.access,
        "focus": membership.focus,
        "joined_at": membership.created_at,
        "languages": [
            {"name": row["name"], "percentage": float(row.get("percentage") or 0)}
            for row in (project.language_stats or []) if isinstance(row, dict) and row.get("name")
        ][:6],
        "team": _team(project),
        **stats,
    }


def _languages(projects: list[Project]) -> list[dict]:
    totals: dict[str, float] = defaultdict(float)
    counted = 0
    for project in projects:
        stats = project.language_stats or []
        if not stats:
            continue
        counted += 1
        for row in stats:
            name = row.get("name") if isinstance(row, dict) else None
            if name:
                totals[name] += float(row.get("percentage") or 0)
    if not counted:
        return []
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:8]
    return [{"name": name, "percentage": round(total / counted, 1)} for name, total in ranked]


def _tech(projects: list[Project]) -> list[dict]:
    counts: dict[str, int] = defaultdict(int)
    for project in projects:
        for stack in project.tech_stacks:
            counts[stack.name] += 1
    return [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _github_projects(db: Session, project_filter) -> list[Project]:
    return db.query(Project).filter(
        project_filter,
        or_(Project.github_url.isnot(None), Project.github_commit_count.isnot(None), Project.github_push_count.isnot(None)),
    ).all()


def _contributions(credited: dict, projects: dict[UUID, Project]) -> list[dict]:
    rows = []
    for project_id, per in credited.get("projects", {}).items():
        project = projects.get(project_id)
        if project is None:
            continue
        total = max(project.github_commit_count or 0, per["commits"])
        rows.append({
            "project_id": project_id,
            "title": project.title,
            "owned": False,
            "commits": per["commits"],
            "pushes": per["pushes"],
            "share": round(per["commits"] / total * 100, 1) if total else 0,
        })
    rows.sort(key=lambda row: (row["commits"], row["pushes"]), reverse=True)
    return rows


def standing_for(db: Session, user: User, viewer_id: UUID | None) -> dict:
    """The graphic view. Numbers the viewer is not allowed to see come back empty rather than zero."""
    projects = projects_for(db, user.id, viewer_id)
    imported = _github_projects(db, visible_project_filter(viewer_id))
    mine = credit(db, imported).get(user.id, {})
    contributions = _contributions(mine, {p.id: p for p in imported})
    for row in contributions:
        row["owned"] = any(p.id == row["project_id"] for p in projects)
    connections = _connection_count(db, user.id) if audience_open(db, user, viewer_id, "connection_visibility", "public") else None
    activity = _activity_count(db, user.id) if audience_open(db, user, viewer_id, "activity_visibility", "public") else None
    return {
        "projects": len(projects),
        "commits": mine.get("commits", 0),
        "pushes": mine.get("pushes", 0),
        "connections": connections,
        "activity": activity,
        "last_push": mine.get("last_push"),
        "languages": _languages(projects),
        "tech": _tech(projects),
        "contributions": contributions,
        "weekly": sorted([week, count] for week, count in mine.get("weekly", {}).items()),
    }


def _matches_tech(user: User, projects: list[Project], needed: list[str]) -> bool:
    if not needed:
        return True
    names = {stack.name.lower() for project in projects for stack in project.tech_stacks}
    skills = (user.profile.skills or "").lower() if user.profile else ""
    return all(tech.lower() in names or tech.lower() in skills for tech in needed)


def _matches_query(user: User, query: str) -> bool:
    if not query:
        return True
    profile = user.profile
    haystack = " ".join(filter(None, [
        user.username,
        profile.full_name if profile else None,
        profile.headline if profile else None,
        profile.skills if profile else None,
        profile.company if profile else None,
        profile.role if profile else None,
    ])).lower()
    return query.lower() in haystack


def talent_board(db: Session, viewer_id: UUID | None, query: str, tech: list[str], sort: str, limit: int) -> list[dict]:
    """Discoverable people, ranked by work and activity the viewer is allowed to see."""
    public = (
        db.query(Project)
        .options(selectinload(Project.tech_stacks))
        .filter(public_project_filter(viewer_id))
        .all()
    )
    by_owner: dict[UUID, list[Project]] = defaultdict(list)
    for project in public:
        by_owner[project.owner_id].append(project)
    credited = credit(db, [p for p in public if p.github_url or p.github_commit_count or p.github_push_count])

    users = db.query(User).options(selectinload(User.profile)).outerjoin(User.profile).filter(
        or_(UserProfile.discoverable.is_(None), UserProfile.discoverable.is_(True)),
        or_(UserProfile.role.is_(None), UserProfile.role != "Recruiter"),
    ).all()

    cards = []
    for user in users:
        if viewer_id and user.id == viewer_id:
            continue
        projects = by_owner.get(user.id, [])
        if not _matches_query(user, query) or not _matches_tech(user, projects, tech):
            continue
        open_profile = profile_open_to(db, user.id, viewer_id)
        profile = user.profile
        show_connections = audience_open(db, user, viewer_id, "connection_visibility", "public")
        show_activity = audience_open(db, user, viewer_id, "activity_visibility", "public")
        connections = _connection_count(db, user.id) if show_connections else None
        activity = _activity_count(db, user.id) if show_activity else None
        work = credited.get(user.id, {})
        last_push: datetime | None = work.get("last_push")
        tech_names = sorted({stack.name for project in projects for stack in project.tech_stacks})
        cards.append({
            "id": user.id,
            "username": user.username,
            "full_name": None if not open_profile else (profile.full_name if profile else None),
            "headline": None if not open_profile else (profile.headline if profile else None),
            "role": profile.role if profile else None,
            "company": None if not open_profile else (profile.company if profile else None),
            "avatar_url": None if not open_profile else (profile.avatar_url if profile else None),
            "location": None if not open_profile else (profile.location if profile else None),
            "skills": None if not open_profile else (profile.skills if profile else None),
            "projects": 0 if not open_profile else len(projects),
            "commits": 0 if not open_profile else work.get("commits", 0),
            "pushes": 0 if not open_profile else work.get("pushes", 0),
            "connections": connections,
            "activity": activity,
            "last_push": None if not open_profile else last_push,
            "tech": [] if not open_profile else tech_names,
            "sort_connections": -1 if connections is None else connections,
            "sort_activity": -1 if activity is None else activity,
        })

    key = {
        "projects": lambda card: (card["projects"], card["commits"]),
        "commits": lambda card: (card["commits"], card["projects"]),
        "pushes": lambda card: (card["pushes"], _aware(card["last_push"]), card["projects"]),
        "connections": lambda card: (card["sort_connections"], card["projects"]),
        "activity": lambda card: (card["sort_activity"], card["projects"]),
    }[sort]
    cards.sort(key=key, reverse=True)
    for card in cards:
        card.pop("sort_connections")
        card.pop("sort_activity")
    return cards[:limit]


def field_top(db: Session, viewer_id: UUID | None) -> dict:
    """Highest visible count for each metric across the people this viewer can find."""
    cards = talent_board(db, viewer_id, "", [], "projects", limit=10_000)
    return {metric: max((card[metric] or 0 for card in cards), default=0) for metric in SORTS}
