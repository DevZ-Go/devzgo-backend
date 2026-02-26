from uuid import UUID
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.models.enums import ProjectCategory, ProjectVisibility

class ProjectCreate(BaseModel):
    title: str
    short_description: Optional[str] = None
    full_description: str | None = None
    category: ProjectCategory
    visibility: ProjectVisibility
    tech_stack_ids: List[int] = []

class ProjectResponse(BaseModel):
    id: UUID
    title: str
    short_description: Optional[str] = None
    full_description: str | None
    category: ProjectCategory
    visibility: ProjectVisibility
    
    cover_image_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    owner_username: str
    tech_stacks: List[str] = []

    class Config:
        from_attributes = True