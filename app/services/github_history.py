"""
Per-person commit and push history for repositories imported from GitHub.

fetch()   reads contributor statistics, up to MAX_COMMIT_PAGES of commits, and recent push events with a short-lived token.
store()   replaces contributors and commits, adds push events that are new, and updates the project totals.
credit()  works out how many commits and pushes each DevZ-Go member made across a set of projects.

A GitHub author counts as a DevZ-Go member when it is the GitHub account that imported the project (the owner),
an account someone linked by signing in with GitHub, or a commit email matching a member's email.
Everyone else is shown by their GitHub name only.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable
from uuid import UUID

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.github_history import ProjectCommit, ProjectContributor, ProjectPush
from app.models.project import Project
from app.models.user import User
from app.services import github_import as gh

MAX_COMMIT_PAGES = 10
MAX_EVENT_PAGES = 3
STATS_TRIES = 5
STATS_WAIT = 2.0
WEEKS_KEPT = 52
_REPO = re.compile(r"github\.com/([^/\s]+)/([^/\s#?]+)", re.I)


def full_name_from_url(url: str | None) -> str | None:
    match = _REPO.search(url or "")
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"


def _time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _week(moment: datetime) -> int:
    """Start of the GitHub week (Sunday, UTC) as unix seconds."""
    day = moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((day - timedelta(days=(day.weekday() + 1) % 7)).timestamp())


def _email(value) -> str | None:
    value = (value or "").strip().lower()
    return value[:255] if value and "noreply" not in value else None


def fetch(token: str, full_name: str) -> dict:
    """Raw history from GitHub. Each part is left empty when GitHub refuses or has nothing yet."""
    data: dict = {"viewer": None, "stats": None, "commits": [], "pushes": []}
    with gh._client(token) as client:
        me = client.get("/user")
        if me.status_code == 200:
            data["viewer"] = str(me.json().get("id"))
        for _ in range(STATS_TRIES):
            res = client.get(f"/repos/{full_name}/stats/contributors")
            if res.status_code == 202:  # GitHub is still computing the statistics
                time.sleep(STATS_WAIT)
                continue
            if res.status_code == 200 and isinstance(res.json(), list):
                data["stats"] = res.json()
            break
        for page in range(1, MAX_COMMIT_PAGES + 1):
            res = client.get(f"/repos/{full_name}/commits", params={"per_page": 100, "page": page})
            if res.status_code != 200 or not isinstance(res.json(), list):
                break
            data["commits"].extend(res.json())
            if len(res.json()) < 100:
                break
        for page in range(1, MAX_EVENT_PAGES + 1):
            res = client.get(f"/repos/{full_name}/events", params={"per_page": 100, "page": page})
            if res.status_code != 200 or not isinstance(res.json(), list):
                break
            data["pushes"].extend(e for e in res.json() if e.get("type") == "PushEvent")
            if len(res.json()) < 100:
                break
    return data


def _commit_rows(raw: list[dict]) -> list[dict]:
    rows, seen = [], set()
    for item in raw:
        sha = item.get("sha")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        info = item.get("commit") or {}
        author = info.get("author") or {}
        account = item.get("author") or {}
        rows.append({
            "sha": sha[:40],
            "message": ((info.get("message") or "").strip().splitlines() or [""])[0][:300],
            "author_github_id": str(account["id"]) if account.get("id") else None,
            "author_login": account.get("login"),
            "author_name": (author.get("name") or "")[:200] or None,
            "author_email": _email(author.get("email")),
            "committed_at": _time(author.get("date")),
        })
    return rows


def _contributor_rows(stats: list[dict] | None, commits: list[dict]) -> list[dict]:
    """Whole-history statistics for GitHub accounts, plus commit-list counts for authors without one."""
    emails: dict[str, str] = {}
    names: dict[str, str] = {}
    latest: dict[str, datetime] = {}
    for c in commits:
        gid = c["author_github_id"]
        if gid:
            if c["author_email"]:
                emails.setdefault(gid, c["author_email"])
            if c["author_name"]:
                names.setdefault(gid, c["author_name"])
            if c["committed_at"] and (gid not in latest or c["committed_at"] > latest[gid]):
                latest[gid] = c["committed_at"]

    rows: list[dict] = []
    covered: set[str] = set()
    for s in stats or []:
        account = s.get("author") or {}
        if not account.get("id"):
            continue
        gid = str(account["id"])
        covered.add(gid)
        weeks = [w for w in s.get("weeks") or [] if isinstance(w, dict)]
        active = [w["w"] for w in weeks if w.get("c")]
        rows.append({
            "github_id": gid,
            "login": account.get("login"),
            "name": names.get(gid),
            "email": emails.get(gid),
            "avatar_url": account.get("avatar_url"),
            "commits": int(s.get("total") or 0),
            "additions": sum(int(w.get("a") or 0) for w in weeks),
            "deletions": sum(int(w.get("d") or 0) for w in weeks),
            "first_commit_at": datetime.fromtimestamp(min(active), timezone.utc) if active else None,
            "last_commit_at": latest.get(gid) or (datetime.fromtimestamp(max(active), timezone.utc) if active else None),
            "weekly": [[int(w["w"]), int(w.get("c") or 0)] for w in weeks[-WEEKS_KEPT:]],
        })

    groups: dict[str, list[dict]] = defaultdict(list)
    for c in commits:
        if c["author_github_id"] in covered:
            continue
        key = c["author_github_id"] or c["author_email"] or (c["author_name"] or "").lower()
        if key:
            groups[key].append(c)
    for group in groups.values():
        first = group[0]
        dates = [c["committed_at"] for c in group if c["committed_at"]]
        weekly: dict[int, int] = defaultdict(int)
        for d in dates:
            weekly[_week(d)] += 1
        rows.append({
            "github_id": first["author_github_id"],
            "login": first["author_login"],
            "name": first["author_name"],
            "email": next((c["author_email"] for c in group if c["author_email"]), None),
            "avatar_url": None,
            "commits": len(group),
            "additions": None,
            "deletions": None,
            "first_commit_at": min(dates) if dates else None,
            "last_commit_at": max(dates) if dates else None,
            "weekly": sorted([w, n] for w, n in weekly.items())[-WEEKS_KEPT:],
        })
    rows.sort(key=lambda r: r["commits"], reverse=True)
    return rows


def _push_rows(raw: list[dict]) -> list[dict]:
    rows = []
    for event in raw:
        if not event.get("id"):
            continue
        actor = event.get("actor") or {}
        payload = event.get("payload") or {}
        ref = payload.get("ref") or ""
        size = payload.get("size")
        rows.append({
            "event_id": str(event["id"])[:32],
            "actor_github_id": str(actor["id"]) if actor.get("id") else None,
            "actor_login": actor.get("login"),
            "branch": ref.removeprefix("refs/heads/")[:200] or None,
            "commit_count": size if isinstance(size, int) else len(payload.get("commits") or []) or None,
            "pushed_at": _time(event.get("created_at")),
        })
    return rows


def store(db: Session, project: Project, data: dict, commit_total: int | None = None) -> None:
    """Save fetched history on the project; the caller commits."""
    commits = _commit_rows(data.get("commits") or [])
    contributors = _contributor_rows(data.get("stats"), commits)
    db.query(ProjectContributor).filter(ProjectContributor.project_id == project.id).delete(synchronize_session=False)
    db.query(ProjectCommit).filter(ProjectCommit.project_id == project.id).delete(synchronize_session=False)
    db.add_all(ProjectContributor(project_id=project.id, **row) for row in contributors)
    db.add_all(ProjectCommit(project_id=project.id, **row) for row in commits)

    known = {r[0] for r in db.query(ProjectPush.event_id).filter(ProjectPush.project_id == project.id)}
    for row in _push_rows(data.get("pushes") or []):
        if row["event_id"] not in known:
            known.add(row["event_id"])
            db.add(ProjectPush(project_id=project.id, **row))
    db.flush()

    pushes = db.query(func.count(ProjectPush.id), func.max(ProjectPush.pushed_at)).filter(ProjectPush.project_id == project.id).one()
    counted = sum(row["commits"] for row in contributors)
    project.github_commit_count = max(commit_total or 0, counted) if (commit_total or counted) else project.github_commit_count
    project.github_push_count = pushes[0]
    if pushes[1] and (project.github_pushed_at is None or pushes[1] > project.github_pushed_at):
        project.github_pushed_at = pushes[1]
    if data.get("viewer"):
        project.github_owner_id = data["viewer"]
    project.github_synced_at = datetime.now(timezone.utc)


def capture(db: Session, project: Project, token: str, full_name: str, commit_total: int | None = None) -> bool:
    """fetch() and store(); False when GitHub could not be reached, leaving what was stored before."""
    try:
        data = fetch(token, full_name)
    except httpx.HTTPError:
        return False
    store(db, project, data, commit_total)
    return True


# ---------------- matching GitHub authors to members ----------------
class Matcher:
    """Finds the DevZ-Go member behind a GitHub author, for a set of projects."""

    def __init__(self, db: Session, projects: Iterable[Project], github_ids: set[str], emails: set[str]):
        self.owner_accounts = {p.id: (p.github_owner_id, p.owner_id) for p in projects}
        self.by_github: dict[str, UUID] = {}
        self.by_email: dict[str, UUID] = {}
        ids = {g for g in github_ids if g}
        if ids:
            self.by_github = {g: uid for uid, g in db.query(User.id, User.github_id).filter(User.github_id.in_(ids))}
        mails = {e for e in emails if e}
        if mails:
            self.by_email = {e.lower(): uid for uid, e in db.query(User.id, User.email).filter(func.lower(User.email).in_(mails))}

    def user(self, project_id: UUID, github_id: str | None, email: str | None = None) -> UUID | None:
        owner_github, owner_id = self.owner_accounts.get(project_id, (None, None))
        if github_id and github_id == owner_github:
            return owner_id
        if github_id and github_id in self.by_github:
            return self.by_github[github_id]
        if email and email.lower() in self.by_email:
            return self.by_email[email.lower()]
        return None


def matcher_for(db: Session, projects: list[Project], contributors: list[ProjectContributor],
                pushes: list[ProjectPush] = (), commits: list[ProjectCommit] = ()) -> Matcher:
    ids = {c.github_id for c in contributors} | {p.actor_github_id for p in pushes} | {c.author_github_id for c in commits}
    emails = {c.email for c in contributors} | {c.author_email for c in commits}
    return Matcher(db, projects, ids, emails)


def credit(db: Session, projects: list[Project]) -> dict[UUID, dict]:
    """Commits and pushes per member across these projects.

    Projects with stored history credit each matched author. Projects imported before history was kept
    credit their totals to the owner, as before.
    """
    totals: dict[UUID, dict] = defaultdict(
        lambda: {"commits": 0, "pushes": 0, "last_push": None, "projects": {}, "weekly": defaultdict(int)})
    ids = [p.id for p in projects]
    if not ids:
        return {}
    contributors = db.query(ProjectContributor).filter(ProjectContributor.project_id.in_(ids)).all()
    pushes = db.query(ProjectPush).filter(ProjectPush.project_id.in_(ids)).all()
    match = matcher_for(db, projects, contributors, pushes)
    with_history = {c.project_id for c in contributors} | {p.project_id for p in pushes}

    def bump(uid: UUID, project_id: UUID, commits: int = 0, pushes_: int = 0, at: datetime | None = None) -> None:
        entry = totals[uid]
        entry["commits"] += commits
        entry["pushes"] += pushes_
        per = entry["projects"].setdefault(project_id, {"commits": 0, "pushes": 0})
        per["commits"] += commits
        per["pushes"] += pushes_
        if at and (entry["last_push"] is None or at > entry["last_push"]):
            entry["last_push"] = at

    for c in contributors:
        uid = match.user(c.project_id, c.github_id, c.email)
        if uid:
            bump(uid, c.project_id, commits=c.commits or 0)
            for week, count in c.weekly or []:
                totals[uid]["weekly"][int(week)] += int(count)
    for p in pushes:
        uid = match.user(p.project_id, p.actor_github_id)
        if uid:
            bump(uid, p.project_id, pushes_=1, at=p.pushed_at)
    for project in projects:
        if project.id not in with_history and (project.github_commit_count or project.github_push_count):
            bump(project.owner_id, project.id, project.github_commit_count or 0, project.github_push_count or 0,
                 project.github_pushed_at)
    return dict(totals)
