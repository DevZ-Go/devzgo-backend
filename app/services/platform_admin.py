"""Who may open the admin area, and the record of changes that need more than one admin."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.admin import AdminAuditEvent, AdminInvite, AdminProposal, AdminProposalVote, PlatformAdmin
from app.models.user import User

# Recovery account. This address is an owner whenever it has a user, and that role cannot be taken away.
OWNER_EMAIL = "idevzgo.ca@gmail.com"
# Invited once by the migration. Removing the invite, or removing the admin row, is permanent.
TEAM_EMAILS = (
    "singhchangaganjeet@gmail.com",
    "rajputmanasi2002@gmail.com",
    "rajputjanasi@gmail.com",
    "munnirani855@gmail.com",
)


@dataclass
class AdminContext:
    user: User
    role: str

    @property
    def owner(self) -> bool:
        return self.role == "owner"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_owner_email(email: str | None) -> bool:
    return (email or "").strip().lower() == OWNER_EMAIL


def claim_reserved_admin(db: Session, user: User) -> str | None:
    """Attach a reserved invite, and keep the owner email as owner. Returns the role, or None."""
    email = (user.email or "").strip().lower()
    row = db.query(PlatformAdmin).filter(PlatformAdmin.user_id == user.id).one_or_none()
    invite = db.query(AdminInvite).filter(AdminInvite.email == email).one_or_none()
    if row is not None:
        changed = False
        if is_owner_email(email) and row.role != "owner":
            row.role = "owner"
            changed = True
        if invite is not None:
            db.delete(invite)
            changed = True
        if changed:
            db.commit()
        return row.role
    if not is_owner_email(email) and invite is None:
        return None
    db.add(PlatformAdmin(
        user_id=user.id,
        role="owner" if is_owner_email(email) else "admin",
        granted_by=None if invite is None else invite.granted_by,
    ))
    if invite is not None:
        db.delete(invite)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.query(PlatformAdmin).filter(PlatformAdmin.user_id == user.id).one_or_none()
        return row.role if row is not None else None
    return "owner" if is_owner_email(email) else "admin"


def admin_ids(db: Session) -> list[UUID]:
    return [row[0] for row in db.query(PlatformAdmin.user_id).all()]


def approval_requirement(db: Session, actor: User, target_user_id: UUID | None) -> tuple[int, int]:
    """How many approvals a dangerous change needs, and how many admins are allowed to vote.

    The person a removal is about cannot vote. Three approvals are required when three or more
    admins can vote, and two when only two can. One admin is not enough.
    """
    eligible = [user_id for user_id in admin_ids(db) if user_id != target_user_id]
    if actor.id not in eligible:
        raise HTTPException(status_code=400, detail="You cannot approve a change that is about your own access.")
    if len(eligible) < 2:
        raise HTTPException(
            status_code=400,
            detail="This needs at least two admins. Grant admin access to another person, then ask them to approve it.",
        )
    return (3 if len(eligible) >= 3 else 2), len(eligible)


def write_audit(
    db: Session,
    actor: User | None,
    *,
    action: str,
    summary: str,
    severity: str,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict | None = None,
) -> None:
    db.add(AdminAuditEvent(
        actor_id=None if actor is None else actor.id,
        actor_email=None if actor is None else actor.email,
        action=action[:64],
        summary=summary[:300],
        severity=severity,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id)[:80],
        payload=payload or {},
    ))


def open_proposal(
    db: Session,
    actor: User,
    *,
    action: str,
    target_id: str | None,
    target_user_id: UUID | None,
    payload: dict,
    summary: str,
    warning: str,
    reason: str,
    required: int,
) -> AdminProposal:
    proposal = AdminProposal(
        action=action,
        target_id=None if target_id is None else str(target_id)[:80],
        target_user_id=target_user_id,
        payload=payload,
        summary=summary[:300],
        warning=warning,
        reason=reason.strip(),
        required_approvals=required,
        status="pending",
        proposer_id=actor.id,
        proposer_email=actor.email,
    )
    db.add(proposal)
    db.flush()
    db.add(AdminProposalVote(
        proposal_id=proposal.id,
        user_id=actor.id,
        voter_email=actor.email,
        decision="approve",
        reason=reason.strip()[:500],
    ))
    write_audit(
        db, actor, action="proposal.opened", summary=summary, severity="critical",
        target_type=action, target_id=target_id, payload={"required_approvals": required},
    )
    db.commit()
    db.refresh(proposal)
    return proposal


def proposal_votes(db: Session, proposal_id: UUID) -> list[AdminProposalVote]:
    return db.query(AdminProposalVote).filter(AdminProposalVote.proposal_id == proposal_id).order_by(AdminProposalVote.created_at).all()


def proposal_dict(db: Session, proposal: AdminProposal, viewer_id: UUID | None = None) -> dict:
    votes = proposal_votes(db, proposal.id)
    approvals = [vote for vote in votes if vote.decision == "approve"]
    mine = next((vote.decision for vote in votes if vote.user_id == viewer_id), None)
    return {
        "id": str(proposal.id),
        "action": proposal.action,
        "target_id": proposal.target_id,
        "summary": proposal.summary,
        "warning": proposal.warning,
        "reason": proposal.reason,
        "status": proposal.status,
        "required_approvals": proposal.required_approvals,
        "approvals": len(approvals),
        "approvers": [vote.voter_email for vote in approvals],
        "proposer_email": proposal.proposer_email,
        "my_vote": mine,
        "result": proposal.result,
        "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
        "resolved_at": proposal.resolved_at.isoformat() if proposal.resolved_at else None,
    }


def lock_proposal(db: Session, proposal_id: UUID) -> AdminProposal:
    try:
        parsed = proposal_id if isinstance(proposal_id, UUID) else UUID(str(proposal_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="That request does not exist")
    proposal = db.query(AdminProposal).filter(AdminProposal.id == parsed).with_for_update().one_or_none()
    if proposal is None:
        raise HTTPException(status_code=404, detail="That request does not exist")
    return proposal
