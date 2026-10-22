"""Job description to developer matching: deterministic, explainable, and nothing is saved.

extract() reads a pasted description and suggests requirements from the technology catalog. A term
counts as required only when its heading or its own line says so; everything else is preferred, and
words that are also everyday English (Go, Swift, Spring) are offered for review instead of added.
The recruiter edits the lists before match() ranks developers.

Scoring, shown to the recruiter as written here: required technologies count twice, preferred once.
Evidence found in the source of a project the recruiter can see earns full credit; a technology the
developer added to a project without it being detected earns half; a profile skill alone earns none.
Likes, followers and commit volume never change the percentage.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models.profile import UserProfile
from app.models.project import Project
from app.models.techstack import TechStack
from app.models.user import User
from app.services.github_history import credit
from app.services.skill_matrix import detected_names, hidden_only_names, profile_skill_names, project_skills
from app.services.social import profile_open_to, public_project_filter
from app.services.tech_catalog import (
    AMBIGUOUS,
    AMBIGUOUS_TARGET,
    CASE_SENSITIVE,
    JD_ALIASES,
    KNOWN_NAMES,
    category_of,
    term_pattern,
)

MAX_TEXT = 20_000
MAX_TERMS = 25
EXCERPT = 140
FORMULA = (
    "Required technologies count twice and preferred ones once. Evidence detected in the source of a project you can "
    "see earns full credit; a technology the developer added to a project without it being detected earns half; a "
    "profile skill alone earns none. Likes, followers and commit volume are not used."
)
WEIGHTS = {"evidenced": 1.0, "declared_project": 0.5, "declared_profile": 0.0, "missing": 0.0}

REQUIRED_HEADING = re.compile(
    r"requir|must|qualif|essential|minimum|what you.?ll need|you have|you bring|looking for|skills|experience", re.I)
PREFERRED_HEADING = re.compile(r"nice[ -]to[ -]have|bonus|prefer|plus|desir|good to have|ideal|asset|extra", re.I)
NEUTRAL_HEADING = re.compile(r"responsib|what you.?ll do|about|benefit|perk|offer|day[ -]to[ -]day|culture|team|role", re.I)
REQUIRED_LINE = re.compile(
    r"\b(?:(?:required|requires?|must|mandatory|essential|need to have)\b|strong (?:experience|knowledge)|proficien)", re.I)
PREFERRED_LINE = re.compile(
    r"\b(?:nice[ -]to[ -]have|bonus|preferred|preferably|a plus|an asset|desirable|familiarity|exposure to|ideally|optional)\b", re.I)
BULLET = re.compile(r"^\s*(?:[-*\u2022\u00b7>]+|\d+[.)])\s*")
SENTENCE_BREAK = re.compile(r"(?<=[.;!?])\s+(?=[A-Z])")


@dataclass(frozen=True)
class Term:
    text: str
    name: str
    ambiguous: bool
    pattern: re.Pattern[str]


def _terms(catalog_names: list[str]) -> list[Term]:
    """Every phrase that names a technology, longest first so 'React Native' wins over 'React'."""
    seen: dict[str, Term] = {}

    def add(text: str, name: str, case_sensitive: bool, ambiguous: bool = False) -> None:
        key = text if case_sensitive else text.lower()
        if key not in seen:
            seen[key] = Term(text, name, ambiguous, term_pattern(text, case_sensitive))

    for alias, name in JD_ALIASES.items():
        add(alias, name, False)
    for word, target in AMBIGUOUS_TARGET.items():
        add(word, target, True, True)
    for name in KNOWN_NAMES.values():
        add(name, name, name in AMBIGUOUS or name in CASE_SENSITIVE, name in AMBIGUOUS)
    for name in catalog_names:
        if name.lower() not in KNOWN_NAMES and len(name) > 1:
            add(name, name, True, name in AMBIGUOUS)
    return sorted(seen.values(), key=lambda t: -len(t.text))


def _is_heading(line: str, names_tech: bool) -> bool:
    """Markdown headings, bold lines and lines ending in ':' always; a short capitalised line only when it names no technology."""
    text = line.strip()
    if text.startswith("#") or (text.endswith(":") and len(text) <= 80) or re.fullmatch(r"\*\*[^*]{2,60}\*\*:?", text):
        return True
    return (not names_tech and len(text.split()) <= 6 and not text.endswith((".", ",", ";")) and not BULLET.match(line)
            and text[:1].isupper())


def _heading_mode(label: str) -> str | None:
    if PREFERRED_HEADING.search(label):
        return "preferred"
    if REQUIRED_HEADING.search(label):
        return "required"
    if NEUTRAL_HEADING.search(label):
        return "mentioned"
    return None


def _clauses(text: str):
    """(raw line, sentence, whether the sentence is the whole line). A cue such as 'Nice to have' only covers its own sentence."""
    for raw in text.splitlines():
        if not raw.strip():
            continue
        parts = [p for p in SENTENCE_BREAK.split(BULLET.sub("", raw).strip()) if p.strip()]
        for part in parts:
            yield raw, part.strip(), len(parts) == 1


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT else text[: EXCERPT - 1].rstrip() + "\u2026"


def extract(text: str, catalog_names: list[str]) -> dict:
    """Suggested required, preferred and ambiguous terms, each with the line and the reason it was placed there."""
    terms = _terms(catalog_names)
    mode, heading = "mentioned", ""
    found: dict[str, dict] = {}
    ambiguous: dict[str, dict] = {}
    rank = {"mentioned": 0, "preferred": 1, "required": 2}

    for raw, line, whole in _clauses(text[:MAX_TEXT]):
        hits: list[tuple[Term, str]] = []
        masked = line
        for term in terms:
            match = term.pattern.search(masked)
            if match:
                hits.append((term, match.group(0)))
                masked = term.pattern.sub(lambda m: " " * len(m.group(0)), masked)

        if whole and _is_heading(raw, any(not t.ambiguous for t, _ in hits)):
            label = line.strip("#*: ").strip()
            new_mode = _heading_mode(label)
            if new_mode:
                mode, heading = new_mode, label
        prefix = line.split(":", 1)[0] if ":" in line[:40] and not line.rstrip().endswith(":") else ""
        prefix_mode = _heading_mode(prefix) if prefix else None
        if PREFERRED_LINE.search(line):
            line_mode, reason = "preferred", "The line marks it as nice to have"
        elif REQUIRED_LINE.search(line):
            line_mode, reason = "required", "The line marks it as required"
        elif prefix_mode in ("required", "preferred"):
            line_mode, reason = prefix_mode, f"Listed after \u201c{prefix.strip('*# ')}\u201d"
        elif mode != "mentioned":
            line_mode, reason = mode, f"Listed under \u201c{heading}\u201d"
        else:
            line_mode, reason = "mentioned", "Mentioned in the description; not marked as required"

        for term, written in hits:
            if term.ambiguous:
                if term.name not in ambiguous:
                    ambiguous[term.name] = {"term": written, "name": term.name, "category": category_of(term.name),
                                            "excerpt": _excerpt(line)}
                continue
            current = found.get(term.name)
            if current is None or rank[line_mode] > rank[current["mode"]]:
                found[term.name] = {"name": term.name, "category": category_of(term.name), "mode": line_mode,
                                    "reason": reason, "excerpt": _excerpt(line)}

    required = [f for f in found.values() if f["mode"] == "required"][:MAX_TERMS]
    preferred = [f for f in found.values() if f["mode"] != "required"][:MAX_TERMS]
    for row in required + preferred:
        row.pop("mode")
    return {
        "required": required,
        "preferred": preferred,
        "ambiguous": [a for name, a in ambiguous.items() if name not in found][:MAX_TERMS],
    }


@dataclass
class MatchFilters:
    open_to_work: bool = False
    work_type: str = ""
    arrangement: str = ""
    location: str = ""
    min_relevant_projects: int = 0
    require_all_required: bool = False


def _list(value: str | None) -> list[str]:
    return [part for part in (value or "").split(",") if part]


def _clean(names: list[str]) -> list[str]:
    """Trimmed, de-duplicated names, with common spellings mapped to catalog names (postgres -> PostgreSQL)."""
    out: list[str] = []
    for name in names:
        name = " ".join(name.split())[:60]
        name = JD_ALIASES.get(name.lower()) or KNOWN_NAMES.get(name.lower()) or name
        if name and name.lower() not in {n.lower() for n in out}:
            out.append(name)
    return out[:MAX_TERMS]


def match(db: Session, viewer_id: UUID, required: list[str], preferred: list[str], filters: MatchFilters, limit: int = 50) -> dict:
    required = _clean(required)
    preferred = [p for p in _clean(preferred) if p.lower() not in {r.lower() for r in required}]
    wanted = [(name, "required") for name in required] + [(name, "preferred") for name in preferred]
    max_points = 2 * len(required) + len(preferred)

    public = (
        db.query(Project)
        .options(selectinload(Project.tech_stacks), selectinload(Project.collaborators))
        .filter(public_project_filter(viewer_id))
        .all()
    )
    by_person: dict[UUID, list[tuple[Project, str, str]]] = defaultdict(list)
    for project in public:
        by_person[project.owner_id].append((project, "owner", "Owner"))
        for member in project.collaborators:
            by_person[member.user_id].append((project, "member", member.role or "Collaborator"))
    imported = [p for p in public if p.github_url or p.github_commit_count or p.github_push_count]
    credited = credit(db, imported)
    detected_by_id = detected_names(db, public)
    skills_of = {p.id: project_skills(p, detected_by_id) for p in public}

    people = db.query(User).options(selectinload(User.profile)).outerjoin(User.profile).filter(
        or_(UserProfile.discoverable.is_(None), UserProfile.discoverable.is_(True)),
        User.account_type != "recruiter",
        User.id != viewer_id,
    ).all()

    hidden = hidden_only_names(db, [p.id for p in people], public)
    results, restricted = [], 0
    for person in people:
        if not profile_open_to(db, person.id, viewer_id):
            restricted += 1
            continue
        profile = person.profile
        open_to_work = bool(profile and profile.open_to_work)
        work_types = _list(profile.work_types) if open_to_work else []
        arrangements = _list(profile.work_arrangements) if open_to_work else []
        if filters.open_to_work and not open_to_work:
            continue
        if filters.work_type and filters.work_type not in work_types:
            continue
        if filters.arrangement and filters.arrangement not in arrangements:
            continue
        if filters.location and filters.location.lower() not in ((profile.location if profile else "") or "").lower():
            continue

        projects = by_person.get(person.id, [])
        commits_in = {pid: per["commits"] for pid, per in credited.get(person.id, {}).get("projects", {}).items()}
        profile_skills = set(profile_skill_names(profile.skills if profile else None)) - hidden.get(person.id, set())

        rows, points, relevant = [], 0.0, set()
        for name, importance in wanted:
            key = name.lower()
            support = []
            for project, relation, role in projects:
                tech = skills_of[project.id].get(key)
                if tech is None:
                    continue
                relevant.add(project.id)
                support.append({
                    "project_id": project.id, "title": project.title, "relation": relation, "role": role,
                    "detected": tech["detected"], "commits": commits_in.get(project.id, 0),
                })
            support.sort(key=lambda s: (not s["detected"], -s["commits"], s["title"].lower()))
            if any(s["detected"] for s in support):
                status = "evidenced"
            elif support:
                status = "declared_project"
            elif key in profile_skills:
                status = "declared_profile"
            else:
                status = "missing"
            weight = WEIGHTS[status]
            points += weight * (2 if importance == "required" else 1)
            rows.append({
                "name": name, "importance": importance, "status": status, "weight": weight,
                "contribution_linked": any(s["detected"] and s["commits"] for s in support),
                "projects": support[:3], "project_count": len(support),
            })

        required_met = sum(1 for r in rows if r["importance"] == "required" and r["status"] == "evidenced")
        if points <= 0:
            continue
        if filters.require_all_required and any(
                r["importance"] == "required" and r["status"] in ("missing", "declared_profile") for r in rows):
            continue
        if len(relevant) < filters.min_relevant_projects:
            continue
        results.append({
            "id": person.id,
            "username": person.username,
            "full_name": profile.full_name if profile else None,
            "headline": profile.headline if profile else None,
            "avatar_url": profile.avatar_url if profile else None,
            "location": profile.location if profile else None,
            "open_to_work": open_to_work,
            "work_types": work_types,
            "work_arrangements": arrangements,
            "percent": math.floor(points / max_points * 100 + 0.5) if max_points else 0,
            "points": round(points, 1),
            "max_points": max_points,
            "required_met": required_met,
            "required_total": len(required),
            "preferred_met": sum(1 for r in rows if r["importance"] == "preferred" and r["status"] == "evidenced"),
            "preferred_total": len(preferred),
            "relevant_projects": len(relevant),
            "contribution_linked": sum(1 for r in rows if r["contribution_linked"]),
            "requirements": rows,
        })

    results.sort(key=lambda r: (-r["points"], -r["required_met"], -r["relevant_projects"],
                                -r["contribution_linked"], r["username"].lower()))
    return {
        "required": required,
        "preferred": preferred,
        "formula": FORMULA,
        "considered": len(people) - restricted,
        "restricted": restricted,
        "results": results[:limit],
    }


def catalog_names(db: Session) -> list[str]:
    return [name for (name,) in db.query(TechStack.name).all()]
