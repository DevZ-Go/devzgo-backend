"""Project quality and impact report: rule-based checks over recorded source facts and live project data.

Nothing here runs, installs or builds code; whether tests pass is always "not assessed". Each check
carries one status and the evidence behind it:
  found         evidence found
  not_detected  looked for and not found (not proof it is absent)
  not_assessed  could not be checked from what DevZ-Go has
  needs_review  something the project team should look at
The project team (owner and members who manage files) sees file paths, security findings and
recommendations. Everyone else who may open the project sees the other sections only. Secret values
are never recorded (see services.source_facts), so they can never be shown.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.file import File as FileRecord
from app.models.github_history import ProjectContributor
from app.models.project import Project
from app.models.project_comment import ProjectComment
from app.models.project_like import ProjectLike
from app.services.social import project_access

STATUSES = ("found", "not_detected", "not_assessed", "needs_review")
SECTIONS = [
    ("documentation", "Documentation"),
    ("organization", "Code organization"),
    ("testing", "Testing"),
    ("practices", "Development practices"),
    ("security", "Security indicators"),
    ("impact", "Impact"),
]
RECOMMENDATIONS: dict[str, str] = {
    "readme": "Add a README that explains what the project does.",
    "overview": "Open the README with a short overview: the problem, who it is for and what it does.",
    "setup": "Add setup steps to the README: prerequisites, install commands and how to run it locally.",
    "usage": "Describe how to use the project, with an example or the main features.",
    "tech_stack": "List the main technologies in the README or on the project page.",
    "env_example": "Add a .env.example that lists the settings the project needs, without real values.",
    "structure": "Group source files into folders (for example src/, components/, services/, tests/).",
    "manifest": "Declare dependencies in a manifest such as package.json or requirements.txt.",
    "generated": "Leave dependency and cache folders (node_modules, virtual environments) out of the source.",
    "test_files": "Add automated tests for the most important behaviour.",
    "test_framework": "Set up a test framework (for example Jest, Vitest, pytest or JUnit) so tests are easy to run.",
    "ci": "Add a continuous integration workflow (for example GitHub Actions) that builds and tests each change.",
    "lint": "Add a linter or formatter configuration so the code style stays consistent.",
    "lockfile": "Commit the dependency lockfile so installs are reproducible.",
    "secret_files": "Remove committed environment or key files, rotate any credentials they held, and ignore them in .gitignore.",
    "possible_secrets": "Check the flagged lines; move any real credentials into environment variables and rotate them.",
    "gitignore": "Add a .gitignore that excludes environment files, dependencies and build output.",
    "demo": "Add a live demo or deployment link so reviewers can try the project.",
    "outcomes": "Describe the outcomes you can report (users, results, feedback), stated as your own account.",
}


def _check(key: str, label: str, status: str, detail: str, evidence: list[dict] | None = None) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail, "evidence": evidence or []}


def _files(paths: list[str], limit: int = 5) -> list[dict]:
    return [{"file": path} for path in paths[:limit]]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _documentation(project: Project, facts: dict | None) -> list[dict]:
    if facts is None:
        reason = "Source has not been analysed yet."
        checks = [_check(k, label, "not_assessed", reason) for k, label in (
            ("readme", "README"), ("overview", "Project overview"), ("setup", "Setup instructions"),
            ("usage", "Usage instructions"), ("env_example", "Environment example file"))]
        tech = [t.name for t in project.tech_stacks]
        checks.insert(4, _check("tech_stack", "Technology stack documented", "found" if tech else "not_detected",
                                f"Listed on DevZ-Go: {', '.join(tech[:6])}." if tech else "No technologies listed on DevZ-Go."))
        return checks

    readme = facts.get("readme")
    sections = (readme or {}).get("sections", {})
    readme_file = _files([readme["path"]]) if readme else []
    checks = [
        _check("readme", "README", "found" if readme else "not_detected",
               f"{readme['path']} ({_plural(readme['words'], 'word')})." if readme else "No README file in the source.",
               readme_file),
    ]
    description = (project.full_description or "").strip()
    if sections.get("overview"):
        checks.append(_check("overview", "Project overview", "found", "The README describes the project.", readme_file))
    elif len(description) >= 120:
        checks.append(_check("overview", "Project overview", "found", "The project page has a written description."))
    else:
        checks.append(_check("overview", "Project overview", "not_detected", "No overview found in the README or the project description."))
    for key, label, section, found, missing in (
        ("setup", "Setup instructions", "setup", "The README explains how to install or run it.", "No setup or install steps found in the README."),
        ("usage", "Usage instructions", "usage", "The README explains how to use it or lists its features.", "No usage section found in the README."),
    ):
        checks.append(_check(key, label, "found" if sections.get(section) else "not_detected",
                             found if sections.get(section) else missing, readme_file if sections.get(section) else []))
    tech = [t.name for t in project.tech_stacks]
    if sections.get("tech"):
        checks.append(_check("tech_stack", "Technology stack documented", "found", "The README lists the technologies used.", readme_file))
    elif tech:
        checks.append(_check("tech_stack", "Technology stack documented", "found", f"Listed on DevZ-Go: {', '.join(tech[:6])}."))
    else:
        checks.append(_check("tech_stack", "Technology stack documented", "not_detected", "No technology list in the README or on DevZ-Go."))
    examples = facts.get("env_examples") or []
    checks.append(_check("env_example", "Environment example file", "found" if examples else "not_detected",
                         "An example settings file is included." if examples else "No .env.example or similar file found.",
                         _files(examples)))
    return checks


def _organization(facts: dict | None) -> list[dict]:
    if facts is None:
        reason = "Source has not been analysed yet."
        return [_check(k, label, "not_assessed", reason) for k, label in (
            ("structure", "Source organised into folders"), ("manifest", "Dependencies declared"),
            ("generated", "Generated files left out"))]
    structure = facts.get("structure") or {}
    known = structure.get("known_dirs") or []
    nested, root = structure.get("nested_source_files", 0), structure.get("root_source_files", 0)
    if not facts.get("source_files"):
        organised = _check("structure", "Source organised into folders", "not_assessed", "No source code files were found.")
    elif known or nested > root:
        where = f"Folders such as {', '.join(known[:5])}." if known else f"{_plural(nested, 'source file')} sit in folders."
        organised = _check("structure", "Source organised into folders", "found", where)
    else:
        organised = _check("structure", "Source organised into folders", "not_detected",
                           f"{_plural(root, 'source file')} sit at the top level with no folder structure.")
    manifests = facts.get("manifests") or []
    generated = facts.get("generated")
    if generated is None:
        generated_check = _check("generated", "Generated files left out", "not_assessed",
                                 "The original archive is not available to check.")
    elif generated.get("entries"):
        generated_check = _check("generated", "Generated files left out", "needs_review",
                                 f"The source included {', '.join(generated['folders'][:4])} "
                                 f"({_plural(generated['entries'], 'file')}); DevZ-Go left them out.")
    else:
        generated_check = _check("generated", "Generated files left out", "found",
                                 "No dependency or cache folders were included.")
    return [
        organised,
        _check("manifest", "Dependencies declared", "found" if manifests else "not_detected",
               f"{_plural(len(manifests), 'dependency manifest')} found." if manifests else "No dependency manifest found.",
               _files(manifests)),
        generated_check,
    ]


def _testing(facts: dict | None) -> list[dict]:
    not_run = _check("test_results", "Tests pass", "not_assessed",
                     "DevZ-Go never runs code, so whether tests pass is not assessed.")
    if facts is None:
        reason = "Source has not been analysed yet."
        return [_check("test_files", "Test files", "not_assessed", reason),
                _check("test_framework", "Test framework", "not_assessed", reason), not_run]
    tests = facts.get("tests") or {}
    count = tests.get("count", 0)
    frameworks, configs = tests.get("frameworks") or [], tests.get("configs") or []
    return [
        _check("test_files", "Test files", "found" if count else "not_detected",
               f"{_plural(count, 'test file')} found." if count else "No test files found.", _files(tests.get("sample") or [])),
        _check("test_framework", "Test framework", "found" if frameworks or configs else "not_detected",
               f"Uses {', '.join(frameworks)}." if frameworks else
               ("A test configuration file is present." if configs else "No test framework or test configuration found."),
               _files(configs)),
        not_run,
    ]


def _practices(project: Project, facts: dict | None) -> list[dict]:
    checks: list[dict] = []
    if facts is None:
        reason = "Source has not been analysed yet."
        checks = [_check(k, label, "not_assessed", reason) for k, label in (
            ("ci", "Continuous integration"), ("docker", "Container setup"), ("lint", "Linting or formatting"),
            ("lockfile", "Dependency lockfile"), ("deploy", "Deployment configuration"))]
    else:
        manifests = facts.get("manifests") or []
        lockfiles = facts.get("lockfiles") or []
        for key, label, paths, found, missing in (
            ("ci", "Continuous integration", facts.get("ci") or [], "CI configuration found.", "No CI workflow found."),
            ("docker", "Container setup", facts.get("docker") or [], "Docker configuration found.", "No Dockerfile or compose file found."),
            ("lint", "Linting or formatting", facts.get("lint") or [], "Linter or formatter configuration found.", "No linter or formatter configuration found."),
            ("deploy", "Deployment configuration", facts.get("deploy") or [], "Deployment configuration found.", "No deployment configuration found."),
        ):
            checks.append(_check(key, label, "found" if paths else "not_detected", found if paths else missing, _files(paths)))
        if lockfiles:
            lock = _check("lockfile", "Dependency lockfile", "found", "Dependency versions are locked.", _files(lockfiles))
        elif manifests:
            lock = _check("lockfile", "Dependency lockfile", "not_detected", "Dependencies are declared but no lockfile was found.")
        else:
            lock = _check("lockfile", "Dependency lockfile", "not_assessed", "No dependency manifest to lock.")
        checks.insert(3, lock)
    if project.github_url and project.github_commit_count:
        checks.append(_check("history", "Version history", "found",
                             f"{_plural(project.github_commit_count, 'commit')} in the GitHub repository."))
    elif project.github_url:
        checks.append(_check("history", "Version history", "not_assessed", "Linked to GitHub; commit history has not been imported."))
    else:
        checks.append(_check("history", "Version history", "not_assessed", "Not imported from GitHub, so history is unknown."))
    return checks


def _security(facts: dict | None) -> list[dict]:
    if facts is None:
        reason = "Source has not been analysed yet."
        return [_check(k, label, "not_assessed", reason) for k, label in (
            ("secret_files", "Environment and key files"), ("possible_secrets", "Hard-coded credentials"),
            ("gitignore", "Ignore rules"))]
    security = facts.get("security") or {}
    secret_files = security.get("secret_files") or []
    hits = security.get("possible_secrets") or []
    ignore = facts.get("gitignore") or {}
    ignore_file = _files([ignore.get("path") or ".gitignore"])
    if not ignore.get("present"):
        gitignore = _check("gitignore", "Ignore rules", "not_detected", "No .gitignore at the project root.")
    elif secret_files and not ignore.get("covers_env"):
        gitignore = _check("gitignore", "Ignore rules", "needs_review", ".gitignore does not exclude .env files.", ignore_file)
    else:
        gitignore = _check("gitignore", "Ignore rules", "found", "A .gitignore is present.", ignore_file)
    return [
        _check("secret_files", "Environment and key files", "needs_review" if secret_files else "found",
               f"{_plural(len(secret_files), 'environment or key file')} committed with the source." if secret_files
               else "No environment or key files are in the source.", _files(secret_files, 10)),
        _check("possible_secrets", "Hard-coded credentials", "needs_review" if hits else "found",
               f"{_plural(len(hits), 'line')} may contain a credential. Values are not stored or shown." if hits
               else f"No credential patterns found in {_plural(security.get('scanned_files', 0), 'file')}.",
               [{"file": h["file"], "line": h["line"], "label": h["kind"]} for h in hits[:10]]),
        gitignore,
    ]


def _impact(db: Session, project: Project) -> list[dict]:
    likes = db.query(func.count(ProjectLike.id)).filter(ProjectLike.project_id == project.id).scalar() or 0
    comments = db.query(func.count(ProjectComment.id)).filter(ProjectComment.project_id == project.id).scalar() or 0
    contributors = db.query(func.count(ProjectContributor.id)).filter(ProjectContributor.project_id == project.id).scalar() or 0
    members = len(project.collaborators)
    demo = []
    if project.live_url:
        demo.append({"url": project.live_url, "label": "Live demo or deployment"})
    readme_link = ((project.source_facts or {}).get("readme") or {}).get("deploy_link")
    if readme_link and readme_link != project.live_url:
        demo.append({"url": readme_link, "label": "Linked from the README"})
    if project.demo_video_url:
        demo.append({"label": "Demo video on the project page"})

    team = []
    if contributors:
        team.append(_plural(contributors, "GitHub contributor"))
    if members:
        team.append(f"{_plural(members + 1, 'team member')} on DevZ-Go")
    activity = []
    if project.github_commit_count:
        activity.append(_plural(project.github_commit_count, "commit"))
    if project.github_pushed_at:
        activity.append(f"last push {project.github_pushed_at.day} {project.github_pushed_at:%b %Y}")
    if activity and not project.github_commit_count:
        activity[0] = activity[0][0].upper() + activity[0][1:]
    note = (project.impact_note or "").strip()
    return [
        _check("demo", "Demo or deployment", "found" if demo else "not_detected",
               "A way to see the project running is available." if demo else "No live demo, deployment link or demo video.", demo),
        _check("engagement", "Community engagement", "found" if likes or comments else "not_detected",
               f"{_plural(likes, 'like')} and {_plural(comments, 'comment')} on DevZ-Go." if likes or comments
               else "No likes or comments on DevZ-Go yet."),
        _check("team", "Contributors", "found" if team else "not_detected",
               " · ".join(team) + "." if team else "No other contributors recorded."),
        _check("activity", "Repository activity", "found" if activity else "not_assessed",
               ", ".join(activity) + " on GitHub." if activity else "Not imported from GitHub."),
        _check("outcomes", "Reported outcomes", "found" if note else "not_detected",
               note if note else "The developer has not reported outcomes.",
               [{"label": "Reported by the developer"}] if note else []),
    ]


def _summary(checks: list[dict]) -> dict:
    return {status: sum(1 for c in checks if c["status"] == status) for status in STATUSES}


def quality_report(db: Session, project: Project, viewer_id: UUID | None) -> dict:
    access = project_access(db, project, viewer_id)
    team = bool(access["owner"] or access["files"])
    facts = project.source_facts if project.source_facts else None
    has_source = bool(project.github_url) or db.query(FileRecord.id).filter(FileRecord.project_id == project.id).first() is not None

    built = {
        "documentation": _documentation(project, facts),
        "organization": _organization(facts),
        "testing": _testing(facts),
        "practices": _practices(project, facts),
        "security": _security(facts) if team else [],
        "impact": _impact(db, project),
    }
    sections = [
        {"key": key, "label": label, "restricted": key == "security" and not team,
         "checks": built[key], "summary": _summary(built[key])}
        for key, label in SECTIONS
    ]

    every = [c for s in sections for c in s["checks"]]
    recommendations = [
        {"check": c["key"], "status": c["status"], "text": RECOMMENDATIONS[c["key"]]}
        for c in every if team and c["status"] in ("not_detected", "needs_review") and c["key"] in RECOMMENDATIONS
    ]
    analyzed_at: datetime | None = project.source_analyzed_at if facts else None
    return {
        "project_id": project.id,
        "audience": "team" if team else "viewer",
        "analyzed_at": analyzed_at,
        "has_source": has_source,
        "source_recorded": facts is not None,
        "can_refresh": team and has_source,
        "totals": _summary(every),
        "sections": sections,
        "recommendations": recommendations,
    }
