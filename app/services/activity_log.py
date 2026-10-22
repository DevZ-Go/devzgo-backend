"""
Activity log: every stored commit and push from repositories imported from GitHub, as one timeline.

A person's log covers their own work across every imported project the viewer may open, credited the same
way as everywhere else (the GitHub account that imported a project, a linked GitHub account, or a matching
commit email). The team view and a project's log cover everyone's work on those projects.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.collaboration import ProjectCollaborator
from app.models.github_history import ProjectCommit, ProjectPush
from app.models.project import Project
from app.models.user import User
from app.services import github_history as history
from app.services.social import user_summary, visible_project_filter

KINDS = ("all", "commits", "pushes")
HEATMAP_DAYS = 371  # 53 full weeks
MAX_OFFSET = 5000


@dataclass
class LogQuery:
    kind: str = "all"
    q: str = ""
    project_id: Optional[UUID] = None
    day: Optional[date] = None
    offset: int = 0
    limit: int = 30
    tz: str = "UTC"


def zone(name: str | None) -> str:
    try:
        ZoneInfo(name or "UTC")
        return name or "UTC"
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"


def repo_url(project: Project) -> str | None:
    url = (project.github_url or "").strip().rstrip("/")
    return url.removesuffix(".git") or None


def commits_by(user: User):
    """SQL condition matching the commits credited to this member (see github_history.Matcher)."""
    author = ProjectCommit.author_github_id
    conditions = [and_(Project.owner_id == user.id, Project.github_owner_id.isnot(None), author == Project.github_owner_id)]
    if user.github_id:
        conditions.append(and_(author == user.github_id, or_(
            Project.github_owner_id.is_(None), Project.github_owner_id != author, Project.owner_id == user.id)))
    if user.email:
        linked = select(User.github_id).where(User.github_id.isnot(None))
        conditions.append(and_(
            func.lower(ProjectCommit.author_email) == user.email.lower(),
            or_(author.is_(None), and_(author.notin_(linked), author != func.coalesce(Project.github_owner_id, ""))),
        ))
    return or_(*conditions)


def pushes_by(user: User):
    actor = ProjectPush.actor_github_id
    conditions = [and_(Project.owner_id == user.id, Project.github_owner_id.isnot(None), actor == Project.github_owner_id)]
    if user.github_id:
        conditions.append(and_(actor == user.github_id, or_(
            Project.github_owner_id.is_(None), Project.github_owner_id != actor, Project.owner_id == user.id)))
    return or_(*conditions)


def team_projects(user_id: UUID):
    """Projects this person owns or has joined."""
    return or_(Project.owner_id == user_id, Project.collaborators.any(ProjectCollaborator.user_id == user_id))


def _streaks(days: list[date]) -> tuple[int, int]:
    """Longest run of consecutive active days, and the run ending today or yesterday."""
    if not days:
        return 0, 0
    longest = run = 1
    for before, after in zip(days, days[1:]):
        run = run + 1 if after - before == timedelta(days=1) else 1
        longest = max(longest, run)
    current = 0
    today = datetime.now(timezone.utc).date()
    if days[-1] >= today - timedelta(days=1):
        current = 1
        for before, after in zip(reversed(days[:-1]), reversed(days[1:])):
            if after - before != timedelta(days=1):
                break
            current += 1
    return longest, current


def build(db: Session, viewer_id: Optional[UUID], projects_clause, commit_clause, push_clause, query: LogQuery) -> dict:
    """The log for the imported projects matching projects_clause that the viewer may open."""
    tz = zone(query.tz)
    projects = (
        db.query(Project)
        .filter(Project.github_url.isnot(None), projects_clause, visible_project_filter(viewer_id))
        .all()
    )
    by_id = {p.id: p for p in projects}
    every = list(by_id)
    ids = every
    if query.project_id is not None:
        ids = [query.project_id] if query.project_id in by_id else []

    def commits(within=None):
        rows = (db.query(ProjectCommit).join(Project, Project.id == ProjectCommit.project_id)
                .filter(ProjectCommit.project_id.in_(ids if within is None else within)))
        return rows.filter(commit_clause) if commit_clause is not None else rows

    def pushes(within=None):
        rows = (db.query(ProjectPush).join(Project, Project.id == ProjectPush.project_id)
                .filter(ProjectPush.project_id.in_(ids if within is None else within)))
        return rows.filter(push_clause) if push_clause is not None else rows

    commit_day = func.date(func.timezone(tz, ProjectCommit.committed_at))
    push_day = func.date(func.timezone(tz, ProjectPush.pushed_at))
    daily: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    if ids:
        for day, n in commits().filter(ProjectCommit.committed_at.isnot(None)).with_entities(commit_day, func.count()).group_by(commit_day):
            daily[day][0] += n
        for day, n in pushes().filter(ProjectPush.pushed_at.isnot(None)).with_entities(push_day, func.count()).group_by(push_day):
            daily[day][1] += n

    per_project: dict[UUID, dict] = {}
    if every:
        for pid, n, last in commits(every).with_entities(ProjectCommit.project_id, func.count(), func.max(ProjectCommit.committed_at)).group_by(ProjectCommit.project_id):
            per_project.setdefault(pid, {"commits": 0, "pushes": 0, "last_at": None})
            per_project[pid].update(commits=n, last_at=last)
        for pid, n, last in pushes(every).with_entities(ProjectPush.project_id, func.count(), func.max(ProjectPush.pushed_at)).group_by(ProjectPush.project_id):
            row = per_project.setdefault(pid, {"commits": 0, "pushes": 0, "last_at": None})
            row["pushes"] = n
            if last and (row["last_at"] is None or last > row["last_at"]):
                row["last_at"] = last

    days = sorted(daily)
    longest, current = _streaks(days)
    weekday = [0] * 7
    for day, (c, p) in daily.items():
        weekday[day.weekday()] += c + p
    since = datetime.now(ZoneInfo(tz)).date() - timedelta(days=HEATMAP_DAYS - 1)
    with_history = {pid for pid, row in per_project.items() if (row["commits"] or row["pushes"]) and pid in ids}
    stored = set()
    if ids:
        stored = {r[0] for r in db.query(ProjectCommit.project_id).filter(ProjectCommit.project_id.in_(ids)).distinct()}
        stored |= {r[0] for r in db.query(ProjectPush.project_id).filter(ProjectPush.project_id.in_(ids)).distinct()}

    summary = {
        "commits": sum(c for c, _ in daily.values()),
        "pushes": sum(p for _, p in daily.values()),
        "projects": len(with_history),
        "active_days": len(days),
        "longest_streak": longest,
        "current_streak": current,
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "busiest_weekday": weekday.index(max(weekday)) if any(weekday) else None,
        "weekdays": weekday,
        "legacy_projects": sum(1 for p in projects if p.id not in stored and (p.github_commit_count or p.github_push_count)),
    }
    summary["contributors"] = 0
    if ids:
        author = func.coalesce(ProjectCommit.author_github_id, func.lower(ProjectCommit.author_email), ProjectCommit.author_name)
        summary["contributors"] = commits().with_entities(func.count(func.distinct(author))).scalar() or 0
        undated = commits().filter(ProjectCommit.committed_at.is_(None)).count()
        summary["commits"] += undated
        summary["pushes"] += pushes().filter(ProjectPush.pushed_at.is_(None)).count()

    items, total = _timeline(db, query, commits, pushes, by_id, ids, commit_day, push_day)
    breakdown = sorted(
        ({"id": pid, "title": by_id[pid].title, "github_url": repo_url(by_id[pid]), **row} for pid, row in per_project.items()),
        key=lambda r: (r["commits"] + r["pushes"], r["last_at"] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True,
    )
    return {
        "timezone": tz,
        "summary": summary,
        "heatmap": [[d, c, p] for d, (c, p) in sorted(daily.items()) if d >= since],
        "heatmap_start": since,
        "projects": breakdown,
        "total": total,
        "items": items,
    }


def _timeline(db: Session, query: LogQuery, commits, pushes, by_id: dict, ids: list, commit_day, push_day) -> tuple[list[dict], int]:
    if not ids:
        return [], 0
    offset = min(query.offset, MAX_OFFSET)
    want = offset + query.limit
    text = query.q.strip()
    commit_rows, push_rows = [], []
    total = 0
    if query.kind in ("all", "commits"):
        rows = commits()
        if text:
            rows = rows.filter(or_(ProjectCommit.message.ilike(f"%{text}%"), Project.title.ilike(f"%{text}%"),
                                   ProjectCommit.sha.ilike(f"{text}%")))
        if query.day:
            rows = rows.filter(commit_day == query.day)
        total += rows.count()
        commit_rows = rows.order_by(ProjectCommit.committed_at.desc().nullslast(), ProjectCommit.sha).limit(want).all()
    if query.kind in ("all", "pushes"):
        rows = pushes()
        if text:
            rows = rows.filter(or_(ProjectPush.branch.ilike(f"%{text}%"), Project.title.ilike(f"%{text}%")))
        if query.day:
            rows = rows.filter(push_day == query.day)
        total += rows.count()
        push_rows = rows.order_by(ProjectPush.pushed_at.desc().nullslast(), ProjectPush.event_id).limit(want).all()

    floor = datetime.min.replace(tzinfo=timezone.utc)
    merged = sorted(
        [("commit", c.committed_at, c) for c in commit_rows] + [("push", p.pushed_at, p) for p in push_rows],
        key=lambda row: row[1] or floor, reverse=True,
    )[offset:want]

    page_projects = [by_id[row[2].project_id] for row in merged]
    match = history.matcher_for(db, page_projects, [],
                                pushes=[r[2] for r in merged if r[0] == "push"],
                                commits=[r[2] for r in merged if r[0] == "commit"])
    owners = {}
    for kind, _, row in merged:
        uid = (match.user(row.project_id, row.author_github_id, row.author_email) if kind == "commit"
               else match.user(row.project_id, row.actor_github_id))
        owners[id(row)] = uid
    people = {}
    wanted = {uid for uid in owners.values() if uid}
    if wanted:
        users = db.query(User).options(selectinload(User.profile)).filter(User.id.in_(wanted)).all()
        people = {u.id: user_summary(u).model_dump(mode="json") for u in users}

    items = []
    for kind, at, row in merged:
        project = by_id[row.project_id]
        base = repo_url(project)
        person = people.get(owners[id(row)])
        common = {"type": kind, "at": at, "project": {"id": project.id, "title": project.title, "github_url": base}, "user": person}
        if kind == "commit":
            items.append({**common, "id": row.sha, "sha": row.sha, "message": row.message,
                          "login": row.author_login, "name": row.author_name or row.author_login,
                          "url": f"{base}/commit/{row.sha}" if base else None})
        else:
            items.append({**common, "id": row.event_id, "branch": row.branch, "commit_count": row.commit_count,
                          "login": row.actor_login, "name": row.actor_login,
                          "url": f"{base}/tree/{row.branch}" if base and row.branch else base})
    return items, total
