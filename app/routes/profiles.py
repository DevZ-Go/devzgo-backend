from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.profile import UserProfile
from app.models.project import Project
from app.models.connection import Connection
from app.models.collaboration import ProjectCollaborator
from app.models.enums import ProjectVisibility
from app.core.dependencies import get_current_user, get_current_user_optional
from app.schemas.network import UserProfileResponse, UserProfileUpdate, ConnectionUserSummary

router = APIRouter(prefix="/profiles", tags=["Profiles"])

def _build_profile_response(user: User, viewer_id: Optional[UUID] = None, db: Optional[Session] = None) -> UserProfileResponse:
    profile = user.profile
    full_name = profile.full_name if profile and profile.full_name else user.username
    headline = profile.headline if profile else None
    bio = profile.bio if profile else None
    avatar_url = profile.avatar_url if profile else None
    github_url = profile.github_url if profile else None
    linkedin_url = profile.linkedin_url if profile else None
    website_url = profile.website_url if profile else None
    skills = profile.skills if profile else None
    location = profile.location if profile else None

    # Count stats
    projects_count = len(user.projects) if user.projects else 0
    collaborations_count = len(user.collaborations) if user.collaborations else 0

    connections_count = 0
    connection_status = None

    if db:
        connections_count = db.query(Connection).filter(
            or_(Connection.requester_id == user.id, Connection.receiver_id == user.id),
            Connection.status == "accepted",
        ).count()

        if viewer_id:
            if viewer_id == user.id:
                connection_status = "self"
            else:
                conn = db.query(Connection).filter(
                    or_(
                        and_(Connection.requester_id == viewer_id, Connection.receiver_id == user.id),
                        and_(Connection.requester_id == user.id, Connection.receiver_id == viewer_id),
                    )
                ).first()
                if conn:
                    if conn.status == "accepted":
                        connection_status = "connected"
                    elif conn.status == "pending":
                        connection_status = "pending_sent" if conn.requester_id == viewer_id else "pending_received"

    return UserProfileResponse(
        id=profile.id if profile else user.id,
        user_id=user.id,
        username=user.username,
        email=user.email,
        full_name=full_name,
        headline=headline,
        bio=bio,
        avatar_url=avatar_url,
        github_url=github_url,
        linkedin_url=linkedin_url,
        website_url=website_url,
        skills=skills,
        location=location,
        created_at=user.created_at,
        projects_count=projects_count,
        connections_count=connections_count,
        collaborations_count=collaborations_count,
        connection_status=connection_status,
    )

@router.get("/me", response_model=UserProfileResponse)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _build_profile_response(current_user, current_user.id, db)

@router.put("/me", response_model=UserProfileResponse)
def update_my_profile(
    profile_data: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = current_user.profile
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)

    if profile_data.full_name is not None:
        profile.full_name = profile_data.full_name
    if profile_data.headline is not None:
        profile.headline = profile_data.headline
    if profile_data.bio is not None:
        profile.bio = profile_data.bio
    if profile_data.avatar_url is not None:
        profile.avatar_url = profile_data.avatar_url
    if profile_data.github_url is not None:
        profile.github_url = profile_data.github_url
    if profile_data.linkedin_url is not None:
        profile.linkedin_url = profile_data.linkedin_url
    if profile_data.website_url is not None:
        profile.website_url = profile_data.website_url
    if profile_data.skills is not None:
        profile.skills = profile_data.skills
    if profile_data.location is not None:
        profile.location = profile_data.location

    db.commit()
    db.refresh(profile)
    return _build_profile_response(current_user, current_user.id, db)

@router.get("/search", response_model=List[ConnectionUserSummary])
def search_developers(
    query: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    term = f"%{query.strip()}%"
    users = db.query(User).outerjoin(User.profile).filter(
        or_(
            User.username.ilike(term),
            UserProfile.full_name.ilike(term),
            UserProfile.skills.ilike(term),
            UserProfile.headline.ilike(term),
        )
    ).limit(limit).all()

    results = []
    for u in users:
        name = u.profile.full_name if u.profile and u.profile.full_name else u.username
        headline = u.profile.headline if u.profile else None
        avatar = u.profile.avatar_url if u.profile else None
        results.append(
            ConnectionUserSummary(
                id=u.id,
                username=u.username,
                full_name=name,
                headline=headline,
                avatar_url=avatar,
            )
        )
    return results

@router.get("/{user_id}", response_model=UserProfileResponse)
def get_user_profile(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Developer not found")

    viewer_id = current_user.id if current_user else None
    return _build_profile_response(user, viewer_id, db)
