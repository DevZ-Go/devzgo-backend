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


@router.post("", response_model=ProjectResponse)
def create_project(
    project_data: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Validate tech stack IDs
    tech_stacks = db.query(TechStack).filter(
        TechStack.id.in_(project_data.tech_stack_ids)
    ).all()

    if len(tech_stacks) != len(project_data.tech_stack_ids):
        raise HTTPException(status_code=400, detail="Invalid tech stack ID provided")

    # Create project
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

    return ProjectResponse(
        id=new_project.id,
        title=new_project.title,
        short_description=new_project.short_description,
        full_description=new_project.full_description,
        category=new_project.category,
        visibility=new_project.visibility,
        cover_image_url=new_project.cover_image_url,
        created_at=new_project.created_at,
        updated_at=new_project.updated_at,
        owner_username=current_user.username,
        tech_stacks=[tech.name for tech in new_project.tech_stacks],
    )


@router.get("", response_model=List[ProjectResponse])
def list_public_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).filter(
        Project.visibility == ProjectVisibility.PUBLIC
    ).all()

    result = []

    for project in projects:
        result.append(
            ProjectResponse(
                id=project.id,
                title=project.title,
                short_description=project.short_description,
                full_description=project.full_description,
                category=project.category,
                visibility=project.visibility,
                cover_image_url=project.cover_image_url,
                created_at=project.created_at,
                updated_at=project.updated_at,
                owner_username=project.owner.username,
                tech_stacks=[tech.name for tech in project.tech_stacks],
            )
        )

    return result