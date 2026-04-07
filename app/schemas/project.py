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
    cover_image_url: Optional[str] = None
    demo_video_url: Optional[str] = None

class ProjectFileEntry(BaseModel):
    """One row from the files table (workspace tree or attachment metadata)."""

    id: UUID
    file_name: str
    file_path: str
    is_directory: bool
    parent_path: Optional[str] = None

    class Config:
        from_attributes = True


class ProjectResponse(BaseModel):
    id: UUID
    owner_id: UUID
    # True when the Bearer token identifies the project owner
    is_owner: bool = False
    title: str
    short_description: Optional[str] = None
    full_description: str | None
    category: ProjectCategory
    visibility: ProjectVisibility
    
    cover_image_url: Optional[str] = None
    demo_video_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    owner_username: str
    tech_stacks: List[str] = []
    tech_stack_ids: List[int] = []

    class Config:
        from_attributes = True