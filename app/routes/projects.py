from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.db.session import get_db
from app.models.project import Project
from app.models.techstack import TechStack
from app.models.enums import ProjectVisibility
from app.schemas.project import ProjectCreate, ProjectResponse
from app.core.dependencies import get_current_user
from app.models.user import User

router = APIRouter(prefix="/projects", tags=["Projects"])


# ---------------- CREATE PROJECT ----------------
@router.post("", response_model=ProjectResponse)
def create_project(
    project_data: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    tech_stacks = db.query(TechStack).filter(
        TechStack.id.in_(project_data.tech_stack_ids)
    ).all()

    if len(tech_stacks) != len(project_data.tech_stack_ids):
        raise HTTPException(status_code=400, detail="Invalid tech stack ID provided")

    new_project = Project(
        owner_id=current_user.id,
        title=project_data.title,
        short_description=project_data.short_description,
        full_description=project_data.full_description,
        category=project_data.category,
        visibility=project_data.visibility,
    )

    new_project.tech_stacks = tech_stacks

    db.add(new_project)
    db.commit()
    db.refresh(new_project)

    return build_project_response(new_project, current_user.username)


# ---------------- LIST PUBLIC PROJECTS ----------------
@router.get("", response_model=List[ProjectResponse])
def list_public_projects(
    tech_stack_id: int | None = None,
    db: Session = Depends(get_db)
):
    query = db.query(Project).filter(
        Project.visibility == ProjectVisibility.PUBLIC
    )

    if tech_stack_id:
        query = query.join(Project.tech_stacks).filter(
            TechStack.id == tech_stack_id
        )

    projects = query.all()

    return [
        build_project_response(p, p.owner.username)
        for p in projects
    ]


# ---------------- LIST MY PROJECTS ----------------
# 🔥 IMPORTANT: must come BEFORE /{project_id}
@router.get("/me", response_model=List[ProjectResponse])
def list_my_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    projects = db.query(Project).filter(
        Project.owner_id == current_user.id
    ).all()

    return [
        build_project_response(p, current_user.username)
        for p in projects
    ]


# ---------------- LIST TECH STACKS ----------------
# 🔥 IMPORTANT: must come BEFORE /{project_id}
@router.get("/techstacks")
def list_techstacks(db: Session = Depends(get_db)):
    tech_stacks = db.query(TechStack).all()
    return [{"id": tech.id, "name": tech.name} for tech in tech_stacks]


# ---------------- GET SINGLE PROJECT ----------------
@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: UUID, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.visibility == ProjectVisibility.PRIVATE:
        raise HTTPException(status_code=403, detail="Private project")

    return build_project_response(project, project.owner.username)


# ---------------- UPDATE PROJECT ----------------
@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    project_update: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    project.title = project_update.title
    project.short_description = project_update.short_description
    project.full_description = project_update.full_description
    project.category = project_update.category
    project.visibility = project_update.visibility

    tech_stacks = db.query(TechStack).filter(
        TechStack.id.in_(project_update.tech_stack_ids)
    ).all()

    project.tech_stacks = tech_stacks

    db.commit()
    db.refresh(project)

    return build_project_response(project, current_user.username)


# ---------------- HELPER FUNCTION ----------------
def build_project_response(project: Project, owner_username: str):
    return ProjectResponse(
        id=project.id,
        title=project.title,
        short_description=project.short_description,
        full_description=project.full_description,
        category=project.category,
        visibility=project.visibility,
        cover_image_url=project.cover_image_url,
        created_at=project.created_at,
        updated_at=project.updated_at,
        owner_username=owner_username,
        tech_stacks=[tech.name for tech in project.tech_stacks],
    )