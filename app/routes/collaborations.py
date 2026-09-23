from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.project import Project
from app.models.collaboration import CollaborationRequest, ProjectCollaborator
from app.models.notification import Notification
from app.models.network import NetworkActivity
from app.core.dependencies import get_current_user, get_current_user_optional
from app.schemas.network import (
    CollaborationRequestCreate,
    CollaborationRequestResponse,
    ProjectCollaboratorResponse,
    ConnectionUserSummary,
)

router = APIRouter(prefix="/collaborations", tags=["Collaborations"])

def _user_summary(user: User) -> ConnectionUserSummary:
    name = user.profile.full_name if user.profile and user.profile.full_name else user.username
    headline = user.profile.headline if user.profile else None
    avatar = user.profile.avatar_url if user.profile else None
    return ConnectionUserSummary(
        id=user.id,
        username=user.username,
        full_name=name,
        headline=headline,
        avatar_url=avatar,
    )

@router.get("/projects/{project_id}/collaborators", response_model=List[ProjectCollaboratorResponse])
def get_project_collaborators(
    project_id: UUID,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    collaborators = db.query(ProjectCollaborator).filter(
        ProjectCollaborator.project_id == project_id
    ).all()

    results = []
    # Include owner as Lead Collaborator if not explicitly in collaborators
    owner_in_list = any(c.user_id == project.owner_id for c in collaborators)
    if not owner_in_list and project.owner:
        owner_name = project.owner.profile.full_name if project.owner.profile and project.owner.profile.full_name else project.owner.username
        owner_headline = project.owner.profile.headline if project.owner.profile else None
        owner_avatar = project.owner.profile.avatar_url if project.owner.profile else None
        results.append(
            ProjectCollaboratorResponse(
                id=project.id,  # representative ID
                project_id=project.id,
                user_id=project.owner.id,
                username=project.owner.username,
                full_name=owner_name,
                headline=owner_headline,
                avatar_url=owner_avatar,
                role="Project Creator / Lead",
                created_at=project.created_at,
            )
        )

    for c in collaborators:
        u = c.user
        name = u.profile.full_name if u.profile and u.profile.full_name else u.username
        headline = u.profile.headline if u.profile else None
        avatar = u.profile.avatar_url if u.profile else None
        results.append(
            ProjectCollaboratorResponse(
                id=c.id,
                project_id=c.project_id,
                user_id=c.user_id,
                username=u.username,
                full_name=name,
                headline=headline,
                avatar_url=avatar,
                role=c.role,
                created_at=c.created_at,
            )
        )
    return results

@router.get("/projects/{project_id}/requests", response_model=List[CollaborationRequestResponse])
def get_project_collaboration_requests(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only project owners can view collaboration requests")

    requests = db.query(CollaborationRequest).filter(
        CollaborationRequest.project_id == project_id,
        CollaborationRequest.status == "pending",
    ).all()

    return [
        CollaborationRequestResponse(
            id=r.id,
            project_id=r.project_id,
            project_title=project.title,
            requester_id=r.requester_id,
            requester=_user_summary(r.requester),
            project_owner_id=r.project_owner_id,
            message=r.message,
            role=r.role,
            status=r.status,
            created_at=r.created_at,
        )
        for r in requests
    ]

@router.post("/projects/{project_id}/requests", response_model=CollaborationRequestResponse)
def submit_collaboration_request(
    project_id: UUID,
    request_data: CollaborationRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.owner_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot request collaboration on your own project")

    # Check if already a collaborator
    is_collab = db.query(ProjectCollaborator).filter(
        ProjectCollaborator.project_id == project_id,
        ProjectCollaborator.user_id == current_user.id,
    ).first()
    if is_collab:
        raise HTTPException(status_code=400, detail="You are already a collaborator on this project")

    # Check if request already pending
    existing = db.query(CollaborationRequest).filter(
        CollaborationRequest.project_id == project_id,
        CollaborationRequest.requester_id == current_user.id,
        CollaborationRequest.status == "pending",
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Collaboration request already pending")

    new_request = CollaborationRequest(
        project_id=project_id,
        requester_id=current_user.id,
        project_owner_id=project.owner_id,
        message=request_data.message,
        role=request_data.role,
        status="pending",
    )
    db.add(new_request)

    # Notify project owner
    requester_name = current_user.profile.full_name if current_user.profile and current_user.profile.full_name else current_user.username
    notif = Notification(
        recipient_id=project.owner_id,
        actor_id=current_user.id,
        type="collaboration_request",
        related_id=str(project.id),
        message=f"{requester_name} requested to collaborate on \"{project.title}\" as {request_data.role}.",
    )
    db.add(notif)

    db.commit()
    db.refresh(new_request)

    return CollaborationRequestResponse(
        id=new_request.id,
        project_id=new_request.project_id,
        project_title=project.title,
        requester_id=new_request.requester_id,
        requester=_user_summary(current_user),
        project_owner_id=new_request.project_owner_id,
        message=new_request.message,
        role=new_request.role,
        status=new_request.status,
        created_at=new_request.created_at,
    )

@router.put("/requests/{request_id}/accept", response_model=ProjectCollaboratorResponse)
def accept_collaboration_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    req = db.query(CollaborationRequest).filter(CollaborationRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Collaboration request not found")

    if req.project_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only project owner can accept collaboration requests")

    req.status = "accepted"

    # Add as ProjectCollaborator
    existing_collab = db.query(ProjectCollaborator).filter(
        ProjectCollaborator.project_id == req.project_id,
        ProjectCollaborator.user_id == req.requester_id,
    ).first()

    if not existing_collab:
        collab = ProjectCollaborator(
            project_id=req.project_id,
            user_id=req.requester_id,
            role=req.role or "Collaborator",
        )
        db.add(collab)
    else:
        collab = existing_collab

    # Notify requester
    owner_name = current_user.profile.full_name if current_user.profile and current_user.profile.full_name else current_user.username
    notif = Notification(
        recipient_id=req.requester_id,
        actor_id=current_user.id,
        type="collaboration_accepted",
        related_id=str(req.project_id),
        message=f"{owner_name} accepted your collaboration request on \"{req.project.title}\"!",
    )
    db.add(notif)

    # Post collaboration announcement to feed
    requester = req.requester
    req_name = requester.profile.full_name if requester.profile and requester.profile.full_name else requester.username
    collab_activity = NetworkActivity(
        user_id=req.requester_id,
        activity_type="collaboration",
        project_id=req.project_id,
        title=f"{req_name} joined as {collab.role}",
        content=f"Excited to start collaborating with @{current_user.username} on {req.project.title}!",
        is_demo=False,
    )
    db.add(collab_activity)

    db.commit()
    db.refresh(collab)

    u = collab.user
    name = u.profile.full_name if u.profile and u.profile.full_name else u.username
    headline = u.profile.headline if u.profile else None
    avatar = u.profile.avatar_url if u.profile else None

    return ProjectCollaboratorResponse(
        id=collab.id,
        project_id=collab.project_id,
        user_id=collab.user_id,
        username=u.username,
        full_name=name,
        headline=headline,
        avatar_url=avatar,
        role=collab.role,
        created_at=collab.created_at,
    )

@router.put("/requests/{request_id}/decline")
def decline_collaboration_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    req = db.query(CollaborationRequest).filter(CollaborationRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Collaboration request not found")

    if req.project_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only project owner can decline collaboration requests")

    req.status = "declined"
    db.commit()
    return {"message": "Collaboration request declined"}
