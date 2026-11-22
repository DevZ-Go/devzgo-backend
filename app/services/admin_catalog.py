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
from sqlalchemy.sql import update as sa_update

import app.db.base  # noqa: F401  registers every model on Base.metadata
from app.db.session import Base

# Optional per-table allow-lists. When a table is listed, only these columns may change.
# Tables not listed are still editable via safe defaults (see editable_columns).
RECORD_UPDATE: dict[str, frozenset[str]] = {
    "profiles": frozenset({
        "full_name", "headline", "bio", "location", "company", "role", "skills",
        "github_url", "linkedin_url", "website_url", "open_to_work", "profile_visibility",
        "message_policy", "discoverable", "allow_connection_requests",
    }),
    "network_clubs": frozenset({
        "name", "tagline", "about", "category", "institution", "join_policy", "policies",
        "purpose", "funds_visibility", "verification_status", "verification_note", "slug",
    }),
    "club_members": frozenset({"role", "custom_title", "hide_on_profile", "email_broadcast_opt_in"}),
    "club_announcements": frozenset({"title", "body", "pinned", "status", "audience", "badge"}),
    "club_events": frozenset({"title", "description", "location", "status", "modality", "meeting_url"}),
    "club_polls": frozenset({"question", "status", "allow_vote_change"}),
    "club_showcases": frozenset({"title", "tagline", "description", "kind", "sub_category", "github_url", "live_url"}),
    "projects": frozenset({"title", "short_description", "comments_enabled", "likes_hidden"}),
    "technical_challenges": frozenset({"title", "status"}),
    "notifications": frozenset({"message", "read"}),
    "platform_settings": frozenset({"value"}),
    "badge_definitions": frozenset({"name", "description", "status", "icon_key"}),
    "entitlements": frozenset({"expires_at", "source"}),
}

# Rows here cannot be edited or deleted through the generic record.* actions.
IMMUTABLE_TABLES = frozenset({
    "users",
    "platform_admins",
    "platform_admin_invites",
    "admin_proposals",
    "admin_proposal_votes",
    "admin_audit_events",
})

_LOCKED_COLUMN_NAMES = frozenset({
    "created_at", "updated_at", "joined_at", "password_hash", "token_version",
})

# group id, label, short blurb
GROUPS: list[tuple[str, str, str]] = [
    ("accounts", "Accounts", "Sign-in and profiles"),
    ("projects", "Projects", "Projects, files, likes, comments"),
    ("network", "Network", "Posts and reactions"),
    ("clubs", "Clubs", "Clubs, members, events, polls"),
    ("messages", "Messages", "DMs and group chats"),
    ("connections", "Connections", "Connection pairs"),
    ("collaboration", "Collaboration", "Join requests"),
    ("recruiting", "Recruiting", "Shortlists and outreach"),
    ("challenges", "Challenges", "Assessments and reviews"),
    ("career", "Career", "Experience, education, resumes"),
    ("talent", "Talent", "Badges, entitlements, evaluations"),
    ("intelligence", "Intelligence", "Project AI and settings"),
    ("email", "Email", "Outbox and verification"),
    ("engagement", "Engagement", "Saves and link previews"),
    ("wraps", "Wraps", "Monthly wraps"),
    ("safety", "Safety", "Blocks and mutes"),
    ("workspace", "Workspace", "GitHub and drafts"),
    ("notifications", "Notifications", "In-app notices"),
    ("usage", "Usage", "Page visits"),
    ("security", "Security", "Admins and audit"),
]

# table name, label, group id, short blurb
TABLES: list[tuple[str, str, str, str]] = [
    ("users", "Accounts", "accounts", "Sign-in accounts"),
    ("profiles", "Profiles", "accounts", "Profile fields"),
    ("projects", "Projects", "projects", "All projects"),
    ("files", "Project files", "projects", "File names"),
    ("project_tech", "Project technologies", "projects", "Tech on projects"),
    ("techstacks", "Technologies", "projects", "Tech catalog"),
    ("project_collaborators", "Project members", "projects", "Project access"),
    ("project_invitations", "Project invitations", "projects", "Open invites"),
    ("project_likes", "Project likes", "projects", "Likes"),
    ("project_comments", "Project comments", "projects", "Comments"),
    ("project_comment_likes", "Comment likes", "projects", "Likes on comments"),
    ("network_activities", "Posts", "network", "Network posts"),
    ("activity_likes", "Post likes", "network", "Likes on posts"),
    ("activity_comments", "Post comments", "network", "Comments on posts"),
    ("network_clubs", "Clubs", "clubs", "Student clubs"),
    ("club_members", "Club members", "clubs", "Membership and roles"),
    ("club_custom_roles", "Custom titles", "clubs", "Club-defined titles"),
    ("club_role_permissions", "Role permissions", "clubs", "Permission matrix"),
    ("club_events", "Club events", "clubs", "Events"),
    ("club_announcements", "Announcements", "clubs", "Club posts"),
    ("club_polls", "Polls", "clubs", "Polls"),
    ("club_poll_options", "Poll options", "clubs", "Choices"),
    ("club_poll_votes", "Poll votes", "clubs", "Votes"),
    ("club_fund_entries", "Funds", "clubs", "Ledger rows"),
    ("club_event_media", "Event media", "clubs", "Event files"),
    ("club_showcases", "Showcases", "clubs", "Club project showcases"),
    ("messages", "Direct messages", "messages", "One-to-one chat"),
    ("message_reactions", "Message reactions", "messages", "DM reactions"),
    ("message_hides", "Hidden messages", "messages", "Hidden DMs"),
    ("chat_rooms", "Group chats", "messages", "Rooms"),
    ("chat_members", "Group members", "messages", "Room members"),
    ("room_messages", "Group messages", "messages", "Room messages"),
    ("room_message_reactions", "Group reactions", "messages", "Room reactions"),
    ("room_message_hides", "Hidden group messages", "messages", "Hidden room messages"),
    ("room_message_opens", "View-once opens", "messages", "Opened view-once"),
    ("connections", "Connections", "connections", "Connection pairs"),
    ("collaboration_requests", "Collaboration requests", "collaboration", "Join requests"),
    ("shortlists", "Shortlists", "recruiting", "Recruiter lists"),
    ("shortlist_entries", "Shortlist entries", "recruiting", "Saved candidates"),
    ("recruitment_requests", "Recruitment requests", "recruiting", "Outreach"),
    ("technical_challenges", "Challenges", "challenges", "Assessments"),
    ("challenge_invitations", "Challenge invitations", "challenges", "Invites"),
    ("challenge_submissions", "Submissions", "challenges", "Work submitted"),
    ("challenge_reviews", "Reviews", "challenges", "Reviews"),
    ("career_experiences", "Experience", "career", "Work history"),
    ("career_skill_intervals", "Skill intervals", "career", "Skill timeline"),
    ("career_experience_projects", "Experience projects", "career", "Linked projects"),
    ("career_education", "Education", "career", "Schools"),
    ("career_certifications", "Certifications", "career", "Certs"),
    ("career_field_visibility", "Field visibility", "career", "What recruiters see"),
    ("resume_import_jobs", "Resume imports", "career", "Import jobs"),
    ("resume_extracted_items", "Extracted items", "career", "Parsed resume rows"),
    ("badge_definitions", "Badge definitions", "talent", "Badge catalog"),
    ("badge_criteria_versions", "Badge criteria", "talent", "Criteria versions"),
    ("earned_badges", "Earned badges", "talent", "Awards"),
    ("skill_evidence_records", "Skill evidence", "talent", "Evidence"),
    ("assessment_achievement_records", "Assessment achievements", "talent", "Assessment awards"),
    ("entitlements", "Entitlements", "talent", "Premium access"),
    ("generated_resumes", "Generated resumes", "talent", "Resume Studio"),
    ("generated_resume_versions", "Resume versions", "talent", "Version history"),
    ("resume_exports", "Resume exports", "talent", "Exports"),
    ("consents", "Consents", "talent", "Consent records"),
    ("candidate_evaluations", "Evaluations", "talent", "Recruiter notes"),
    ("career_audit_logs", "Career audit", "talent", "Career changes"),
    ("platform_settings", "Platform settings", "intelligence", "Global knobs"),
    ("project_analysis_preferences", "Analysis preferences", "intelligence", "Per-project prefs"),
    ("project_intelligence_settings", "PI settings", "intelligence", "Intelligence settings"),
    ("project_source_snapshots", "Source snapshots", "intelligence", "Snapshots"),
    ("project_analysis_jobs", "Analysis jobs", "intelligence", "Jobs"),
    ("project_evidence_claims", "Evidence claims", "intelligence", "Claims"),
    ("project_evidence_refs", "Evidence refs", "intelligence", "References"),
    ("project_plain_insights", "Plain insights", "intelligence", "Summaries"),
    ("project_ai_description_drafts", "AI drafts", "intelligence", "Draft descriptions"),
    ("project_ai_usage_ledger", "AI usage", "intelligence", "Usage ledger"),
    ("project_ai_publications", "AI publications", "intelligence", "Published AI text"),
    ("pi_visitor_ai_requests", "Visitor AI requests", "intelligence", "Visitor quota"),
    ("pi_monthly_usage", "Monthly PI usage", "intelligence", "Monthly counters"),
    ("pi_generation_records", "Generation records", "intelligence", "Generation log"),
    ("pending_registrations", "Pending signups", "email", "OTP signups"),
    ("email_outbox", "Email outbox", "email", "Queued mail"),
    ("email_delivery_events", "Delivery events", "email", "Delivery log"),
    ("email_change_requests", "Email changes", "email", "Change requests"),
    ("content_saves", "Saves", "engagement", "Saved content"),
    ("link_previews", "Link previews", "engagement", "Preview cache"),
    ("monthly_wraps", "Monthly wraps", "wraps", "Stored wraps"),
    ("user_blocks", "Blocks", "safety", "Blocks"),
    ("chat_mutes", "Mutes", "safety", "Mutes"),
    ("github_connections", "GitHub connections", "workspace", "Linked GitHub"),
    ("workspace_drafts", "Editor drafts", "workspace", "Drafts"),
    ("workspace_audit_events", "Workspace history", "workspace", "Sync history"),
    ("workspace_sync_jobs", "Sync jobs", "workspace", "Sync jobs"),
    ("project_contributors", "Contributors", "workspace", "GitHub contributors"),
    ("project_commits", "Commits", "workspace", "Commits"),
    ("project_pushes", "Pushes", "workspace", "Pushes"),
    ("notifications", "Notifications", "notifications", "In-app notices"),
    ("usage_events", "Visits", "usage", "Page visits"),
    ("password_reset_tokens", "Password resets", "security", "Reset links"),
    ("platform_admins", "Admins", "security", "Admin access"),
    ("platform_admin_invites", "Admin invites", "security", "Pending admin invites"),
    ("admin_proposals", "Approval requests", "security", "Pending votes"),
    ("admin_proposal_votes", "Approval votes", "security", "Votes"),
    ("admin_audit_events", "Audit log", "security", "Admin changes"),
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
        "editable": editable_columns(name),
        "removable": can_delete_table(name),
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
        "editable": editable_columns(name),
        "removable": can_delete_table(name),
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


def editable_columns(name: str) -> list[str]:
    if name in IMMUTABLE_TABLES:
        return []
    table = sql_table(name)
    pk = set(_pk_names(table))
    allowed = RECORD_UPDATE.get(name)
    out = []
    for column in table.c:
        if column.name in pk or column.name in _LOCKED_COLUMN_NAMES or _is_secret(column.name):
            continue
        if allowed is not None and column.name not in allowed:
            continue
        out.append(column.name)
    return out


def update_row(db, name: str, pk: dict[str, Any], fields: dict[str, Any]) -> None:
    allowed = set(editable_columns(name))
    if not allowed:
        raise HTTPException(status_code=400, detail="That record cannot be edited from the admin area")
    table = sql_table(name)
    if set(pk) != set(_pk_names(table)):
        raise HTTPException(status_code=400, detail="The record id is incomplete")
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(status_code=400, detail="Choose at least one field to change")
    values = {}
    for key, value in fields.items():
        key = str(key)
        if key not in allowed or key not in table.c:
            raise HTTPException(status_code=400, detail=f"{key} cannot be edited here")
        if value is None or value == "":
            values[key] = None
        elif isinstance(value, (dict, list)):
            values[key] = value
        else:
            values[key] = _coerce(table.c[key], str(value))
    stmt = sa_update(table).values(**values)
    for key, raw in pk.items():
        stmt = stmt.where(table.c[key] == _coerce(table.c[key], str(raw)))
    try:
        result = db.execute(stmt)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That change conflicts with something already stored.")
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="That record is already gone")


def can_delete_table(name: str) -> bool:
    return name not in IMMUTABLE_TABLES and name in _BY_NAME and name in Base.metadata.tables
