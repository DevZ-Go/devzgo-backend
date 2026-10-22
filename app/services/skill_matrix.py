"""Evidence-based skill matrix: which technologies someone has shown, and in which projects.

There are no levels or scores. Each skill says how it is known:
  detected             found in a project's source: a dependency, an import, a config file, file types
                       or the language bar
  contribution_linked  present in a project where this person has commits credited from GitHub
  declared             named by the person (a profile skill, or a tech they added to a project) with
                       nothing detected to back it
Only projects this viewer may open are used, so private work never shapes what others see.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.techstack import TechStack
from app.models.user import User
from app.services.github_history import credit
from app.services.project_analysis import LANGUAGE_TECH
from app.services.social import profile_open_to
from app.services.talent import joined_for, projects_for
from app.services.tech_catalog import CATEGORIES, KNOWN_NAMES, category_of

PROJECTS_PER_SKILL = 20
FILES_PER_PROJECT = 4
CLASS_ORDER = ("contribution_linked", "detected", "declared")


def _value(enum_or_str) -> str:
    return enum_or_str.value if hasattr(enum_or_str, "value") else str(enum_or_str)


def _latest(*moments: datetime | None) -> datetime | None:
    aware = [m if m.tzinfo else m.replace(tzinfo=timezone.utc) for m in moments if m is not None]
    return max(aware, default=None)


def detected_names(db: Session, projects: list[Project]) -> dict[int, str]:
    ids = {int(i) for p in projects for i in (p.detected_tech_stack_ids or []) if isinstance(i, int)}
    if not ids:
        return {}
    return {int(t.id): t.name for t in db.query(TechStack).filter(TechStack.id.in_(ids)).all()}


def _language_shares(project: Project) -> dict[str, float]:
    shares: dict[str, float] = {}
    for row in project.language_stats or []:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        tech = LANGUAGE_TECH.get(row["name"], row["name"])
        if tech:
            shares[tech.lower()] = shares.get(tech.lower(), 0.0) + float(row.get("percentage") or 0)
    return shares


_LANGUAGE_TECH_LOWER = {name.lower(): tech for name, tech in LANGUAGE_TECH.items()}


def profile_skill_names(skills: str | None) -> dict[str, str]:
    """Profile skills keyed by lowercase name, using the names project analysis uses ("Dockerfile" is Docker)."""
    out: dict[str, str] = {}
    for raw in (skills or "").split(","):
        name = raw.strip()
        if not name:
            continue
        name = _LANGUAGE_TECH_LOWER.get(name.lower(), name)
        if name:
            out.setdefault(name.lower(), KNOWN_NAMES.get(name.lower(), name))
    return out


def _names(projects: list[Project]) -> set[str]:
    out: set[str] = set()
    for project in projects:
        out |= {t.name.lower() for t in project.tech_stacks}
        out |= set(_language_shares(project))
    return out


def hidden_only_names(db: Session, owner_ids: list[UUID], visible: list[Project]) -> dict[UUID, set[str]]:
    """Per owner, tech names that only their projects hidden from this viewer contain.

    Profile skills are filled in from every project the person owns, private ones included, so these
    names are left out of what the viewer sees.
    """
    if not owner_ids:
        return {}
    visible_ids = [p.id for p in visible]
    hidden = db.query(Project).filter(Project.owner_id.in_(owner_ids), Project.id.notin_(visible_ids)).all()
    by_owner_hidden: dict[UUID, list[Project]] = {}
    for project in hidden:
        by_owner_hidden.setdefault(project.owner_id, []).append(project)
    by_owner_visible: dict[UUID, list[Project]] = {}
    for project in visible:
        by_owner_visible.setdefault(project.owner_id, []).append(project)
    return {owner: _names(rows) - _names(by_owner_visible.get(owner, [])) for owner, rows in by_owner_hidden.items()}


def project_skills(project: Project, detected_by_id: dict[int, str]) -> dict[str, dict]:
    """Every technology one project shows, keyed by lowercase name: display name, detected, files, language share."""
    facts = project.source_facts or {}
    evidence = {name.lower(): (name, rows) for name, rows in (facts.get("tech_evidence") or {}).items()}
    detected = {detected_by_id[i].lower(): detected_by_id[i] for i in (project.detected_tech_stack_ids or [])
                if isinstance(i, int) and i in detected_by_id}
    confirmed = {t.name.lower(): t.name for t in project.tech_stacks}
    shares = _language_shares(project)

    found: dict[str, dict] = {}
    for key in set(evidence) | set(detected) | set(confirmed) | set(shares):
        name = confirmed.get(key) or KNOWN_NAMES.get(key) or detected.get(key) or (evidence.get(key) or (key,))[0]
        rows = evidence.get(key, (None, []))[1]
        is_detected = bool(rows) or key in detected or key in shares
        found[key] = {
            "name": name,
            "detected": is_detected,
            "confirmed": key in confirmed,
            "files": [r for r in rows if r.get("kind") != "extension"][:FILES_PER_PROJECT]
                     or rows[:1],
            "kinds": sorted({r["kind"] for r in rows if r.get("kind")} | ({"language"} if key in shares else set())),
            "language_share": round(shares[key], 1) if key in shares else None,
            "unrecorded": is_detected and not rows and key not in shares,
        }
    return found


def skill_matrix(db: Session, subject: User, viewer_id: UUID | None) -> dict:
    open_profile = profile_open_to(db, subject.id, viewer_id)
    owned = [(p, "owner", "Owner") for p in projects_for(db, subject.id, viewer_id)]
    joined = [(p, "member", m.role or "Collaborator") for p, m in joined_for(db, subject.id, viewer_id)]
    considered = owned + joined
    projects = [p for p, _, _ in considered]

    imported = [p for p in projects if p.github_url or p.github_commit_count or p.github_push_count]
    commits_in = {pid: per["commits"] for pid, per in credit(db, imported).get(subject.id, {}).get("projects", {}).items()}
    detected_by_id = detected_names(db, projects)

    skills: dict[str, dict] = {}
    for project, relation, role in considered:
        last_activity = _latest(project.github_pushed_at, project.source_analyzed_at, project.updated_at)
        commits = commits_in.get(project.id, 0)
        for key, tech in project_skills(project, detected_by_id).items():
            skill = skills.setdefault(key, {"name": tech["name"], "rows": [], "profile": False})
            if tech["confirmed"]:
                skill["name"] = tech["name"]
            skill["rows"].append({
                "project_id": project.id,
                "title": project.title,
                "relation": relation,
                "role": role,
                "visibility": _value(project.visibility).lower(),
                "detected": tech["detected"],
                "kinds": tech["kinds"],
                "files": tech["files"],
                "language_share": tech["language_share"],
                "unrecorded": tech["unrecorded"],
                "commits": commits,
                "last_activity": last_activity,
            })

    profile = subject.profile
    if open_profile and profile and profile.skills:
        hidden = hidden_only_names(db, [subject.id], projects).get(subject.id, set())
        for key, name in profile_skill_names(profile.skills).items():
            if key not in hidden:
                skills.setdefault(key, {"name": name, "rows": [], "profile": False})
                skills[key]["profile"] = True

    groups: dict[str, list[dict]] = {key: [] for key, _ in CATEGORIES}
    totals = dict.fromkeys(CLASS_ORDER, 0)
    for skill in skills.values():
        rows = sorted(skill["rows"], key=lambda r: (not r["detected"], -r["commits"], r["title"].lower()))
        detected = [r for r in rows if r["detected"]]
        classes = []
        if any(r["commits"] for r in detected):
            classes.append("contribution_linked")
        if detected:
            classes.append("detected")
        else:
            classes.append("declared")
        for cls in classes:
            totals[cls] += 1
        sources = sorted({k for r in detected for k in r["kinds"]} | ({"recorded"} if any(r["unrecorded"] for r in detected) else set())
                         | ({"project"} if any(not r["detected"] for r in rows) else set())
                         | ({"profile"} if skill["profile"] else set()))
        category = category_of(skill["name"])
        groups[category].append({
            "name": skill["name"],
            "category": category,
            "classes": classes,
            "sources": sources,
            "project_count": len(rows),
            "owned_projects": sum(1 for r in rows if r["relation"] == "owner"),
            "team_projects": sum(1 for r in rows if r["relation"] == "member"),
            "commits": sum(r["commits"] for r in rows),
            "last_activity": _latest(*(r["last_activity"] for r in rows)),
            "projects": rows[:PROJECTS_PER_SKILL],
        })

    def strength(skill: dict) -> tuple:
        return (CLASS_ORDER.index(skill["classes"][0]), -skill["project_count"], skill["name"].lower())

    categories = [
        {"key": key, "label": label, "skills": sorted(groups[key], key=strength)}
        for key, label in CATEGORIES if groups[key]
    ]
    with_source = [p for p in projects if p.source_facts]
    return {
        "user_id": subject.id,
        "restricted": not open_profile,
        "projects_considered": len(projects),
        "projects_with_file_evidence": len(with_source),
        "totals": {"skills": len(skills), **totals},
        "categories": categories,
    }
