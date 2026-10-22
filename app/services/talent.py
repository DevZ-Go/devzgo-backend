"""
What a viewer is allowed to know about someone's work, used by profile lists and the recruiter board.

Private projects, hidden connections, and hidden activity never affect a number someone else can see.
Commits and pushes come from repositories imported from GitHub, credited to each person who made them
(see services.github_history). Projects imported before per-person history was kept credit the owner.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models.collaboration import ProjectCollaborator
from app.models.connection import Connection
from app.models.file import File as FileRecord
from app.models.network import NetworkActivity
from app.models.profile import UserProfile
from app.models.project import Project
from app.models.user import User
from app.services.github_history import credit
from app.services.social import audience_open, profile_open_to, public_project_filter, visible_project_filter

METRICS = ("projects", "commits", "pushes", "connections", "activity")
SORTS = ("relevance", "projects", "commits", "pushes", "recent", "connections", "activity")
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


@dataclass
class TalentQuery:
    """Filters a recruiter can combine. Every filter only looks at data this viewer is allowed to see."""
    query: str = ""
    tech: list[str] = field(default_factory=list)
    evidence: str = "any"  # any: project tech or listed skills; projects: only tech used in visible projects
    min_projects: int = 0  # visible projects using any requested tech (or any visible project with no tech)
    github: bool = False  # has commits credited from imported repositories
    source: bool = False  # has a visible project with uploaded or linked source code
    open_to_work: bool = False
    work_type: str = ""
    arrangement: str = ""
    sort: str = "relevance"


def _list(value: str | None) -> list[str]:
    return [part for part in (value or "").split(",") if part]


def _matches_query(user: User, query: str, open_profile: bool) -> bool:
    if not query:
        return True
    profile = user.profile if open_profile else None
    haystack = " ".join(filter(None, [
        user.username,
        profile.full_name if profile else None,
        profile.headline if profile else None,
        profile.skills if profile else None,
        profile.location if profile else None,
    ])).lower()
    return query.lower() in haystack


def source_project_ids(db: Session, projects: list[Project]) -> set[UUID]:
    ids = [p.id for p in projects]
    uploaded = {row[0] for row in db.query(FileRecord.project_id).filter(FileRecord.project_id.in_(ids)).distinct()} if ids else set()
    return uploaded | {p.id for p in projects if p.github_url}


def _availability(profile: UserProfile | None, open_profile: bool) -> tuple[bool, list[str], list[str]]:
    if not open_profile or profile is None or not profile.open_to_work:
        return False, [], []
    return True, _list(profile.work_types), _list(profile.work_arrangements)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def talent_board(db: Session, viewer_id: UUID | None, q: TalentQuery, limit: int) -> list[dict]:
    """Discoverable developers, filtered and ordered by evidence this viewer is allowed to see.

    There is no overall score: each sort is one visible measure, and each card lists why it matched.
    Someone without imported GitHub history is shown as "not imported", never as inactive.
    """
    public = (
        db.query(Project)
        .options(selectinload(Project.tech_stacks))
        .filter(public_project_filter(viewer_id))
        .all()
    )
    by_owner: dict[UUID, list[Project]] = defaultdict(list)
    for project in public:
        by_owner[project.owner_id].append(project)
    imported = [p for p in public if p.github_url or p.github_commit_count or p.github_push_count]
    credited = credit(db, imported)
    with_source = source_project_ids(db, public)
    needed = [t.lower() for t in q.tech]

    users = db.query(User).options(selectinload(User.profile)).outerjoin(User.profile).filter(
        or_(UserProfile.discoverable.is_(None), UserProfile.discoverable.is_(True)),
        User.account_type != "recruiter",
    ).all()

    cards = []
    for user in users:
        if viewer_id and user.id == viewer_id:
            continue
        open_profile = profile_open_to(db, user.id, viewer_id)
        profile = user.profile
        projects = by_owner.get(user.id, []) if open_profile else []
        if not _matches_query(user, q.query, open_profile):
            continue

        project_tech = {stack.name.lower() for project in projects for stack in project.tech_stacks}
        skills = (profile.skills or "").lower() if open_profile and profile else ""
        in_projects = [t for t in needed if t in project_tech]
        in_skills = [t for t in needed if t not in project_tech and t in skills]
        if needed and (len(in_projects) + (0 if q.evidence == "projects" else len(in_skills))) < len(needed):
            continue
        relevant = [p for p in projects if not needed or any(s.name.lower() in needed for s in p.tech_stacks)]
        if len(relevant) < q.min_projects:
            continue
        source_count = sum(1 for p in projects if p.id in with_source)
        if q.source and not source_count:
            continue
        work = credited.get(user.id, {}) if open_profile else {}
        github_history = bool(work)
        if q.github and not work.get("commits"):
            continue
        open_to_work, work_types, arrangements = _availability(profile, open_profile)
        if q.open_to_work and not open_to_work:
            continue
        if q.work_type and q.work_type not in work_types:
            continue
        if q.arrangement and q.arrangement not in arrangements:
            continue

        show_connections = audience_open(db, user, viewer_id, "connection_visibility", "public")
        show_activity = audience_open(db, user, viewer_id, "activity_visibility", "public")
        connections = _connection_count(db, user.id) if show_connections else None
        activity = _activity_count(db, user.id) if show_activity else None
        last_push: datetime | None = work.get("last_push")
        names = {stack.name.lower(): stack.name for project in projects for stack in project.tech_stacks}

        match: list[str] = []
        for tech in in_projects:
            used = sum(1 for p in projects if any(s.name.lower() == tech for s in p.tech_stacks))
            match.append(f"Uses {names.get(tech, tech)} in {_plural(used, 'public project')}")
        for tech in in_skills:
            match.append(f"Lists {tech} as a skill (no public project shows it yet)")
        if q.query:
            match.append(f"Profile mentions \u201c{q.query}\u201d")
        if source_count:
            match.append(f"{_plural(source_count, 'project')} with source code to review")
        if github_history:
            match.append(f"{_plural(work.get('commits', 0), 'commit')} credited from imported GitHub repositories")
        elif projects:
            match.append("No GitHub history imported (not counted against them)")
        if open_to_work:
            match.append("Open to work" + (f": {', '.join(work_types)}" if work_types else ""))

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
            "projects": len(projects),
            "relevant_projects": len(relevant),
            "source_projects": source_count,
            "commits": work.get("commits", 0),
            "pushes": work.get("pushes", 0),
            "github_history": github_history,
            "connections": connections,
            "activity": activity,
            "last_push": last_push,
            "tech": sorted(names.values()),
            "open_to_work": open_to_work,
            "work_types": work_types,
            "work_arrangements": arrangements,
            "restricted": not open_profile,
            "match": match,
            "_tech_hits": len(in_projects) * 2 + len(in_skills),
            "_connections": -1 if connections is None else connections,
            "_activity": -1 if activity is None else activity,
        })

    key = {
        "relevance": lambda c: (c["_tech_hits"], c["relevant_projects"], c["source_projects"], c["github_history"]),
        "projects": lambda c: (c["projects"], c["commits"]),
        "commits": lambda c: (c["github_history"], c["commits"], c["projects"]),
        "pushes": lambda c: (c["github_history"], c["pushes"], _aware(c["last_push"]), c["projects"]),
        "recent": lambda c: (c["last_push"] is not None, _aware(c["last_push"]), c["projects"]),
        "connections": lambda c: (c["_connections"], c["projects"]),
        "activity": lambda c: (c["_activity"], c["projects"]),
    }[q.sort]
    cards.sort(key=key, reverse=True)
    for card in cards:
        for private in ("_tech_hits", "_connections", "_activity"):
            card.pop(private)
    return cards[:limit]


def field_top(db: Session, viewer_id: UUID | None) -> dict:
    """Highest visible count for each metric across the people this viewer can find."""
    cards = talent_board(db, viewer_id, TalentQuery(sort="projects"), limit=10_000)
    return {metric: max((card[metric] or 0 for card in cards), default=0) for metric in METRICS}
