"""Join requests (someone asks the owner) and invitations (the owner asks someone).
Membership (project_collaborators) only starts when the other side accepts."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.errors import Conflict
from app.db.session import get_db
from app.models.collaboration import PERMISSIONS, CollaborationRequest, ProjectCollaborator, ProjectInvitation
from app.models.network import NetworkActivity
from app.models.project import Project
from app.models.user import User
from app.realtime.broadcaster import queue_event
from app.schemas.network import (
    CollabPermissions,
    CollaborationRequestCreate,
    CollaborationRequestResponse,
    InvitationCreate,
    InvitationResponse,
    InviteeResult,
    MyCollabStatus,
    ProjectCollaboratorResponse,
)
from app.services.social import can_view_project, display_name, notify, user_summary

router = APIRouter(prefix="/collaborations", tags=["Collaborations"])
INVITE_TTL = timedelta(days=14)


def _now():
    return datetime.now(timezone.utc)


def _project(db: Session, project_id: UUID, me: Optional[User], owner_only: bool = False) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or not can_view_project(db, project, me.id if me else None):
        raise HTTPException(status_code=404, detail="Project not found")
    if owner_only and project.owner_id != me.id:
        raise HTTPException(status_code=403, detail="Only the project owner can do this")
    return project


def _member(db: Session, project_id: UUID, user_id: UUID) -> Optional[ProjectCollaborator]:
    return db.query(ProjectCollaborator).filter_by(project_id=project_id, user_id=user_id).first()


def _pending_request(db: Session, project_id: UUID, user_id: UUID) -> Optional[CollaborationRequest]:
    return db.query(CollaborationRequest).filter_by(project_id=project_id, requester_id=user_id, status="pending").first()


def _live_invitations(db: Session):
    return db.query(ProjectInvitation).filter(ProjectInvitation.status == "pending", ProjectInvitation.expires_at > _now())


def _live_invitation(db: Session, project_id: UUID, user_id: UUID) -> Optional[ProjectInvitation]:
    return _live_invitations(db).filter_by(project_id=project_id, invitee_id=user_id).first()


def _perms(row) -> CollabPermissions:
    return CollabPermissions(**{k: getattr(row, k) for k in PERMISSIONS})


def _member_view(c: ProjectCollaborator) -> ProjectCollaboratorResponse:
    s = user_summary(c.user)
    return ProjectCollaboratorResponse(id=c.id, project_id=c.project_id, user_id=c.user_id, username=s.username,
                                       full_name=s.full_name, headline=s.headline, avatar_url=s.avatar_url,
                                       role=c.role, created_at=c.created_at, permissions=_perms(c))


def _request_view(r: CollaborationRequest) -> CollaborationRequestResponse:
    return CollaborationRequestResponse(id=r.id, project_id=r.project_id, project_title=r.project.title,
                                        requester_id=r.requester_id, requester=user_summary(r.requester),
                                        project_owner_id=r.project_owner_id, message=r.message, role=r.role,
                                        status=r.status, created_at=r.created_at)


def _invitation_view(i: ProjectInvitation) -> InvitationResponse:
    return InvitationResponse(id=i.id, project_id=i.project_id, project_title=i.project.title,
                              inviter=user_summary(i.inviter), invitee=user_summary(i.invitee), role=i.role,
                              message=i.message, permissions=_perms(i), status=i.status,
                              created_at=i.created_at, expires_at=i.expires_at)


def _changed(db: Session, project_id: UUID, *user_ids: UUID) -> None:
    for uid in set(user_ids):
        queue_event(db, uid, {"type": "collab.changed", "project_id": str(project_id)})


def _join(db: Session, project: Project, user: User, role: str, perms: CollabPermissions, owner: User) -> ProjectCollaborator:
    collab = _member(db, project.id, user.id)
    if collab is None:
        collab = ProjectCollaborator(project_id=project.id, user_id=user.id, role=role or "Collaborator", **perms.model_dump())
        db.add(collab)
        db.add(NetworkActivity(user_id=user.id, activity_type="collaboration", project_id=project.id, is_demo=False,
                               title=f"{display_name(user)} joined as {collab.role}"[:255],
                               content=f"Excited to start collaborating with @{owner.username} on {project.title}!"))
    return collab


def _commit(db: Session, code: str, detail: str) -> None:
    """The partial unique indexes catch a double click that slipped past the checks above."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict(code, detail)


# ---------------- team and my status ----------------
@router.get("/projects/{project_id}/collaborators", response_model=List[ProjectCollaboratorResponse])
def get_project_collaborators(project_id: UUID, db: Session = Depends(get_db),
                              me: Optional[User] = Depends(get_current_user_optional)):
    project = _project(db, project_id, me)
    owner = user_summary(project.owner)
    lead = ProjectCollaboratorResponse(id=project.id, project_id=project.id, user_id=project.owner_id, username=owner.username,
                                       full_name=owner.full_name, headline=owner.headline, avatar_url=owner.avatar_url,
                                       role="Project Creator / Lead", created_at=project.created_at, is_owner=True,
                                       permissions=CollabPermissions(**{k: True for k in PERMISSIONS}))
    members = db.query(ProjectCollaborator).filter_by(project_id=project_id).order_by(ProjectCollaborator.created_at).all()
    return [lead] + [_member_view(c) for c in members]


@router.get("/projects/{project_id}/me", response_model=MyCollabStatus)
def my_collaboration_status(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    invitation = _live_invitation(db, project_id, me.id)
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or not (invitation or can_view_project(db, project, me.id)):
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id == me.id:
        return MyCollabStatus(status="owner", permissions=CollabPermissions(**{k: True for k in PERMISSIONS}))
    if member := _member(db, project_id, me.id):
        return MyCollabStatus(status="member", permissions=_perms(member))
    if invitation:
        return MyCollabStatus(status="invited", invitation_id=invitation.id, permissions=_perms(invitation))
    if request := _pending_request(db, project_id, me.id):
        return MyCollabStatus(status="requested", request_id=request.id)
    return MyCollabStatus(status="none")


# ---------------- join requests ----------------
@router.get("/projects/{project_id}/requests", response_model=List[CollaborationRequestResponse])
def get_project_collaboration_requests(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    _project(db, project_id, me, owner_only=True)
    rows = db.query(CollaborationRequest).filter_by(project_id=project_id, status="pending").order_by(CollaborationRequest.created_at).all()
    return [_request_view(r) for r in rows]


@router.post("/projects/{project_id}/requests", response_model=CollaborationRequestResponse)
def submit_collaboration_request(project_id: UUID, data: CollaborationRequestCreate, db: Session = Depends(get_db),
                                 me: User = Depends(get_current_user)):
    project = _project(db, project_id, me)
    if project.owner_id == me.id:
        raise HTTPException(status_code=400, detail="Cannot request collaboration on your own project")
    if _member(db, project_id, me.id):
        raise Conflict("already_member", "You are already a collaborator on this project")
    if invitation := _live_invitation(db, project_id, me.id):
        raise Conflict("invitation_pending", "The owner already invited you. Accept the invitation instead.",
                       invitation_id=invitation.id)
    if _pending_request(db, project_id, me.id):
        raise Conflict("request_pending", "Collaboration request already pending")

    req = CollaborationRequest(project_id=project_id, requester_id=me.id, project_owner_id=project.owner_id,
                               message=data.message, role=data.role, status="pending")
    db.add(req)
    notify(db, project.owner_id, me, "collaboration_request", str(project.id),
           f"{display_name(me)} requested to collaborate on \"{project.title}\" as {data.role}.")
    _commit(db, "request_pending", "Collaboration request already pending")
    db.refresh(req)
    return _request_view(req)


@router.get("/requests/mine", response_model=List[CollaborationRequestResponse])
def my_pending_requests(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    rows = db.query(CollaborationRequest).filter_by(requester_id=me.id, status="pending").order_by(CollaborationRequest.created_at.desc()).all()
    return [_request_view(r) for r in rows]


def _owners_pending_request(db: Session, request_id: UUID, me: User) -> CollaborationRequest:
    req = db.query(CollaborationRequest).filter(CollaborationRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Collaboration request not found")
    if req.project_owner_id != me.id:
        raise HTTPException(status_code=403, detail="Only the project owner can answer collaboration requests")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="This request is no longer pending")
    return req


@router.put("/requests/{request_id}/accept", response_model=ProjectCollaboratorResponse)
def accept_collaboration_request(request_id: UUID, permissions: Optional[CollabPermissions] = Body(None),
                                 db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    req = _owners_pending_request(db, request_id, me)
    req.status = "accepted"
    collab = _join(db, req.project, req.requester, req.role, permissions or CollabPermissions(), me)
    notify(db, req.requester_id, me, "collaboration_accepted", str(req.project_id),
           f"{display_name(me)} accepted your collaboration request on \"{req.project.title}\"!")
    db.commit()
    db.refresh(collab)
    return _member_view(collab)


@router.put("/requests/{request_id}/decline")
def decline_collaboration_request(request_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    req = _owners_pending_request(db, request_id, me)
    req.status = "declined"
    _changed(db, req.project_id, req.requester_id)
    db.commit()
    return {"message": "Collaboration request declined"}


@router.delete("/requests/{request_id}")
def withdraw_collaboration_request(request_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    req = db.query(CollaborationRequest).filter_by(id=request_id, requester_id=me.id, status="pending").first()
    if not req:
        raise HTTPException(status_code=404, detail="No pending request to withdraw")
    req.status = "withdrawn"
    _changed(db, req.project_id, req.project_owner_id)
    db.commit()
    return {"message": "Collaboration request withdrawn"}


# ---------------- invitations ----------------
@router.get("/projects/{project_id}/invitee-search", response_model=List[InviteeResult])
def search_invitees(project_id: UUID, q: str = Query(..., min_length=1, max_length=30), db: Session = Depends(get_db),
                    me: User = Depends(get_current_user)):
    project = _project(db, project_id, me, owner_only=True)
    users = db.query(User).filter(func.lower(User.username).startswith(q.strip().lower(), autoescape=True),
                                  User.id != project.owner_id).order_by(User.username).limit(10).all()
    ids = [u.id for u in users]
    members = {r[0] for r in db.query(ProjectCollaborator.user_id).filter(ProjectCollaborator.project_id == project_id, ProjectCollaborator.user_id.in_(ids))}
    invited = {r[0] for r in _live_invitations(db).with_entities(ProjectInvitation.invitee_id).filter(ProjectInvitation.project_id == project_id)}
    requested = {r[0] for r in db.query(CollaborationRequest.requester_id).filter_by(project_id=project_id, status="pending")}

    def status(uid):
        return "member" if uid in members else "invited" if uid in invited else "requested" if uid in requested else "none"
    return [InviteeResult(**user_summary(u).model_dump(), status=status(u.id)) for u in users]


@router.post("/projects/{project_id}/invitations", response_model=InvitationResponse)
def invite_collaborator(project_id: UUID, data: InvitationCreate, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    project = _project(db, project_id, me, owner_only=True)
    invitee = db.query(User).filter(func.lower(User.username) == data.username.strip().lower()).first()
    if not invitee:
        raise HTTPException(status_code=404, detail="No developer with that username")
    if invitee.id == me.id:
        raise HTTPException(status_code=400, detail="You cannot invite yourself")
    if _member(db, project_id, invitee.id):
        raise Conflict("already_member", "This developer is already a collaborator")
    if request := _pending_request(db, project_id, invitee.id):
        raise Conflict("request_pending", "This developer already asked to join. Accept their request instead.",
                       request_id=request.id)
    if _live_invitation(db, project_id, invitee.id):
        raise Conflict("invitation_pending", "This developer already has a pending invitation")

    db.query(ProjectInvitation).filter(ProjectInvitation.project_id == project_id, ProjectInvitation.invitee_id == invitee.id,
                                       ProjectInvitation.status == "pending").update({"status": "expired"})
    inv = ProjectInvitation(project_id=project_id, inviter_id=me.id, invitee_id=invitee.id, role=data.role,
                            message=data.message, expires_at=_now() + INVITE_TTL, **data.permissions.model_dump())
    db.add(inv)
    notify(db, invitee.id, me, "collaboration_invitation", str(project_id),
           f"{display_name(me)} invited you to collaborate on \"{project.title}\" as {data.role}.")
    _commit(db, "invitation_pending", "This developer already has a pending invitation")
    db.refresh(inv)
    return _invitation_view(inv)


@router.get("/projects/{project_id}/invitations", response_model=List[InvitationResponse])
def project_invitations(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    _project(db, project_id, me, owner_only=True)
    return [_invitation_view(i) for i in _live_invitations(db).filter_by(project_id=project_id).order_by(ProjectInvitation.created_at)]


@router.get("/invitations/mine", response_model=List[InvitationResponse])
def my_invitations(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    return [_invitation_view(i) for i in _live_invitations(db).filter_by(invitee_id=me.id).order_by(ProjectInvitation.created_at.desc())]


def _answer(db: Session, invitation_id: UUID, me: User, status: str) -> ProjectInvitation:
    inv = db.query(ProjectInvitation).filter(ProjectInvitation.id == invitation_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation not found")
    is_owner = inv.project.owner_id == me.id
    if (status == "revoked") != is_owner or (not is_owner and inv.invitee_id != me.id):
        raise HTTPException(status_code=403, detail="This is not your invitation to answer")
    if inv.status != "pending" or inv.expires_at <= _now():
        raise HTTPException(status_code=400, detail="This invitation is no longer valid")
    inv.status, inv.responded_at = status, _now()
    return inv


@router.put("/invitations/{invitation_id}/accept", response_model=ProjectCollaboratorResponse)
def accept_invitation(invitation_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    inv = _answer(db, invitation_id, me, "accepted")
    collab = _join(db, inv.project, me, inv.role, _perms(inv), inv.project.owner)
    notify(db, inv.project.owner_id, me, "collaboration_joined", str(inv.project_id),
           f"{display_name(me)} accepted your invitation to \"{inv.project.title}\".")
    db.commit()
    db.refresh(collab)
    return _member_view(collab)


@router.put("/invitations/{invitation_id}/decline")
def decline_invitation(invitation_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    inv = _answer(db, invitation_id, me, "declined")
    _changed(db, inv.project_id, inv.project.owner_id)
    db.commit()
    return {"message": "Invitation declined"}


@router.delete("/invitations/{invitation_id}")
def revoke_invitation(invitation_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    inv = _answer(db, invitation_id, me, "revoked")
    _changed(db, inv.project_id, inv.invitee_id)
    db.commit()
    return {"message": "Invitation revoked"}


# ---------------- members ----------------
@router.delete("/projects/{project_id}/members/me")
def leave_project(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    project = _project(db, project_id, me)
    member = _member(db, project_id, me.id)
    if not member:
        raise HTTPException(status_code=400 if project.owner_id == me.id else 404,
                            detail="Owners cannot leave their own project" if project.owner_id == me.id else "You are not a member")
    db.delete(member)
    notify(db, project.owner_id, me, "collaboration_left", str(project_id), f"{display_name(me)} left \"{project.title}\".")
    db.commit()
    return {"message": "You left the project"}


def _owners_member(db: Session, project_id: UUID, user_id: UUID, me: User) -> tuple[Project, ProjectCollaborator]:
    project = _project(db, project_id, me, owner_only=True)
    member = _member(db, project_id, user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    return project, member


@router.delete("/projects/{project_id}/members/{user_id}")
def remove_member(project_id: UUID, user_id: UUID, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    project, member = _owners_member(db, project_id, user_id, me)
    db.delete(member)
    notify(db, user_id, me, "collaboration_removed", str(project_id),
           f"{display_name(me)} removed you from \"{project.title}\".")
    db.commit()
    return {"message": "Member removed"}


@router.patch("/projects/{project_id}/members/{user_id}", response_model=ProjectCollaboratorResponse)
def update_member_permissions(project_id: UUID, user_id: UUID, permissions: CollabPermissions,
                              db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    _, member = _owners_member(db, project_id, user_id, me)
    for key, value in permissions.model_dump().items():
        setattr(member, key, value)
    _changed(db, project_id, user_id)
    db.commit()
    db.refresh(member)
    return _member_view(member)
