"""Admin changes. Dangerous ones are described here and do not run until enough admins approve."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.policy import normalize_username, username_problem
from app.models.admin import AdminInvite, AdminProposalVote, PlatformAdmin
from app.models.challenge import CHALLENGE_STATUSES, TechnicalChallenge
from app.models.enums import ProjectVisibility as Visibility
from app.models.profile import UserProfile
from app.models.project import Project
from app.models.user import User
from app.models.workspace import GitHubConnection
from app.models.clubs import CLUB_ROLES, ClubMember, NetworkClub
from app.services.admin_catalog import (
    IMMUTABLE_TABLES,
    TABLES,
    can_delete_table,
    delete_row,
    editable_columns,
    raw_row,
    table_spec,
    update_row,
)
from app.services.platform_admin import (
    OWNER_EMAIL,
    approval_requirement,
    is_owner_email,
    lock_proposal,
    open_proposal,
    proposal_dict,
    write_audit,
)

DELETE_GRACE = timedelta(days=30)
# The owner account can carry these out after the two confirms, without waiting on other admins.
DELETION_ACTIONS = frozenset({
    "user.purge",
    "user.schedule_delete",
    "project.delete",
    "challenge.delete",
    "record.delete",
    "club.delete",
})


def _owner_runs_deletion(actor: User, action: str) -> bool:
    return is_owner_email(actor.email) and action in DELETION_ACTIONS

# Overrides for delete severity / warning / typed confirm column. Everything else uses defaults.
_RECORD_DELETE_META: dict[str, tuple[str, str, str | None]] = {
    "network_activities": ("important", "Removes this network post for everyone.", "title"),
    "activity_comments": ("important", "Removes this comment for everyone.", "comment_text"),
    "messages": ("important", "Removes this DM for both people.", "content"),
    "room_messages": ("important", "Removes this group message for everyone.", "content"),
    "files": ("important", "Removes this file from the project tree.", "file_name"),
    "workspace_drafts": ("important", "Deletes unsaved editor work.", "path"),
    "chat_rooms": ("critical", "Deletes the whole group chat and its messages.", "title"),
    "shortlists": ("critical", "Deletes the shortlist and private notes inside it.", "name"),
    "monthly_wraps": ("critical", "Deletes this monthly wrap permanently.", None),
    "network_clubs": ("critical", "Deletes the club, members, events, polls, and funds.", "name"),
    "earned_badges": ("important", "Removes this earned badge from the person.", None),
    "entitlements": ("important", "Removes this premium entitlement row.", None),
    "generated_resumes": ("important", "Deletes this Resume Studio resume.", "title"),
    "club_members": ("important", "Removes this person from the club.", None),
    "club_announcements": ("important", "Removes this club announcement.", "title"),
    "club_events": ("important", "Removes this club event.", "title"),
    "club_polls": ("important", "Removes this club poll.", "question"),
    "club_showcases": ("important", "Removes this club showcase.", "title"),
    "club_fund_entries": ("important", "Removes this funds ledger row.", "note"),
    "content_saves": ("important", "Removes this saved item.", None),
    "email_outbox": ("important", "Removes this queued email.", None),
    "pending_registrations": ("important", "Cancels this pending signup.", None),
}


def _record_delete_spec(table: str) -> tuple[str, str, str | None]:
    if not can_delete_table(table):
        raise HTTPException(status_code=400, detail="That record cannot be removed from the admin area")
    if table in _RECORD_DELETE_META:
        return _RECORD_DELETE_META[table]
    label = table_spec(table)[1]
    return ("important", f"Removes this row from {label}.", None)


# Back-compat alias used by RecordDelete._meta
RECORD_DELETE = {name: _record_delete_spec(name) for name, *_ in TABLES if name not in IMMUTABLE_TABLES}


@dataclass
class Preview:
    action: str
    label: str
    severity: str
    warning: str
    typed_label: str
    typed_value: str
    casefold: bool
    summary: str
    required_approvals: int | None = None
    eligible_admins: int | None = None
    blocked: str | None = None
    target_user_id: str | None = None

    def public(self) -> dict:
        body = asdict(self)
        body.pop("target_user_id", None)
        return body


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value, message: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=message)


def load_user(db: Session, target_id) -> User:
    user = db.get(User, _uuid(target_id, "Choose a person first"))
    if user is None:
        raise HTTPException(status_code=404, detail="That account is no longer there")
    return user


def _email(value: str) -> str:
    try:
        parsed = TypeAdapter(EmailStr).validate_python((value or "").strip())
    except ValidationError:
        raise HTTPException(status_code=400, detail="Enter a real email address")
    return str(parsed).lower()


def _payload(payload) -> dict:
    if not payload:
        return {}
    if not isinstance(payload, dict) or len(str(payload)) > 8000:
        raise HTTPException(status_code=400, detail="Those details are not in the right form")
    return payload


def _matches(typed: str | None, preview: Preview) -> bool:
    got = (typed or "").strip()
    expected = preview.typed_value.strip()
    if not got or not expected:
        return False
    return got.casefold() == expected.casefold() if preview.casefold else got == expected


def _quorum(db: Session, actor: User, preview: Preview, target: UUID | None) -> Preview:
    if preview.severity != "critical" or preview.blocked:
        return preview
    if _owner_runs_deletion(actor, preview.action):
        preview.warning += (
            f" You are signed in as {OWNER_EMAIL}, so this runs as soon as you confirm. "
            "Other admins do not need to approve it."
        )
        preview.target_user_id = None if target is None else str(target)
        return preview
    try:
        required, eligible = approval_requirement(db, actor, target)
    except HTTPException as exc:
        preview.blocked = str(exc.detail)
        return preview
    preview.required_approvals = required
    preview.eligible_admins = eligible
    preview.target_user_id = None if target is None else str(target)
    preview.warning += " This waits for 2 admin approvals, including yours, before anything changes."
    return preview


def _owner_block(actor: User, target: User, *, never: bool, self_only: bool) -> str | None:
    if not is_owner_email(target.email):
        return None
    if never:
        return f"The owner account {OWNER_EMAIL} cannot be changed this way. It is the recovery account for the admin area."
    if self_only and actor.id != target.id:
        return "Only the owner can do that to the owner account."
    return None


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That change conflicts with something already stored, so nothing was saved.")


class UserAction:
    key = ""
    label = ""
    severity = "important"
    warning = ""
    never_owner = False
    self_only_owner = False
    block_self = False
    table = "users"

    def summary(self, user: User, payload: dict) -> str:
        return f"{self.label} for {user.email}"

    def apply(self, db: Session, actor: User, user: User, payload: dict) -> str:
        raise NotImplementedError

    def build(self, db: Session, actor: User, target_id, payload: dict) -> Preview:
        user = load_user(db, target_id)
        blocked = None
        if self.block_self and actor.id == user.id:
            blocked = "You cannot do that to your own account from the admin area."
        if blocked is None:
            blocked = _owner_block(actor, user, never=self.never_owner, self_only=self.self_only_owner)
        extra = self.prepare(db, actor, user, payload)
        if extra and blocked is None:
            blocked = extra
        preview = Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label=f"Type this email to confirm: {user.email}", typed_value=user.email, casefold=True,
            summary=self.summary(user, payload), blocked=blocked,
        )
        target = user.id if self.severity == "critical" else None
        return _quorum(db, actor, preview, target)

    def prepare(self, db: Session, actor: User, user: User, payload: dict) -> str | None:
        return None

    def run(self, db: Session, actor: User, target_id, payload: dict) -> str:
        user = load_user(db, target_id)
        if self.block_self and actor.id == user.id:
            raise HTTPException(status_code=400, detail="You cannot do that to your own account from the admin area.")
        blocked = _owner_block(actor, user, never=self.never_owner, self_only=self.self_only_owner)
        if blocked:
            raise HTTPException(status_code=400, detail=blocked)
        extra = self.prepare(db, actor, user, payload)
        if extra:
            raise HTTPException(status_code=400, detail=extra)
        summary = self.apply(db, actor, user, payload)
        _flush(db)
        return summary


class Pause(UserAction):
    key, label, self_only_owner = "user.pause", "Pause account", True
    warning = (
        "Pausing signs this person out and hides the account until they sign in again. "
        "Signing in clears a pause. It does not delete their projects or messages."
    )

    def prepare(self, db, actor, user, payload):
        if user.suspended_at is not None:
            return "This account is suspended. Restoring it is a separate action."
        if user.deactivated_at is not None:
            return "This account is already paused."
        return None

    def apply(self, db, actor, user, payload):
        user.deactivated_at = _now()
        user.delete_after = None
        user.token_version = (user.token_version or 0) + 1
        return f"Paused {user.email} until they sign in again"


class Unpause(UserAction):
    key, label = "user.unpause", "Clear pause"
    warning = "This makes a paused account visible again. If a deletion date is set, cancel that separately. A suspension stays in place."

    def prepare(self, db, actor, user, payload):
        if user.delete_after is not None:
            return "A deletion is scheduled. Cancel that instead of only clearing the pause."
        if user.deactivated_at is None:
            return "This account is not paused."
        return None

    def apply(self, db, actor, user, payload):
        user.deactivated_at = None
        return f"Cleared the pause on {user.email}"


class Suspend(UserAction):
    key, label, never_owner, block_self = "user.suspend", "Suspend account", True, True
    warning = (
        "Suspending locks this person out of DevZ-Go until an admin restores the account. "
        "Signing in does not clear it. Their projects and messages stay, but they cannot reach them."
    )

    def prepare(self, db, actor, user, payload):
        if user.suspended_at is not None:
            return "This account is already suspended."
        return None

    def apply(self, db, actor, user, payload):
        user.suspended_at = _now()
        user.suspended_reason = (payload.get("reason") or "")[:300] or None
        user.token_version = (user.token_version or 0) + 1
        return f"Suspended {user.email}"


class Unsuspend(UserAction):
    key, label = "user.unsuspend", "Restore suspended account"
    warning = "This lets the person sign in again. It does not delete the audit entry that recorded the suspension."

    def prepare(self, db, actor, user, payload):
        if user.suspended_at is None:
            return "This account is not suspended."
        return None

    def apply(self, db, actor, user, payload):
        user.suspended_at = None
        user.suspended_reason = None
        return f"Restored sign-in for {user.email}"


class SignOut(UserAction):
    key, label, self_only_owner = "user.sign_out", "Sign out everywhere", True
    warning = "This ends every signed-in session for this account. They can sign in again right away unless they are also suspended."

    def apply(self, db, actor, user, payload):
        user.token_version = (user.token_version or 0) + 1
        return f"Signed {user.email} out of every session"


class CancelDelete(UserAction):
    key, label = "user.cancel_delete", "Cancel scheduled deletion"
    warning = "This stops a scheduled permanent deletion. If the account was also paused, it stays paused until they sign in or an admin clears the pause."

    def prepare(self, db, actor, user, payload):
        if user.delete_after is None:
            return "No deletion is scheduled for this account."
        return None

    def apply(self, db, actor, user, payload):
        user.delete_after = None
        return f"Cancelled scheduled deletion for {user.email}"


class UpdateUsername(UserAction):
    key, label, self_only_owner = "user.update_username", "Change username", True
    warning = "The public handle changes everywhere it is shown. Old links that use the previous handle will not find them."

    def _name(self, payload: dict) -> str:
        name = normalize_username(str(payload.get("username") or ""))
        problem = username_problem(name)
        if problem == "reserved":
            raise HTTPException(status_code=400, detail="That username is reserved")
        if problem:
            raise HTTPException(status_code=400, detail="Username must be 3–30 characters: letters, numbers, _ . -")
        return name

    def summary(self, user, payload):
        return f"Change @{user.username} to @{self._name(payload)}"

    def prepare(self, db, actor, user, payload):
        name = self._name(payload)
        if name == user.username.lower():
            return "That is already their username."
        taken = db.query(User.id).filter(func.lower(User.username) == name, User.id != user.id).first()
        if taken:
            return "That username is already taken."
        return None

    def apply(self, db, actor, user, payload):
        name = self._name(payload)
        previous = user.username
        user.username = name
        return f"Changed @{previous} to @{name}"


class UpdateEmail(UserAction):
    key, label, never_owner = "user.update_email", "Change email", True
    warning = (
        "They will sign in with the new email. The old email loses access. "
        "Do this only when you are sure the new address belongs to the same person."
    )

    def _next(self, user: User, payload: dict) -> str:
        email = _email(str(payload.get("email") or ""))
        if is_owner_email(email) and not is_owner_email(user.email):
            raise HTTPException(status_code=400, detail=f"{OWNER_EMAIL} is reserved for the owner account.")
        return email

    def summary(self, user, payload):
        return f"Change {user.email} to {self._next(user, payload)}"

    def prepare(self, db, actor, user, payload):
        email = self._next(user, payload)
        if email == user.email.lower():
            return "That is already their email."
        taken = db.query(User.id).filter(func.lower(User.email) == email, User.id != user.id).first()
        if taken:
            return "That email is already registered."
        return None

    def apply(self, db, actor, user, payload):
        email = self._next(user, payload)
        previous = user.email
        user.email = email
        user.token_version = (user.token_version or 0) + 1
        return f"Changed {previous} to {email} and signed them out"


class ScheduleDelete(UserAction):
    key, label = "user.schedule_delete", "Schedule permanent deletion"
    severity, never_owner, block_self = "critical", True, True
    warning = (
        "This hides the account and permanently deletes it in 30 days unless an admin cancels it. "
        "Their projects, messages and profile are removed when that date arrives."
    )

    def prepare(self, db, actor, user, payload):
        if user.delete_after is not None:
            return "A deletion is already scheduled. Cancel it, or purge the account now if it must go immediately."
        if db.query(PlatformAdmin.user_id).filter(PlatformAdmin.user_id == user.id).first():
            return "Remove this person's admin access before scheduling deletion."
        return None

    def apply(self, db, actor, user, payload):
        now = _now()
        user.deactivated_at = now
        user.delete_after = now + DELETE_GRACE
        user.token_version = (user.token_version or 0) + 1
        return f"Scheduled permanent deletion of {user.email}"


class PurgeUser(UserAction):
    key, label = "user.purge", "Delete account now"
    severity, never_owner, block_self = "critical", True, True
    warning = (
        "This permanently deletes the account, the projects they own, and the messages tied to them. "
        "Groups they started are removed too. There is no undo."
    )

    def prepare(self, db, actor, user, payload):
        if db.query(PlatformAdmin.user_id).filter(PlatformAdmin.user_id == user.id).first():
            return "Remove this person's admin access before deleting the account."
        return None

    def apply(self, db, actor, user, payload):
        email = user.email
        for project in db.query(Project).filter(Project.owner_id == user.id).all():
            db.delete(project)
        db.query(AdminInvite).filter(AdminInvite.email == email.lower()).delete(synchronize_session=False)
        _flush(db)
        db.delete(user)
        return f"Permanently deleted {email}"


class ChangeType(UserAction):
    key, label = "user.change_type", "Change account type"
    warning = (
        "This switches what DevZ-Go lets them do as soon as you confirm. "
        "A recruiter loses project building, imports and wraps. A developer loses the recruiting tools. "
        "Projects and shortlists stay on the account. They are signed out and come back as the new type."
    )

    def _kind(self, user: User, payload: dict) -> str:
        kind = str(payload.get("account_type") or "")
        if kind not in {"developer", "recruiter"}:
            raise HTTPException(status_code=400, detail="Account type must be developer or recruiter")
        return kind

    def summary(self, user, payload):
        return f"Change {user.email} from {user.account_type} to {self._kind(user, payload)}"

    def prepare(self, db, actor, user, payload):
        if self._kind(user, payload) == user.account_type:
            return "The account is already that type."
        return None

    def apply(self, db, actor, user, payload):
        kind = self._kind(user, payload)
        previous = user.account_type
        user.account_type = kind
        user.token_version = (user.token_version or 0) + 1
        profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one_or_none()
        if profile is not None and kind == "recruiter":
            profile.role = "Recruiter"
        elif profile is not None and profile.role == "Recruiter":
            profile.role = "Developer"
        return f"Changed {user.email} from {previous} to {kind} and signed them out"


class ProjectAction:
    table = "projects"
    key = ""
    label = ""
    severity = "important"
    warning = ""

    def build(self, db, actor, target_id, payload) -> Preview:
        project = _project(db, target_id)
        blocked = self.prepare(project, payload)
        typed = project.title.strip() if project.title and len(project.title.strip()) <= 120 else str(project.id)
        preview = Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the project title to confirm" if typed == (project.title or "").strip() else "Type the project id to confirm",
            typed_value=typed, casefold=False, summary=self.summary(project, payload), blocked=blocked,
        )
        return _quorum(db, actor, preview, None)

    def prepare(self, project: Project, payload: dict) -> str | None:
        return None

    def summary(self, project: Project, payload: dict) -> str:
        return f"{self.label}: {project.title}"

    def run(self, db, actor, target_id, payload) -> str:
        project = _project(db, target_id)
        blocked = self.prepare(project, payload)
        if blocked:
            raise HTTPException(status_code=400, detail=blocked)
        summary = self.apply(db, project, payload)
        _flush(db)
        return summary

    def apply(self, db, project: Project, payload: dict) -> str:
        raise NotImplementedError


def _project(db: Session, target_id) -> Project:
    project = db.get(Project, _uuid(target_id, "Choose a project first"))
    if project is None:
        raise HTTPException(status_code=404, detail="That project is no longer there")
    return project


def _visibility_name(project: Project) -> str:
    visibility = project.visibility
    return visibility.name if isinstance(visibility, Visibility) else str(visibility)


class SetVisibility(ProjectAction):
    key, label = "project.visibility", "Change project visibility"
    warning = "Private hides the project from explore and the network. Public shows it to signed-in people. This does not delete the project."

    def _value(self, payload) -> str:
        value = str(payload.get("visibility") or "")
        if value not in {"PUBLIC", "PRIVATE"}:
            raise HTTPException(status_code=400, detail="Visibility must be public or private")
        return value

    def summary(self, project, payload):
        word = "public" if self._value(payload) == "PUBLIC" else "private"
        return f"Make “{project.title}” {word}"

    def prepare(self, project, payload):
        if _visibility_name(project) == self._value(payload):
            return "The project is already set that way."
        return None

    def apply(self, db, project, payload):
        value = self._value(payload)
        project.visibility = Visibility.PUBLIC if value == "PUBLIC" else Visibility.PRIVATE
        word = "public" if value == "PUBLIC" else "private"
        return f"Made “{project.title}” {word}"


class ProjectComments(ProjectAction):
    key, label = "project.comments", "Change project comments"
    warning = "Turning comments off hides the comment box. Existing comments stay until they are removed one by one."

    def prepare(self, project, payload):
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="Choose whether comments stay on")
        if bool(project.comments_enabled) == enabled:
            return "Comments are already set that way."
        return None

    def apply(self, db, project, payload):
        project.comments_enabled = payload["enabled"]
        state = "on" if payload["enabled"] else "off"
        return f"Turned comments {state} for “{project.title}”"


class ProjectLikes(ProjectAction):
    key, label = "project.likes_hidden", "Change like visibility"
    warning = "Hiding likes keeps the counts private on that project. It does not remove the likes."

    def prepare(self, project, payload):
        hidden = payload.get("hidden")
        if not isinstance(hidden, bool):
            raise HTTPException(status_code=400, detail="Choose whether likes are hidden")
        if bool(project.likes_hidden) == hidden:
            return "Likes are already set that way."
        return None

    def apply(self, db, project, payload):
        project.likes_hidden = payload["hidden"]
        state = "hidden" if payload["hidden"] else "visible"
        return f"Made likes {state} on “{project.title}”"


class ProjectEdit(ProjectAction):
    key, label = "project.edit", "Edit project text"
    warning = "The title and short description are what people see in explore and on the project page. This does not change the code files."

    def _clean(self, payload) -> tuple[str | None, str | None]:
        title = payload.get("title")
        short = payload.get("short_description")
        if title is None and short is None:
            raise HTTPException(status_code=400, detail="Change the title or the short description")
        cleaned_title = None
        if title is not None:
            cleaned_title = " ".join(str(title).split())
            if not cleaned_title or len(cleaned_title) > 200:
                raise HTTPException(status_code=400, detail="The title needs to be 1–200 characters")
        cleaned_short = None
        if short is not None:
            cleaned_short = str(short).strip()
            if len(cleaned_short) > 400:
                raise HTTPException(status_code=400, detail="Keep the short description under 400 characters")
        return cleaned_title, cleaned_short

    def summary(self, project, payload):
        title, _short = self._clean(payload)
        return f"Edit the text of “{title or project.title}”"

    def apply(self, db, project, payload):
        title, short = self._clean(payload)
        if title is not None:
            project.title = title
        if short is not None:
            project.short_description = short or None
        return f"Updated the text of “{project.title}”"


class ProjectDelete(ProjectAction):
    key, label, severity = "project.delete", "Delete project", "critical"
    warning = (
        "This permanently deletes the project, its files, comments, members and workspace history. "
        "There is no undo."
    )

    def apply(self, db, project, payload):
        title = project.title
        db.delete(project)
        return f"Permanently deleted the project “{title}”"


class ChallengeStatus:
    key, label, severity, table = "challenge.status", "Change challenge status", "important", "technical_challenges"
    warning = "Closing a challenge stops new submissions. Reopening it lets people submit again while it is public or they are invited."

    def _row(self, db, target_id) -> TechnicalChallenge:
        row = db.get(TechnicalChallenge, _uuid(target_id, "Choose a challenge first"))
        if row is None:
            raise HTTPException(status_code=404, detail="That challenge is no longer there")
        return row

    def _status(self, payload) -> str:
        status = str(payload.get("status") or "")
        if status not in CHALLENGE_STATUSES:
            raise HTTPException(status_code=400, detail="Status must be draft, open or closed")
        return status

    def build(self, db, actor, target_id, payload) -> Preview:
        row = self._row(db, target_id)
        status = self._status(payload)
        blocked = "The challenge is already in that state." if row.status == status else None
        typed = row.title.strip() if len(row.title.strip()) <= 120 else str(row.id)
        return Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the challenge title to confirm", typed_value=typed, casefold=False,
            summary=f"Set “{row.title}” to {status}", blocked=blocked,
        )

    def run(self, db, actor, target_id, payload) -> str:
        row = self._row(db, target_id)
        status = self._status(payload)
        if row.status == status:
            raise HTTPException(status_code=400, detail="The challenge is already in that state.")
        row.status = status
        if status == "closed":
            row.closed_at = _now()
        if status == "open" and row.published_at is None:
            row.published_at = _now()
        _flush(db)
        return f"Set “{row.title}” to {status}"


class ChallengeDelete:
    key, label, severity, table = "challenge.delete", "Delete challenge", "critical", "technical_challenges"
    warning = "This permanently deletes the challenge, its invitations, submissions and reviews. Developers lose that submission."

    def build(self, db, actor, target_id, payload) -> Preview:
        row = db.get(TechnicalChallenge, _uuid(target_id, "Choose a challenge first"))
        if row is None:
            raise HTTPException(status_code=404, detail="That challenge is no longer there")
        typed = row.title.strip() if len(row.title.strip()) <= 120 else str(row.id)
        preview = Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the challenge title to confirm", typed_value=typed, casefold=False,
            summary=f"Delete the challenge “{row.title}”",
        )
        return _quorum(db, actor, preview, None)

    def run(self, db, actor, target_id, payload) -> str:
        row = db.get(TechnicalChallenge, _uuid(target_id, "Choose a challenge first"))
        if row is None:
            raise HTTPException(status_code=404, detail="That challenge is no longer there")
        title = row.title
        db.delete(row)
        _flush(db)
        return f"Permanently deleted the challenge “{title}”"


class GithubDisconnect:
    key, label, severity, table = "github.disconnect", "Disconnect GitHub", "critical", "github_connections"
    warning = (
        "This forgets the stored GitHub connection and its tokens. "
        "The person cannot push from the editor until they connect GitHub again. Projects already imported stay."
    )

    def _row(self, db, target_id) -> GitHubConnection:
        row = db.get(GitHubConnection, _uuid(target_id, "Choose a GitHub connection first"))
        if row is None:
            raise HTTPException(status_code=404, detail="That GitHub connection is no longer there")
        return row

    def build(self, db, actor, target_id, payload) -> Preview:
        row = self._row(db, target_id)
        typed = row.login or str(row.id)
        blocked = "GitHub is already disconnected." if row.status == "revoked" and not row.access_token_enc else None
        preview = Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the GitHub login to confirm", typed_value=typed, casefold=True,
            summary=f"Disconnect GitHub @{typed}", blocked=blocked,
        )
        return _quorum(db, actor, preview, None)

    def run(self, db, actor, target_id, payload) -> str:
        row = self._row(db, target_id)
        login = row.login or str(row.id)
        row.access_token_enc = None
        row.refresh_token_enc = None
        row.status = "revoked"
        row.last_error = "Disconnected by an admin"
        _flush(db)
        return f"Disconnected GitHub @{login}"


class GrantAdmin:
    key, label, severity, table = "admin.grant", "Grant admin access", "important", "platform_admins"
    warning = (
        "This person will be able to open the admin area, see accounts, projects and messages, "
        "and approve or stop dangerous changes. Grant this only to someone you trust."
    )

    def build(self, db, actor, target_id, payload) -> Preview:
        email = _email(str(payload.get("email") or target_id or ""))
        blocked = None
        if is_owner_email(email):
            blocked = f"{OWNER_EMAIL} is the owner. That account already has access when it signs in."
        else:
            existing = db.query(User).filter(func.lower(User.email) == email).one_or_none()
            if existing is not None and db.query(PlatformAdmin).filter(PlatformAdmin.user_id == existing.id).first():
                blocked = "That person is already an admin."
            elif existing is None and db.query(AdminInvite).filter(AdminInvite.email == email).first():
                blocked = "That email is already invited. They get access when they sign up."
        return Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the email to confirm", typed_value=email, casefold=True,
            summary=f"Grant admin access to {email}", blocked=blocked,
        )

    def run(self, db, actor, target_id, payload) -> str:
        email = _email(str(payload.get("email") or target_id or ""))
        if is_owner_email(email):
            raise HTTPException(status_code=400, detail=f"{OWNER_EMAIL} is already the owner account.")
        existing = db.query(User).filter(func.lower(User.email) == email).one_or_none()
        if existing is not None:
            if db.query(PlatformAdmin).filter(PlatformAdmin.user_id == existing.id).first():
                raise HTTPException(status_code=400, detail="That person is already an admin.")
            db.add(PlatformAdmin(user_id=existing.id, role="admin", granted_by=actor.id))
            _flush(db)
            return f"Granted admin access to {email}"
        if db.query(AdminInvite).filter(AdminInvite.email == email).first():
            raise HTTPException(status_code=400, detail="That email is already invited.")
        db.add(AdminInvite(email=email, role="admin", granted_by=actor.id))
        _flush(db)
        return f"Invited {email}. They get admin access when they sign up with that email."


class RevokeInvite:
    key, label, severity, table = "admin.revoke_invite", "Remove admin invite", "important", "platform_admin_invites"
    warning = "This email will not become an admin when it signs up. Anyone who already has access is unchanged; remove that from the admin list."

    def build(self, db, actor, target_id, payload) -> Preview:
        email = _email(str(payload.get("email") or target_id or ""))
        invite = db.query(AdminInvite).filter(AdminInvite.email == email).one_or_none()
        blocked = None if invite else "There is no invite for that email."
        if invite and invite.role == "owner":
            blocked = f"The owner invite cannot be removed. {OWNER_EMAIL} keeps access."
        return Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the email to confirm", typed_value=email, casefold=True,
            summary=f"Remove the admin invite for {email}", blocked=blocked,
        )

    def run(self, db, actor, target_id, payload) -> str:
        email = _email(str(payload.get("email") or target_id or ""))
        invite = db.query(AdminInvite).filter(AdminInvite.email == email).one_or_none()
        if invite is None:
            raise HTTPException(status_code=404, detail="There is no invite for that email.")
        if invite.role == "owner" or is_owner_email(email):
            raise HTTPException(status_code=400, detail="The owner invite cannot be removed.")
        db.delete(invite)
        _flush(db)
        return f"Removed the admin invite for {email}"


class RevokeAdmin:
    key, label, severity, table = "admin.revoke", "Remove admin access", "critical", "platform_admins"
    warning = (
        "This person will lose the admin area, including the ability to approve dangerous changes. "
        "Their normal DevZ-Go account stays. The owner account cannot be removed."
    )

    def build(self, db, actor, target_id, payload) -> Preview:
        user = load_user(db, target_id)
        blocked = _owner_block(actor, user, never=True, self_only=False)
        if blocked is None and actor.id == user.id:
            blocked = "Ask another admin to remove your access. You cannot remove yourself."
        if blocked is None and not db.query(PlatformAdmin).filter(PlatformAdmin.user_id == user.id).first():
            blocked = "That person is not an admin."
        preview = Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type this email to confirm", typed_value=user.email, casefold=True,
            summary=f"Remove admin access from {user.email}", blocked=blocked,
        )
        return _quorum(db, actor, preview, user.id)

    def run(self, db, actor, target_id, payload) -> str:
        user = load_user(db, target_id)
        if is_owner_email(user.email):
            raise HTTPException(status_code=400, detail="The owner account cannot be removed.")
        row = db.query(PlatformAdmin).filter(PlatformAdmin.user_id == user.id).one_or_none()
        if row is None or row.role == "owner":
            raise HTTPException(status_code=400, detail="That person is not a removable admin.")
        if db.query(PlatformAdmin).count() <= 1:
            raise HTTPException(status_code=400, detail="DevZ-Go needs at least one admin. This was not removed.")
        db.delete(row)
        db.query(AdminInvite).filter(AdminInvite.email == user.email.lower()).delete(synchronize_session=False)
        _flush(db)
        return f"Removed admin access from {user.email}"


def _premium_days(payload: dict) -> int | None:
    raw = payload.get("days")
    if raw is None or raw == "" or str(raw).lower() in {"0", "forever", "none", "null"}:
        return None
    try:
        days = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="days must be a whole number, or leave it blank for no expiry") from exc
    if days < 1 or days > 3650:
        raise HTTPException(status_code=400, detail="days must be between 1 and 3650")
    return days


def _notify_premium_granted(db: Session, user: User, days: int | None) -> None:
    from app.services import email_service
    if not user.email:
        return
    expires_label = f"for {days} day{'s' if days != 1 else ''}" if days else "with no set end date"
    outbox = email_service.enqueue(
        db,
        event_type="premium_granted",
        template="premium_granted",
        to_email=user.email,
        context={"username": user.username, "days": days, "expires_label": expires_label},
        idempotency_key=f"premium_granted:{user.id}:{days or 'forever'}:{_now().date().isoformat()}",
    )
    email_service.try_send_now(db, outbox)


class GrantPremium(UserAction):
    key, label = "premium.grant", "Grant Premium (Resume Studio + Project Intelligence)"
    severity = "important"
    warning = (
        "This unlocks Resume Studio generate/export and Project Intelligence Premium for this developer. "
        "It does not change matching, badges, or search ranking. Optional days set an expiry; they get an email when it ends."
    )

    def prepare(self, db, actor, user, payload):
        if user.is_recruiter:
            return "Premium features are for developer accounts."
        _premium_days(payload)
        return None

    def summary(self, user, payload):
        days = None
        try:
            days = _premium_days(payload)
        except HTTPException:
            pass
        if days:
            return f"Grant Premium (Resume Studio + PI) to {user.email} for {days} days"
        return f"Grant Premium (Resume Studio + PI) to {user.email} with no expiry"

    def apply(self, db, actor, user, payload):
        from app.services.entitlements import grant_premium_bundle
        days = _premium_days(payload)
        grant_premium_bundle(db, user.id, source="admin_grant", granted_by=actor.id, days=days)
        _notify_premium_granted(db, user, days)
        if days:
            return f"Granted Premium (Resume Studio + PI) to {user.email} for {days} days"
        return f"Granted Premium (Resume Studio + PI) to {user.email} with no expiry"


class RevokePremium(UserAction):
    key, label = "premium.revoke", "Revoke Premium (Resume Studio + Project Intelligence)"
    severity = "important"
    warning = (
        "This removes Resume Studio Premium and Project Intelligence Premium. "
        "Free resume import, badges, and free-tier PI stay available."
    )

    def prepare(self, db, actor, user, payload):
        from app.models.entitlements import Entitlement
        from app.services.entitlements import PREMIUM_FEATURES
        has_any = (
            db.query(Entitlement)
            .filter(Entitlement.user_id == user.id, Entitlement.feature.in_(PREMIUM_FEATURES))
            .first()
        )
        if not has_any:
            return "This person does not have a premium entitlement row."
        return None

    def summary(self, user, payload):
        return f"Revoke Premium (Resume Studio + PI) from {user.email}"

    def apply(self, db, actor, user, payload):
        from app.services.entitlements import revoke_premium_bundle
        revoke_premium_bundle(db, user.id)
        return f"Revoked Premium (Resume Studio + PI) from {user.email}"


class GrantPremiumAll:
    key, label, severity = "premium.grant_all", "Grant Premium to all developers", "critical"
    warning = (
        "This grants Resume Studio Premium and Project Intelligence Premium to every developer account. "
        "Optional days apply the same expiry to everyone. Matching and badges are unchanged."
    )

    def build(self, db, actor, target_id, payload) -> Preview:
        days = _premium_days(payload)
        count = db.query(User).filter(User.account_type != "recruiter").count()
        summary = (
            f"Grant Premium (Resume Studio + PI) to all {count} developers for {days} days"
            if days else f"Grant Premium (Resume Studio + PI) to all {count} developers with no expiry"
        )
        preview = Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label='Type GRANT PREMIUM ALL to confirm', typed_value="GRANT PREMIUM ALL", casefold=False,
            summary=summary,
        )
        return _quorum(db, actor, preview, None)

    def run(self, db, actor, target_id, payload) -> str:
        from app.services.entitlements import grant_all_developers
        days = _premium_days(payload)
        count = grant_all_developers(db, source="admin_grant", granted_by=actor.id, days=days)
        developers = (
            db.query(User)
            .filter(User.account_type != "recruiter", User.email.isnot(None))
            .limit(500)
            .all()
        )
        for user in developers:
            _notify_premium_granted(db, user, days)
        _flush(db)
        if days:
            return f"Granted Premium (Resume Studio + PI) to {count} developers for {days} days"
        return f"Granted Premium (Resume Studio + PI) to {count} developers with no expiry"


def _clean_pk(payload: dict) -> dict[str, str]:
    pk = payload.get("pk")
    if not isinstance(pk, dict) or not pk:
        raise HTTPException(status_code=400, detail="Choose a record first")
    return {str(key): str(value) for key, value in pk.items() if value is not None and not str(key).startswith("_")}


def _typed_from_row(row: dict, column: str | None, pk: dict) -> str:
    if column and column in row:
        text = row[column]
        if isinstance(text, str):
            compact = " ".join(text.split())
            if 1 <= len(compact) <= 80:
                return compact
    if "id" in pk:
        return pk["id"]
    return " / ".join(pk[key] for key in sorted(pk))


def _load_club(db: Session, club_id) -> NetworkClub:
    club = db.get(NetworkClub, _uuid(club_id, "Choose a club first"))
    if club is None:
        raise HTTPException(status_code=404, detail="That club is no longer there")
    return club


def _club_member(db: Session, club: NetworkClub, user_id) -> ClubMember:
    member = (
        db.query(ClubMember)
        .filter(ClubMember.club_id == club.id, ClubMember.user_id == _uuid(user_id, "Choose a member first"))
        .first()
    )
    if member is None:
        raise HTTPException(status_code=404, detail="That person is not in this club")
    return member


class RecordDelete:
    key = "record.delete"

    def _meta(self, payload) -> tuple[str, dict, tuple[str, str, str | None]]:
        table = str(payload.get("table") or "")
        spec = _record_delete_spec(table)
        table_spec(table)
        return table, _clean_pk(payload), spec

    def build(self, db, actor, target_id, payload) -> Preview:
        table, pk, (severity, warning, column) = self._meta(payload)
        row = raw_row(db, table, pk)
        if row is None:
            raise HTTPException(status_code=404, detail="That record is no longer there")
        label = table_spec(table)[1]
        typed = _typed_from_row(row, column, pk)
        preview = Preview(
            action=self.key, label=f"Remove {label[:-1] if label.endswith('s') else label}",
            severity=severity, warning=warning,
            typed_label="Type this to confirm", typed_value=typed, casefold=False,
            summary=f"Remove a row from {label}",
        )
        return _quorum(db, actor, preview, None)

    def run(self, db, actor, target_id, payload) -> str:
        table, pk, (_severity, _warning, _column) = self._meta(payload)
        if raw_row(db, table, pk) is None:
            raise HTTPException(status_code=404, detail="That record is already gone")
        delete_row(db, table, pk)
        label = table_spec(table)[1]
        return f"Removed a row from {label}"


class RecordUpdate:
    key = "record.update"

    def _meta(self, payload) -> tuple[str, dict, dict]:
        table = str(payload.get("table") or "")
        table_spec(table)
        allowed = editable_columns(table)
        if not allowed:
            raise HTTPException(status_code=400, detail="That record cannot be edited from the admin area")
        fields = payload.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise HTTPException(status_code=400, detail="Choose at least one field to change")
        cleaned = {str(k): v for k, v in fields.items() if str(k) in allowed}
        if not cleaned:
            raise HTTPException(status_code=400, detail="Those fields cannot be edited here")
        return table, _clean_pk(payload), cleaned

    def build(self, db, actor, target_id, payload) -> Preview:
        table, pk, fields = self._meta(payload)
        row = raw_row(db, table, pk)
        if row is None:
            raise HTTPException(status_code=404, detail="That record is no longer there")
        label = table_spec(table)[1]
        typed = _typed_from_row(row, "name" if "name" in row else ("title" if "title" in row else None), pk)
        keys = ", ".join(sorted(fields))
        preview = Preview(
            action=self.key, label=f"Edit {label[:-1] if label.endswith('s') else label}",
            severity="important",
            warning=f"Updates {keys} on this {label[:-1] if label.endswith('s') else label.lower()} row.",
            typed_label="Type this to confirm", typed_value=typed, casefold=False,
            summary=f"Edit {keys} on {label}",
        )
        return preview

    def run(self, db, actor, target_id, payload) -> str:
        table, pk, fields = self._meta(payload)
        if raw_row(db, table, pk) is None:
            raise HTTPException(status_code=404, detail="That record is already gone")
        update_row(db, table, pk, fields)
        label = table_spec(table)[1]
        return f"Updated {', '.join(sorted(fields))} on {label}"


class ClubVerify:
    key, label, severity = "club.verify", "Verify club", "important"
    warning = "Marks this club as verified. The verified badge appears on the club page."

    def build(self, db, actor, target_id, payload) -> Preview:
        club = _load_club(db, target_id)
        return Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the club name to confirm", typed_value=club.name, casefold=True,
            summary=f"Verify club {club.name}",
        )

    def run(self, db, actor, target_id, payload) -> str:
        club = _load_club(db, target_id)
        club.verification_status = "verified"
        club.verification_note = None
        club.verified_at = _now()
        club.verified_by = actor.id
        _flush(db)
        return f"Verified club {club.name}"


class ClubReject:
    key, label, severity = "club.reject", "Reject club verification", "important"
    warning = "Rejects the verification request. Optional note is stored on the club."

    def build(self, db, actor, target_id, payload) -> Preview:
        club = _load_club(db, target_id)
        return Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the club name to confirm", typed_value=club.name, casefold=True,
            summary=f"Reject verification for {club.name}",
        )

    def run(self, db, actor, target_id, payload) -> str:
        club = _load_club(db, target_id)
        note = str(payload.get("note") or "").strip()[:500] or None
        club.verification_status = "rejected"
        club.verification_note = note
        club.verified_at = None
        club.verified_by = None
        _flush(db)
        return f"Rejected verification for {club.name}"


class ClubRevokeVerify:
    key, label, severity = "club.revoke_verify", "Revoke club verification", "important"
    warning = "Removes the verified badge. The club stays online."

    def build(self, db, actor, target_id, payload) -> Preview:
        club = _load_club(db, target_id)
        return Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the club name to confirm", typed_value=club.name, casefold=True,
            summary=f"Revoke verification for {club.name}",
        )

    def run(self, db, actor, target_id, payload) -> str:
        club = _load_club(db, target_id)
        club.verification_status = "none"
        club.verification_note = None
        club.verified_at = None
        club.verified_by = None
        _flush(db)
        return f"Revoked verification for {club.name}"


class ClubEdit:
    key, label, severity = "club.edit", "Edit club", "important"
    warning = "Updates club profile fields visible to members and visitors."
    _FIELDS = frozenset({
        "name", "tagline", "about", "category", "institution", "join_policy",
        "policies", "purpose", "funds_visibility", "slug",
    })

    def _fields(self, payload: dict) -> dict:
        raw = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        cleaned = {str(k): (None if v is None else str(v)) for k, v in raw.items() if str(k) in self._FIELDS}
        if not cleaned:
            raise HTTPException(status_code=400, detail="Choose at least one club field to change")
        return cleaned

    def build(self, db, actor, target_id, payload) -> Preview:
        club = _load_club(db, target_id)
        fields = self._fields(payload)
        return Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the club name to confirm", typed_value=club.name, casefold=True,
            summary=f"Edit {', '.join(sorted(fields))} on {club.name}",
        )

    def run(self, db, actor, target_id, payload) -> str:
        club = _load_club(db, target_id)
        fields = self._fields(payload)
        for key, value in fields.items():
            setattr(club, key, value)
        _flush(db)
        return f"Updated {', '.join(sorted(fields))} on {club.name}"


class ClubDelete:
    key, label, severity = "club.delete", "Delete club", "critical"
    warning = "Permanently deletes the club and all members, events, polls, funds, and showcases. No undo."

    def build(self, db, actor, target_id, payload) -> Preview:
        club = _load_club(db, target_id)
        preview = Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the club name to confirm", typed_value=club.name, casefold=True,
            summary=f"Delete club {club.name}",
        )
        return _quorum(db, actor, preview, None)

    def run(self, db, actor, target_id, payload) -> str:
        club = _load_club(db, target_id)
        name = club.name
        db.delete(club)
        _flush(db)
        return f"Deleted club {name}"


class ClubSetPresident:
    key, label, severity = "club.set_president", "Assign club president", "important"
    warning = (
        "Makes this member president of the club. The previous president becomes executive. "
        "A person may only preside over one club."
    )

    def build(self, db, actor, target_id, payload) -> Preview:
        club = _load_club(db, target_id)
        user_id = payload.get("user_id")
        member = _club_member(db, club, user_id)
        user = load_user(db, member.user_id)
        return Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the club name to confirm", typed_value=club.name, casefold=True,
            summary=f"Make @{user.username} president of {club.name}",
        )

    def run(self, db, actor, target_id, payload) -> str:
        club = _load_club(db, target_id)
        member = _club_member(db, club, payload.get("user_id"))
        for other in db.query(ClubMember).filter(ClubMember.club_id == club.id, ClubMember.role == "president").all():
            if other.user_id != member.user_id:
                other.role = "executive"
                if not other.custom_title or other.custom_title.lower() == "president":
                    other.custom_title = "Executive"
        member.role = "president"
        member.custom_title = (str(payload.get("custom_title") or "").strip() or "President")
        try:
            _flush(db)
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="That person already presidents another club.")
        user = load_user(db, member.user_id)
        return f"Made @{user.username} president of {club.name}"


class ClubSetRole:
    key, label, severity = "club.set_role", "Change club member role", "important"
    warning = "Changes this member's role. Use Assign president to transfer the presidency."

    def _role(self, payload: dict) -> str:
        role = str(payload.get("role") or "").strip().lower()
        if role == "host":
            role = "president"
        if role not in CLUB_ROLES:
            raise HTTPException(status_code=400, detail="Choose a valid club role")
        if role == "president":
            raise HTTPException(status_code=400, detail="Use Assign president to transfer the presidency")
        return role

    def build(self, db, actor, target_id, payload) -> Preview:
        club = _load_club(db, target_id)
        member = _club_member(db, club, payload.get("user_id"))
        role = self._role(payload)
        user = load_user(db, member.user_id)
        return Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the club name to confirm", typed_value=club.name, casefold=True,
            summary=f"Set @{user.username} to {role} in {club.name}",
        )

    def run(self, db, actor, target_id, payload) -> str:
        club = _load_club(db, target_id)
        member = _club_member(db, club, payload.get("user_id"))
        role = self._role(payload)
        if member.role == "president":
            raise HTTPException(status_code=400, detail="Assign a new president before changing the current president's role.")
        defaults = {
            "vice_president": "Vice President",
            "treasurer": "Treasurer",
            "executive": "Executive",
            "member": "Member",
        }
        member.role = role
        title = str(payload.get("custom_title") or "").strip()
        member.custom_title = title or defaults.get(role, "Member")
        _flush(db)
        user = load_user(db, member.user_id)
        return f"Set @{user.username} to {role} in {club.name}"


class ClubRemoveMember:
    key, label, severity = "club.remove_member", "Remove club member", "important"
    warning = "Removes this person from the club. They can rejoin if the join policy allows it."

    def build(self, db, actor, target_id, payload) -> Preview:
        club = _load_club(db, target_id)
        member = _club_member(db, club, payload.get("user_id"))
        user = load_user(db, member.user_id)
        blocked = None
        if member.role == "president":
            blocked = "Assign a new president before removing the current president."
        return Preview(
            action=self.key, label=self.label, severity=self.severity, warning=self.warning,
            typed_label="Type the club name to confirm", typed_value=club.name, casefold=True,
            summary=f"Remove @{user.username} from {club.name}",
            blocked=blocked,
        )

    def run(self, db, actor, target_id, payload) -> str:
        club = _load_club(db, target_id)
        member = _club_member(db, club, payload.get("user_id"))
        if member.role == "president":
            raise HTTPException(status_code=400, detail="Assign a new president before removing the current president.")
        user = load_user(db, member.user_id)
        db.delete(member)
        _flush(db)
        return f"Removed @{user.username} from {club.name}"


ACTIONS = {
    item.key: item
    for item in (
        Pause(), Unpause(), Suspend(), Unsuspend(), SignOut(), CancelDelete(),
        UpdateUsername(), UpdateEmail(), ScheduleDelete(), PurgeUser(), ChangeType(),
        SetVisibility(), ProjectComments(), ProjectLikes(), ProjectEdit(), ProjectDelete(),
        ChallengeStatus(), ChallengeDelete(), GithubDisconnect(),
        GrantAdmin(), RevokeInvite(), RevokeAdmin(),
        GrantPremium(), RevokePremium(), GrantPremiumAll(),
        RecordDelete(), RecordUpdate(),
        ClubVerify(), ClubReject(), ClubRevokeVerify(), ClubEdit(), ClubDelete(),
        ClubSetPresident(), ClubSetRole(), ClubRemoveMember(),
    )
}


def get_action(key: str):
    action = ACTIONS.get(key)
    if action is None:
        raise HTTPException(status_code=404, detail="That admin action does not exist")
    return action


def preview_action(db: Session, actor: User, action: str, target_id: str | None, payload: dict) -> Preview:
    return get_action(action).build(db, actor, target_id, _payload(payload))


def submit_action(db: Session, actor: User, *, action: str, target_id: str | None, payload: dict, reason: str | None, typed: str | None) -> dict:
    spec = get_action(action)
    body = _payload(payload)
    preview = spec.build(db, actor, target_id, body)
    if preview.blocked:
        raise HTTPException(status_code=400, detail=preview.blocked)
    text = (reason or "").strip()
    if len(text) < 8:
        raise HTTPException(status_code=400, detail="Write a reason of at least 8 characters so other admins can see why.")
    if len(text) > 500:
        raise HTTPException(status_code=400, detail="Keep the reason under 500 characters.")
    if not _matches(typed, preview):
        raise HTTPException(status_code=400, detail="The confirmation text does not match. Nothing was changed.")
    if action == "user.suspend":
        body = {**body, "reason": text}
    if preview.severity == "critical" and not _owner_runs_deletion(actor, action):
        target = UUID(preview.target_user_id) if preview.target_user_id else None
        required, _eligible = approval_requirement(db, actor, target)
        proposal = open_proposal(
            db, actor, action=action, target_id=target_id, target_user_id=target, payload=body,
            summary=preview.summary, warning=preview.warning, reason=text, required=required,
        )
        out = proposal_dict(db, proposal, actor.id)
        out["status"] = "pending"
        out["proposal_id"] = out["id"]
        return out
    summary = spec.run(db, actor, target_id, body)
    write_audit(
        db, actor, action=action, summary=summary,
        severity="critical" if preview.severity == "critical" else "important",
        target_type=getattr(spec, "table", None), target_id=target_id, payload={"reason": text},
    )
    db.commit()
    return {"status": "done", "summary": summary}


def cast_vote(db: Session, actor: User, proposal_id, *, decision: str, typed: str | None, reason: str | None) -> dict:
    proposal = lock_proposal(db, proposal_id)
    if proposal.status != "pending":
        raise HTTPException(status_code=400, detail="This request is already finished")
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="Vote to approve or to stop the change")
    expected = "APPROVE" if decision == "approve" else "REJECT"
    if (typed or "").strip() != expected:
        raise HTTPException(status_code=400, detail=f"Type {expected} to confirm. Nothing was changed.")
    if proposal.target_user_id is not None and actor.id == proposal.target_user_id:
        raise HTTPException(status_code=400, detail="You cannot vote on a change that is about your own access.")
    if db.query(AdminProposalVote).filter(AdminProposalVote.proposal_id == proposal.id, AdminProposalVote.user_id == actor.id).first():
        raise HTTPException(status_code=400, detail="You already responded to this request")
    if decision == "reject" and actor.id == proposal.proposer_id:
        raise HTTPException(status_code=400, detail="Cancel your own request instead of voting to stop it")
    note = (reason or "").strip()
    if decision == "reject" and len(note) < 8:
        raise HTTPException(status_code=400, detail="Say why you are stopping this, in at least 8 characters.")
    db.add(AdminProposalVote(
        proposal_id=proposal.id, user_id=actor.id, voter_email=actor.email,
        decision=decision, reason=note[:500] or None,
    ))
    db.flush()
    if decision == "reject":
        proposal.status = "rejected"
        proposal.resolved_at = _now()
        proposal.result = f"Stopped by {actor.email}"
        write_audit(db, actor, action="proposal.rejected", summary=proposal.summary, severity="critical", target_id=str(proposal.id))
        db.commit()
        return proposal_dict(db, proposal, actor.id)
    approvals = db.query(AdminProposalVote).filter(
        AdminProposalVote.proposal_id == proposal.id, AdminProposalVote.decision == "approve",
    ).count()
    if approvals < proposal.required_approvals:
        write_audit(db, actor, action="proposal.approved", summary=proposal.summary, severity="info", target_id=str(proposal.id))
        db.commit()
        db.refresh(proposal)
        return proposal_dict(db, proposal, actor.id)
    spec = get_action(proposal.action)
    if proposal.action == "user.purge":
        # Drop the link before the account row disappears, so the request can still be saved.
        proposal.target_user_id = None
        db.flush()
    summary = spec.run(db, actor, proposal.target_id, proposal.payload or {})
    proposal.status = "executed"
    proposal.resolved_at = _now()
    proposal.result = summary[:2000]
    write_audit(
        db, actor, action=proposal.action, summary=summary, severity="critical",
        target_id=proposal.target_id, payload={"proposal_id": str(proposal.id), "reason": proposal.reason},
    )
    db.commit()
    return proposal_dict(db, proposal, actor.id)


def cancel_proposal(db: Session, actor: User, proposal_id) -> dict:
    proposal = lock_proposal(db, proposal_id)
    if proposal.status != "pending":
        raise HTTPException(status_code=400, detail="This request is already finished")
    if proposal.proposer_id != actor.id:
        raise HTTPException(status_code=403, detail="Only the admin who started this request can cancel it")
    proposal.status = "cancelled"
    proposal.resolved_at = _now()
    proposal.result = "Cancelled before it ran"
    write_audit(db, actor, action="proposal.cancelled", summary=proposal.summary, severity="info", target_id=str(proposal.id))
    db.commit()
    return proposal_dict(db, proposal, actor.id)
