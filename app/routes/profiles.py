from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
import uuid as uuidlib
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.profile import UserProfile
from app.models.project import Project
from app.models.connection import Connection
from app.models.collaboration import ProjectCollaborator
from app.routes.connections import connection_state
from app.services import storage
from app.services.skills import sync_skills_from_projects
from app.services.social import (
    accepts_connection_requests,
    audience_open,
    is_connected,
    may_message,
    project_stats,
    user_summary,
    visible_project_filter,
)
from app.core.dependencies import get_current_user, get_current_user_optional
from app.schemas.network import (
    CollabGroup,
    ConnectionUserSummary,
    ProjectPreview,
    Standing,
    TalentCard,
    UserProfileResponse,
    UserProfileUpdate,
)
from app.schemas.project import ProjectResponse
from app.services.roles import assign_identity
from app.services.talent import SORTS, field_top, groups_for, projects_for, standing_for, talent_board
AVATAR_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
OWN_SETTINGS = (
    "profile_visibility", "message_policy", "comment_policy", "activity_visibility",
    "connection_visibility", "collaboration_policy", "email_visibility", "resume_visibility",
    "notify_likes", "notify_comments", "notify_shares", "notify_connections",
    "notify_messages", "notify_collaborations",
)

router = APIRouter(prefix="/profiles", tags=["Profiles"])

def _build_profile_response(user: User, viewer_id: Optional[UUID] = None, db: Optional[Session] = None) -> UserProfileResponse:
    profile = user.profile
    full_name = profile.full_name if profile and profile.full_name else user.username
    headline = profile.headline if profile else None
    role = profile.role if profile else None
    bio = profile.bio if profile else None
    avatar_url = profile.avatar_url if profile else None
    github_url = profile.github_url if profile else None
    linkedin_url = profile.linkedin_url if profile else None
    website_url = profile.website_url if profile else None
    skills = profile.skills if profile else None
    location = profile.location if profile else None
    own = viewer_id == user.id
    connected = bool(db and viewer_id and is_connected(db, viewer_id, user.id))
    restricted = bool(profile and profile.profile_visibility == "connections" and not own and not connected)
    resume_url = profile.resume_url if profile else None
    company = profile.company if profile else None
    if restricted:
        headline = bio = skills = location = github_url = linkedin_url = website_url = resume_url = company = None
    elif not own and db is not None and not audience_open(db, user, viewer_id, "resume_visibility", default="connections"):
        resume_url = None
    show_email = own or (db is not None and audience_open(db, user, viewer_id, "email_visibility", default="private"))
    show_connections = own or db is None or audience_open(db, user, viewer_id, "connection_visibility", default="public")

    # Count stats (visible projects only, so private work is not revealed by the number)
    projects_count = db.query(Project.id).filter(Project.owner_id == user.id, visible_project_filter(viewer_id)).count() if db else 0
    collaborations_count = db.query(ProjectCollaborator.id).join(Project, Project.id == ProjectCollaborator.project_id).filter(
        ProjectCollaborator.user_id == user.id, visible_project_filter(viewer_id)).count() if db else 0

    connections_count = 0
    connection_status = connection_id = None

    if db:
        connections_count = db.query(Connection).filter(
            or_(Connection.requester_id == user.id, Connection.receiver_id == user.id),
            Connection.status == "accepted",
        ).count()

        if viewer_id:
            connection_status, connection_id = connection_state(db, viewer_id, user.id)
            if connection_status == "accepted":
                connection_status = "connected"

    return UserProfileResponse(
        id=profile.id if profile else user.id,
        user_id=user.id,
        username=user.username,
        email=user.email if show_email else None,
        timezone=profile.timezone if profile and viewer_id == user.id else None,
        full_name=full_name,
        headline=headline,
        role=role,
        company=company,
        guide_seen=bool(profile.guide_seen) if own and profile else None,
        bio=bio,
        avatar_url=avatar_url,
        github_url=github_url,
        linkedin_url=linkedin_url,
        website_url=website_url,
        skills=skills,
        location=location,
        created_at=user.created_at,
        projects_count=projects_count,
        connections_count=connections_count if show_connections else 0,
        connections_visible=show_connections,
        resume_url=resume_url,
        collaborations_count=collaborations_count,
        connection_status=connection_status,
        connection_id=connection_id,
        restricted=restricted,
        can_message=True if own or db is None else may_message(db, viewer_id, user) if viewer_id else False,
        accepts_connections=accepts_connection_requests(user),
        **({field: getattr(profile, field) for field in OWN_SETTINGS} if own and profile else {}),
    )

@router.get("/me", response_model=UserProfileResponse)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sync_skills_from_projects(db, current_user)
    if db.new or db.dirty:
        db.commit()
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

    sent = profile_data.model_fields_set
    changes = profile_data.model_dump(exclude_unset=True)
    if "role" in sent or "company" in sent:
        assign_identity(
            profile,
            changes.pop("role", None),
            changes.pop("company", None),
            company_sent="company" in sent,
            role_sent="role" in sent,
        )
    # Only fields sent by the client change; sending "" or null clears a field.
    for field, value in changes.items():
        if field == "timezone" and not value:
            continue  # always set; clearing it is not allowed
        setattr(profile, field, value.strip() or None if isinstance(value, str) else value)

    current_user.profile = profile
    sync_skills_from_projects(db, current_user)
    db.commit()
    db.refresh(profile)
    current_user.profile = profile
    return _build_profile_response(current_user, current_user.id, db)


@router.post("/me/guide")
def mark_recruiting_guide(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Remember that this person finished the recruiting guide, so it does not open on every visit."""
    profile = current_user.profile or UserProfile(user_id=current_user.id)
    if current_user.profile is None:
        db.add(profile)
        current_user.profile = profile
    profile.guide_seen = True
    db.commit()
    return {"guide_seen": True}


@router.post("/me/avatar", response_model=UserProfileResponse)
def upload_my_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in AVATAR_EXTS or not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Use a PNG, JPG, GIF, or WebP image")
    file.file.seek(0, 2)
    too_big = file.file.tell() > 5 * 1024 * 1024
    file.file.seek(0)
    if too_big:
        raise HTTPException(status_code=413, detail="Photo is larger than 5 MB")
    name = f"{current_user.id}_{uuidlib.uuid4().hex}.{ext}"
    storage.save(f"avatars/{name}", file.file)
    profile = current_user.profile
    if profile is None:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
    profile.avatar_url = f"/storage/avatars/{name}"
    db.commit()
    db.refresh(profile)
    current_user.profile = profile
    return _build_profile_response(current_user, current_user.id, db)


@router.post("/me/resume", response_model=UserProfileResponse)
def upload_my_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext != "pdf" or file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF resume")
    file.file.seek(0, 2)
    too_big = file.file.tell() > 8 * 1024 * 1024
    file.file.seek(0)
    if too_big:
        raise HTTPException(status_code=413, detail="Resume is larger than 8 MB")
    name = f"{current_user.id}_{uuidlib.uuid4().hex}.pdf"
    storage.save(f"resumes/{name}", file.file)
    profile = current_user.profile
    if profile is None:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
    profile.resume_url = f"/storage/resumes/{name}"
    db.commit()
    db.refresh(profile)
    current_user.profile = profile
    return _build_profile_response(current_user, current_user.id, db)

@router.get("/search", response_model=List[ConnectionUserSummary])
def search_developers(
    query: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    term = f"%{query.strip()}%"
    found = db.query(User).options(selectinload(User.profile)).outerjoin(User.profile).filter(
        or_(
            User.username.ilike(term),
            UserProfile.full_name.ilike(term),
            UserProfile.skills.ilike(term),
            UserProfile.headline.ilike(term),
        )
    )
    found = found.filter(or_(UserProfile.discoverable.is_(None), UserProfile.discoverable.is_(True)))
    if current_user is not None:
        found = found.filter(User.id != current_user.id)
    return [user_summary(u) for u in found.limit(limit).all()]


@router.get("/talent", response_model=List[TalentCard])
def talent(
    q: str = Query("", max_length=80),
    tech: str = Query("", description="Comma-separated tech names. A person must match every one."),
    sort: str = Query("projects"),
    limit: int = Query(30, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """People a recruiter can review, ranked by work this viewer is allowed to see."""
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail="Sort by projects, commits, pushes, connections, or activity")
    needed = [part.strip() for part in tech.split(",") if part.strip()]
    viewer = current_user.id if current_user else None
    return talent_board(db, viewer, q.strip(), needed, sort, limit)


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


@router.get("/{user_id}/collaborations", response_model=List[ProjectPreview])
def get_user_collaborations(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Projects this person joined (accepted memberships only) that the viewer is allowed to see."""
    viewer_id = current_user.id if current_user else None
    rows = (db.query(Project, ProjectCollaborator.role)
            .join(ProjectCollaborator, ProjectCollaborator.project_id == Project.id)
            .options(selectinload(Project.owner), selectinload(Project.tech_stacks))
            .filter(ProjectCollaborator.user_id == user_id, visible_project_filter(viewer_id))
            .order_by(ProjectCollaborator.created_at.desc()).all())
    stats = project_stats(db, [p.id for p, _ in rows], viewer_id)
    return [ProjectPreview(id=p.id, title=p.title, short_description=p.short_description, cover_image_url=p.cover_image_url,
                           tech_stacks=[t.name for t in p.tech_stacks], category=str(p.category.value if hasattr(p.category, "value") else p.category),
                           contribution_info=role, owner_username=p.owner.username, **stats[p.id])
            for p, role in rows]


@router.get("/{user_id}/projects", response_model=List[ProjectResponse])
def get_user_projects(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Every project this person owns that the viewer may open, including private ones they collaborate on."""
    from app.routes.projects import build_project_response

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Developer not found")
    viewer_id = current_user.id if current_user else None
    rows = projects_for(db, user.id, viewer_id)
    stats = project_stats(db, [p.id for p in rows], viewer_id)
    return [build_project_response(p, p.owner.username, viewer_user_id=viewer_id, stats=stats[p.id]) for p in rows]


@router.get("/{user_id}/groups", response_model=List[CollabGroup])
def get_user_groups(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Teams this person leads or has joined, limited to projects the viewer is allowed to see."""
    if not db.query(User.id).filter(User.id == user_id).first():
        raise HTTPException(status_code=404, detail="Developer not found")
    viewer_id = current_user.id if current_user else None
    return groups_for(db, user_id, viewer_id)


@router.get("/{user_id}/snapshot", response_model=Standing)
def get_user_snapshot(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Graphic summary of where this person stands, using only work and activity the viewer may see."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Developer not found")
    viewer_id = current_user.id if current_user else None
    return {**standing_for(db, user, viewer_id), "top": field_top(db, viewer_id)}
