"""Admin area. Every route checks the admin list again; the navbar flag is only a hint."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.admin import AdminAuditEvent, AdminInvite, AdminProposal, PlatformAdmin
from app.models.profile import UserProfile
from app.models.user import User
from app.services.admin_actions import cancel_proposal, cast_vote, preview_action, submit_action
from app.services.admin_catalog import get_row, groups_payload, list_rows, sql_table
from app.services.usage import summarize
from app.services.platform_admin import (
    OWNER_EMAIL,
    TEAM_EMAILS,
    AdminContext,
    claim_reserved_admin,
    proposal_dict,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


def require_admin(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AdminContext:
    role = claim_reserved_admin(db, user)
    if not role:
        raise HTTPException(status_code=403, detail="You do not have access to the admin area")
    return AdminContext(user=user, role=role)


class ActionBody(BaseModel):
    action: str = Field(min_length=1, max_length=64)
    target_id: str | None = Field(default=None, max_length=80)
    payload: dict = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=500)
    typed: str | None = Field(default=None, max_length=300)


class VoteBody(BaseModel):
    decision: str = Field(min_length=1, max_length=16)
    typed: str = Field(min_length=1, max_length=16)
    reason: str | None = Field(default=None, max_length=500)


def _count(db: Session, table: str, column: str, value) -> int:
    sql = sql_table(table)
    return int(db.execute(select(func.count()).select_from(sql).where(sql.c[column] == value)).scalar_one())


def _status(user: User) -> str:
    if user.suspended_at is not None:
        return "suspended"
    if user.delete_after is not None:
        return "deletion scheduled"
    if user.deactivated_at is not None:
        return "paused"
    return "active"


def _person(user: User, profile: UserProfile | None, admin: PlatformAdmin | None) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "full_name": None if profile is None else profile.full_name,
        "account_type": user.account_type,
        "status": _status(user),
        "admin_role": None if admin is None else admin.role,
        "has_password": user.password_hash is not None,
        "github_linked": user.github_id is not None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "deactivated_at": user.deactivated_at.isoformat() if user.deactivated_at else None,
        "delete_after": user.delete_after.isoformat() if user.delete_after else None,
        "suspended_at": user.suspended_at.isoformat() if user.suspended_at else None,
        "suspended_reason": user.suspended_reason,
    }


@router.get("/session")
def session(ctx: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    open_proposals = db.query(AdminProposal).filter(AdminProposal.status == "pending").count()
    return {
        "role": ctx.role,
        "email": ctx.user.email,
        "user_id": str(ctx.user.id),
        "admin_count": db.query(PlatformAdmin).count(),
        "pending_invites": db.query(AdminInvite).count(),
        "open_proposals": open_proposals,
        "owner_email": OWNER_EMAIL,
    }


@router.get("/catalog")
def catalog(ctx: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    users = sql_table("users")
    projects = sql_table("projects")
    return {
        "groups": groups_payload(db),
        "headline": {
            "people": int(db.execute(select(func.count()).select_from(users)).scalar_one()),
            "developers": int(db.execute(select(func.count()).select_from(users).where(users.c.account_type == "developer")).scalar_one()),
            "recruiters": int(db.execute(select(func.count()).select_from(users).where(users.c.account_type == "recruiter")).scalar_one()),
            "suspended": int(db.execute(select(func.count()).select_from(users).where(users.c.suspended_at.is_not(None))).scalar_one()),
            "paused": int(db.execute(select(func.count()).select_from(users).where(
                users.c.deactivated_at.is_not(None), users.c.suspended_at.is_(None), users.c.delete_after.is_(None),
            )).scalar_one()),
            "projects": int(db.execute(select(func.count()).select_from(projects)).scalar_one()),
            "open_proposals": db.query(AdminProposal).filter(AdminProposal.status == "pending").count(),
        },
    }


@router.get("/people")
def people(
    q: str = "",
    offset: int = 0,
    limit: int = 25,
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    limit = min(max(limit, 1), 50)
    offset = max(offset, 0)
    query = db.query(User, UserProfile, PlatformAdmin).outerjoin(UserProfile, UserProfile.user_id == User.id).outerjoin(
        PlatformAdmin, PlatformAdmin.user_id == User.id
    )
    text = q.strip()[:80]
    if text:
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.filter(or_(
            User.email.ilike(pattern, escape="\\"),
            User.username.ilike(pattern, escape="\\"),
            UserProfile.full_name.ilike(pattern, escape="\\"),
        ))
    total = query.count()
    rows = query.order_by(User.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "people": [_person(user, profile, admin) for user, profile, admin in rows],
    }


@router.get("/people/{user_id}")
def person(user_id: UUID, ctx: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="That account is no longer there")
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one_or_none()
    admin = db.query(PlatformAdmin).filter(PlatformAdmin.user_id == user.id).one_or_none()
    account = get_row(db, "users", [f"id:{user.id}"])
    profile_row = None
    if profile is not None:
        profile_row = get_row(db, "profiles", [f"id:{profile.id}"])["row"]
    return {
        "person": _person(user, profile, admin),
        "account": account["row"],
        "profile": profile_row,
        "counts": {
            "projects": _count(db, "projects", "owner_id", user.id),
            "posts": _count(db, "network_activities", "user_id", user.id),
            "messages_sent": _count(db, "messages", "sender_id", user.id),
            "messages_received": _count(db, "messages", "receiver_id", user.id),
            "connections": _count(db, "connections", "requester_id", user.id) + _count(db, "connections", "receiver_id", user.id),
            "challenges": _count(db, "technical_challenges", "recruiter_id", user.id),
            "wraps": _count(db, "monthly_wraps", "user_id", user.id),
        },
    }


@router.get("/tables/{name}")
def table_rows(
    name: str,
    q: str = "",
    offset: int = 0,
    limit: int = 25,
    eq: list[str] = Query(default_factory=list),
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return list_rows(db, name, q=q, eqs=eq, offset=offset, limit=limit)


@router.get("/tables/{name}/row")
def table_row(
    name: str,
    pk: list[str] = Query(default_factory=list),
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return get_row(db, name, pk)


@router.post("/actions/preview")
def preview(body: ActionBody, ctx: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return preview_action(db, ctx.user, body.action, body.target_id, body.payload).public()


@router.post("/actions")
def act(body: ActionBody, ctx: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return submit_action(
        db, ctx.user, action=body.action, target_id=body.target_id,
        payload=body.payload, reason=body.reason, typed=body.typed,
    )


@router.get("/proposals")
def proposals(
    status: str = "pending",
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(AdminProposal)
    if status != "all":
        query = query.filter(AdminProposal.status == status)
    rows = query.order_by(AdminProposal.created_at.desc()).limit(50).all()
    return {"proposals": [proposal_dict(db, row, ctx.user.id) for row in rows]}


@router.post("/proposals/{proposal_id}/vote")
def vote(proposal_id: UUID, body: VoteBody, ctx: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return cast_vote(db, ctx.user, proposal_id, decision=body.decision, typed=body.typed, reason=body.reason)


@router.post("/proposals/{proposal_id}/cancel")
def cancel(proposal_id: UUID, ctx: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return cancel_proposal(db, ctx.user, proposal_id)


@router.get("/audit")
def audit(
    offset: int = 0,
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    offset = max(offset, 0)
    total = db.query(AdminAuditEvent).count()
    rows = db.query(AdminAuditEvent).order_by(AdminAuditEvent.created_at.desc()).offset(offset).limit(40).all()
    return {
        "total": total,
        "offset": offset,
        "events": [
            {
                "id": str(row.id),
                "action": row.action,
                "summary": row.summary,
                "severity": row.severity,
                "actor_email": row.actor_email,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


@router.get("/insights")
def insights(
    days: int = 30,
    audience: str = "all",
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return summarize(db, days, audience)


@router.get("/access")
def access(ctx: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (
        db.query(PlatformAdmin, User)
        .join(User, User.id == PlatformAdmin.user_id)
        .order_by(PlatformAdmin.role.desc(), User.email)
        .all()
    )
    invites = db.query(AdminInvite).order_by(AdminInvite.email).all()
    return {
        "admins": [
            {
                "user_id": str(user.id),
                "email": user.email,
                "username": user.username,
                "role": admin.role,
                "created_at": admin.created_at.isoformat() if admin.created_at else None,
            }
            for admin, user in rows
        ],
        "invites": [
            {"email": invite.email, "role": invite.role, "created_at": invite.created_at.isoformat() if invite.created_at else None}
            for invite in invites
        ],
        "owner_email": OWNER_EMAIL,
        "team_emails": list(TEAM_EMAILS),
        "you": ctx.user.email,
        "your_role": ctx.role,
    }


@router.get("/project-intelligence")
def get_project_intelligence_settings(ctx: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    """Admin-tunable Project Intelligence limits, switches, and spend overview."""
    from app.core.feature_flags import project_intelligence_enabled
    from app.services.project_intelligence.quota import admin_usage_overview, get_pi_settings

    overview = admin_usage_overview(db)
    return {
        "feature_flag_enabled": project_intelligence_enabled(),
        "settings": get_pi_settings(db),
        "overview": overview,
    }


@router.patch("/project-intelligence")
def patch_project_intelligence_settings(
    body: dict,
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models.admin import AdminAuditEvent
    from app.services.project_intelligence.quota import set_pi_settings

    patch = body.get("settings") if isinstance(body.get("settings"), dict) else body
    settings = set_pi_settings(db, patch or {}, updated_by=ctx.user.id)
    db.add(
        AdminAuditEvent(
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            action="pi_settings_update",
            summary="Updated Project Intelligence settings",
            severity="info",
            target_type="platform_settings",
            target_id="project_intelligence",
            payload={"settings": settings},
        )
    )
    db.commit()
    return {"settings": settings}


@router.post("/project-intelligence/premium")
def grant_pi_premium(
    body: dict,
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Assign or revoke Project Intelligence Premium for a user (no payments)."""
    from app.models.admin import AdminAuditEvent
    from app.models.user import User
    from app.services import entitlements as ent

    user_id = body.get("user_id")
    email = body.get("email")
    revoke = bool(body.get("revoke"))
    days = body.get("days")
    user = None
    if user_id:
        user = db.query(User).filter(User.id == user_id).first()
    elif email:
        user = db.query(User).filter(User.email == str(email).strip().lower()).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if revoke:
        ent.revoke(db, user.id, "project_intelligence")
        action = "pi_premium_revoke"
    else:
        ent.grant(
            db,
            user.id,
            "project_intelligence",
            source="admin_grant",
            granted_by=ctx.user.id,
            days=int(days) if days is not None else None,
        )
        action = "pi_premium_grant"
    db.add(
        AdminAuditEvent(
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            action=action,
            summary=("Revoked" if revoke else "Granted") + f" PI Premium for {user.email}",
            severity="info",
            target_type="user",
            target_id=str(user.id),
            payload={"email": user.email, "days": days, "revoke": revoke},
        )
    )
    db.commit()
    return {"ok": True, "user_id": str(user.id), "email": user.email, "premium": not revoke}


@router.get("/project-intelligence/users")
def list_pi_ai_users(
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List users with Project Intelligence AI usage (month + lifetime spend)."""
    from app.services.project_intelligence.admin_ops import list_ai_users

    return {"users": list_ai_users(db, q=q, limit=limit)}


@router.get("/project-intelligence/users/{user_id}")
def get_pi_ai_user_detail(
    user_id: UUID,
    ledger_limit: int = Query(default=100, ge=1, le=500),
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Full AI usage detail for one user: months, ledger, generations, visitor requests, projects."""
    from app.services.project_intelligence.admin_ops import user_ai_detail

    try:
        return user_ai_detail(db, user_id, ledger_limit=ledger_limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/project-intelligence/projects/{project_id}/regenerate")
def admin_regenerate_project_summary(
    project_id: UUID,
    body: dict | None = None,
    ctx: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Force-regenerate a project AI summary. Does not burn the owner's monthly allowance; cost is logged."""
    from app.models.admin import AdminAuditEvent
    from app.services.project_intelligence.admin_ops import admin_regenerate_project

    payload = body or {}
    publish = payload.get("publish", True)
    include_detailed = bool(payload.get("include_detailed", False))
    try:
        result = admin_regenerate_project(
            db,
            project_id=project_id,
            admin_user=ctx.user,
            publish=bool(publish),
            include_detailed=include_detailed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.add(
        AdminAuditEvent(
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            action="pi_admin_regenerate",
            summary=f"Regenerated AI summary for {result.get('project_title') or project_id}",
            severity="info",
            target_type="project",
            target_id=str(project_id),
            payload={
                "draft_id": result.get("draft_id"),
                "job_id": result.get("job_id"),
                "published": result.get("published"),
                "quality_status": result.get("quality_status"),
                "include_detailed": include_detailed,
            },
        )
    )
    db.commit()
    return result

