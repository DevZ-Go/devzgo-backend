"""Read-only view of every application table. Secrets are replaced before they leave the server."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Enum as SAEnum, String, Text, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import Table
from sqlalchemy.sql import delete as sa_delete

import app.db.base  # noqa: F401  registers every model on Base.metadata
from app.db.session import Base

# group id, label, one-line description
GROUPS: list[tuple[str, str, str]] = [
    ("accounts", "Account records", "Sign-in accounts and the profile fields behind them"),
    ("projects", "Projects", "Projects, files, technologies, members, likes and comments"),
    ("network", "Network", "Posts, likes and comments on the network"),
    ("messages", "Messages", "Direct messages, group chats and reactions"),
    ("connections", "Connections", "Connection requests and accepted pairs"),
    ("collaboration", "Collaboration", "Requests to join someone else's project"),
    ("recruiting", "Recruiting", "Shortlists and messages from recruiters"),
    ("challenges", "Challenges", "Technical challenges, invitations, submissions and reviews"),
    ("wraps", "Wraps", "Stored monthly wraps"),
    ("safety", "Safety", "Blocks, restricts and mutes"),
    ("workspace", "Workspace and GitHub", "GitHub links, drafts, sync jobs and commit history"),
    ("notifications", "Notifications", "In-app notifications"),
    ("usage", "Usage", "Which pages were opened, and which gallery direction was on screen"),
    ("security", "Security records", "Password resets, admins, approvals and the audit log"),
]

# table name, label, group id, what an admin is looking at
TABLES: list[tuple[str, str, str, str]] = [
    ("users", "Accounts", "accounts", "Sign-in accounts. Password hashes are hidden."),
    ("profiles", "Profiles", "accounts", "Names, bios, privacy and notification settings."),
    ("projects", "Projects", "projects", "Every project, public and private."),
    ("files", "Project files", "projects", "File names stored on a project. File bytes are not shown here."),
    ("project_tech", "Project technologies", "projects", "Which technologies are attached to which project."),
    ("techstacks", "Technologies", "projects", "The technology list projects can use."),
    ("project_collaborators", "Project members", "projects", "People on a project and the access they have."),
    ("project_invitations", "Project invitations", "projects", "Invitations that are still open or already answered."),
    ("project_likes", "Project likes", "projects", "Likes on projects."),
    ("project_comments", "Project comments", "projects", "Comments written on projects."),
    ("network_activities", "Posts", "network", "Network posts and the automatic activity items."),
    ("activity_likes", "Post likes", "network", "Likes on network posts."),
    ("activity_comments", "Post comments", "network", "Comments on network posts."),
    ("messages", "Direct messages", "messages", "One-to-one messages, including ones a person unsent."),
    ("message_reactions", "Message reactions", "messages", "Emoji reactions on direct messages."),
    ("message_hides", "Hidden messages", "messages", "Messages hidden for one person."),
    ("chat_rooms", "Group chats", "messages", "Group chats, including project chats."),
    ("chat_members", "Group members", "messages", "Who is in each group and who was made a group admin."),
    ("room_messages", "Group messages", "messages", "Messages sent inside group chats."),
    ("room_message_reactions", "Group reactions", "messages", "Emoji reactions on group messages."),
    ("room_message_hides", "Hidden group messages", "messages", "Group messages hidden for one person."),
    ("room_message_opens", "View-once opens", "messages", "Who opened a view-once group message."),
    ("connections", "Connections", "connections", "Pending, accepted and declined connections."),
    ("collaboration_requests", "Collaboration requests", "collaboration", "Requests to collaborate on a project."),
    ("shortlists", "Shortlists", "recruiting", "Private recruiter shortlists."),
    ("shortlist_entries", "Shortlist entries", "recruiting", "Candidates saved on a shortlist, including private notes."),
    ("recruitment_requests", "Recruitment requests", "recruiting", "Requests a recruiter sent to a developer."),
    ("technical_challenges", "Challenges", "challenges", "Challenges recruiters wrote."),
    ("challenge_invitations", "Challenge invitations", "challenges", "Who was invited to a challenge."),
    ("challenge_submissions", "Submissions", "challenges", "Work developers submitted."),
    ("challenge_reviews", "Reviews", "challenges", "Reviews recruiters wrote."),
    ("monthly_wraps", "Monthly wraps", "wraps", "A finished month, stored once."),
    ("user_blocks", "Blocks", "safety", "Blocks and restricts between two people."),
    ("chat_mutes", "Mutes", "safety", "Muted direct chats and muted groups."),
    ("github_connections", "GitHub connections", "workspace", "Which GitHub account is linked. Tokens are hidden."),
    ("workspace_drafts", "Editor drafts", "workspace", "Unsaved editor work. The draft text is hidden."),
    ("workspace_audit_events", "Workspace history", "workspace", "Push and sync attempts. File contents are not stored here."),
    ("workspace_sync_jobs", "Sync jobs", "workspace", "Work that copied a GitHub change into a project."),
    ("project_contributors", "Contributors", "workspace", "GitHub contributors captured for a project."),
    ("project_commits", "Commits", "workspace", "Commits captured for a project."),
    ("project_pushes", "Pushes", "workspace", "Push events captured for a project."),
    ("notifications", "Notifications", "notifications", "Notifications already sent inside DevZ-Go."),
    ("usage_events", "Visits", "usage", "Each time a signed-in person opened a page or switched the gallery direction. What they typed is not stored."),
    ("password_reset_tokens", "Password resets", "security", "Whether a reset link exists. The link itself is hidden."),
    ("platform_admins", "Admins", "security", "Accounts that can open this admin area."),
    ("platform_admin_invites", "Admin invites", "security", "Emails that receive admin access when they sign up."),
    ("admin_proposals", "Approval requests", "security", "Dangerous changes waiting on other admins."),
    ("admin_proposal_votes", "Approval votes", "security", "Who approved or stopped a request."),
    ("admin_audit_events", "Audit log", "security", "Changes made from the admin area."),
]

_BY_NAME = {item[0]: item for item in TABLES}


def _is_secret(name: str) -> bool:
    lowered = name.lower()
    if lowered in {"password_hash", "access_token_enc", "refresh_token_enc", "token_hash", "content_enc"}:
        return True
    if "password" in lowered or lowered.endswith("_enc"):
        return True
    return "token" in lowered and lowered != "token_version"


def _is_textual(column) -> bool:
    if isinstance(column.type, SAEnum) or _is_secret(column.name):
        return False
    return isinstance(column.type, (String, Text))


def table_spec(name: str) -> tuple[str, str, str, str]:
    spec = _BY_NAME.get(name)
    if spec is None or name not in Base.metadata.tables:
        raise HTTPException(status_code=404, detail="That part of the admin area does not exist")
    return spec


def sql_table(name: str) -> Table:
    table_spec(name)
    return Base.metadata.tables[name]


def groups_payload(db) -> list[dict]:
    counts = {}
    for name, table in ((item[0], Base.metadata.tables[item[0]]) for item in TABLES if item[0] in Base.metadata.tables):
        counts[name] = int(db.execute(select(func.count()).select_from(table)).scalar_one())
    grouped: dict[str, list[dict]] = {group[0]: [] for group in GROUPS}
    for name, label, group, blurb in TABLES:
        if name not in counts:
            continue
        grouped[group].append({"name": name, "label": label, "blurb": blurb, "count": counts[name]})
    return [
        {"id": group_id, "label": label, "blurb": blurb, "tables": grouped[group_id]}
        for group_id, label, blurb in GROUPS
        if grouped[group_id]
    ]


def _present(value: Any, *, detail: bool) -> Any:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return {"redacted": True, "present": True}
    if isinstance(value, (dict, list)):
        if detail:
            return value
        text = str(value)
        return text if len(text) <= 160 else text[:160] + "…"
    if isinstance(value, str):
        if detail:
            return value if len(value) <= 20000 else value[:20000] + "…"
        return value if len(value) <= 160 else value[:160] + "…"
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return getattr(value, "name", None) or str(value)
    return value


def _cell(column_name: str, value: Any, *, detail: bool) -> Any:
    if _is_secret(column_name):
        return {"redacted": True, "present": value is not None and value != ""}
    return _present(value, detail=detail)


def _coerce(column, raw: str) -> Any:
    try:
        python = column.type.python_type
    except Exception:
        python = str
    try:
        if python is uuid.UUID:
            return uuid.UUID(raw)
        if python is int:
            return int(raw)
        if python is bool:
            return raw.strip().lower() in {"1", "true", "t", "yes"}
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"{column.name} is not in the right form")
    return raw


def _pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _clauses(table: Table, q: str, eqs: list[str]):
    clauses = []
    for item in eqs:
        if ":" not in item:
            raise HTTPException(status_code=400, detail="Filters look like column:value")
        name, raw = item.split(":", 1)
        if name not in table.c or _is_secret(name):
            raise HTTPException(status_code=400, detail="That column cannot be filtered")
        clauses.append(table.c[name] == _coerce(table.c[name], raw))
    text = q.strip()
    if not text:
        return clauses
    textual = [column for column in table.c if _is_textual(column)]
    matches = [column.ilike(_pattern(text[:80]), escape="\\") for column in textual[:8]]
    if "id" in table.c:
        try:
            matches.append(table.c.id == uuid.UUID(text))
        except ValueError:
            pass
    if matches:
        clauses.append(or_(*matches))
    elif text:
        clauses.append(false())
    return clauses


def _pk_names(table: Table) -> list[str]:
    return [column.name for column in table.primary_key.columns]


def _order(table: Table):
    if "created_at" in table.c:
        return table.c.created_at.desc()
    return table.primary_key.columns[0].desc()


def _row_dict(table: Table, mapping, *, detail: bool) -> dict:
    body = {column.name: _cell(column.name, mapping[column.name], detail=detail) for column in table.c}
    body["_pk"] = {name: body[name] for name in _pk_names(table)}
    return body


def list_rows(db, name: str, *, q: str = "", eqs: list[str] | None = None, offset: int = 0, limit: int = 25) -> dict:
    spec = table_spec(name)
    table = sql_table(name)
    limit = min(max(limit, 1), 50)
    offset = max(offset, 0)
    clauses = _clauses(table, q or "", eqs or [])
    count = select(func.count()).select_from(table)
    rows = select(table)
    for clause in clauses:
        count = count.where(clause)
        rows = rows.where(clause)
    total = int(db.execute(count).scalar_one())
    result = db.execute(rows.order_by(_order(table)).offset(offset).limit(limit))
    return {
        "table": spec[0],
        "label": spec[1],
        "group": spec[2],
        "blurb": spec[3],
        "primary_key": _pk_names(table),
        "columns": [{"name": column.name, "secret": _is_secret(column.name)} for column in table.c],
        "rows": [_row_dict(table, mapping, detail=False) for mapping in result.mappings()],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def _pk_map(table: Table, items: list[str]) -> dict[str, str]:
    found = {}
    for item in items:
        if ":" not in item:
            raise HTTPException(status_code=400, detail="Record ids look like column:value")
        name, raw = item.split(":", 1)
        found[name] = raw
    expected = set(_pk_names(table))
    if set(found) != expected:
        raise HTTPException(status_code=400, detail="The record id is incomplete")
    return found


def get_row(db, name: str, pk_items: list[str]) -> dict:
    spec = table_spec(name)
    table = sql_table(name)
    pk = _pk_map(table, pk_items)
    stmt = select(table)
    for key, raw in pk.items():
        stmt = stmt.where(table.c[key] == _coerce(table.c[key], raw))
    mapping = db.execute(stmt).mappings().first()
    if mapping is None:
        raise HTTPException(status_code=404, detail="That record is no longer there")
    return {
        "table": spec[0],
        "label": spec[1],
        "blurb": spec[3],
        "primary_key": _pk_names(table),
        "columns": [{"name": column.name, "secret": _is_secret(column.name)} for column in table.c],
        "row": _row_dict(table, mapping, detail=True),
    }


def raw_row(db, name: str, pk: dict[str, Any]) -> dict | None:
    """Unredacted row for server-side checks. Callers must not return this to the browser."""
    table = sql_table(name)
    stmt = select(table)
    for key, raw in pk.items():
        if key not in table.c:
            raise HTTPException(status_code=400, detail="The record id is incomplete")
        stmt = stmt.where(table.c[key] == _coerce(table.c[key], str(raw)))
    mapping = db.execute(stmt).mappings().first()
    return dict(mapping) if mapping is not None else None


def delete_row(db, name: str, pk: dict[str, Any]) -> None:
    table = sql_table(name)
    if set(pk) != set(_pk_names(table)):
        raise HTTPException(status_code=400, detail="The record id is incomplete")
    stmt = sa_delete(table)
    for key, raw in pk.items():
        stmt = stmt.where(table.c[key] == _coerce(table.c[key], str(raw)))
    try:
        result = db.execute(stmt)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Other records still depend on this one, so it was left in place.")
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="That record is already gone")
