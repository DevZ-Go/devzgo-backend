import re
from uuid import UUID
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator
from typing import Annotated, Dict, List, Optional
from datetime import datetime
from app.models.enums import ProjectCategory, ProjectVisibility
from app.schemas.network import ConnectionUserSummary

class ProjectCreate(BaseModel):
    title: str
    short_description: Optional[str] = None
    full_description: str | None = None
    category: ProjectCategory
    category_other: Optional[str] = Field(
        default=None,
        description="When category is Other: optional short label (e.g. IoT, Blockchain).",
    )
    visibility: ProjectVisibility
    tech_stack_ids: List[int] = []
    cover_image_url: Optional[str] = None
    demo_video_url: Optional[str] = None
    comments_enabled: Optional[bool] = None  # omitted on update = unchanged
    live_url: Optional[str] = None  # omitted on update = unchanged; "" clears
    impact_note: Optional[str] = Field(default=None, max_length=1000)  # omitted on update = unchanged; "" clears

    @field_validator("live_url", mode="before")
    @classmethod
    def check_live_url(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return ""
        if len(s) > 500 or not re.match(r"^https?://[^\s/$.?#][^\s]*$", s, re.I):
            raise ValueError("Enter a full link starting with http:// or https://")
        return s

    @field_validator("impact_note", mode="before")
    @classmethod
    def trim_impact_note(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("category_other", mode="before")
    @classmethod
    def normalize_category_other(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            s = " ".join(v.split())
            return s[:64] if s else None
        return v

    @model_validator(mode="after")
    def clear_category_other_unless_other(self):
        if self.category != ProjectCategory.OTHER:
            return self.model_copy(update={"category_other": None})
        return self

class ProjectFileContentResponse(BaseModel):
    """Safe text preview of one workspace file (or metadata-only for binary/secret)."""

    path: str
    name: str
    extension: str
    language: Optional[str] = None
    size: int
    is_binary: bool = False
    is_secret: bool = False
    is_image: bool = False
    content: Optional[str] = None
    message: Optional[str] = None


class LanguageStat(BaseModel):
    name: str
    percentage: float


class ProjectAnalysisResponse(BaseModel):
    project_id: UUID
    languages: List[LanguageStat] = []
    detected_tech_stacks: List[str] = []
    detected_tech_stack_ids: List[int] = []
    confirmed_tech_stacks: List[str] = []
    confirmed_tech_stack_ids: List[int] = []


class ProjectFileEntry(BaseModel):
    """One row from the files table (workspace tree or attachment metadata)."""

    id: UUID
    file_name: str
    file_path: str
    is_directory: bool
    parent_path: Optional[str] = None

    class Config:
        from_attributes = True


class SourceSnapshot(BaseModel):
    origin: str
    ref: Optional[str] = None
    commit_sha: Optional[str] = None
    synced_at: Optional[datetime] = None


class ProjectSource(BaseModel):
    kind: str = "zip"
    repository: Optional[str] = None
    default_branch: Optional[str] = None
    snapshot: Optional[SourceSnapshot] = None
    sync_state: Optional[str] = None
    sync_error: Optional[str] = None
    sync_updated_at: Optional[datetime] = None


class ProjectResponse(BaseModel):
    id: UUID
    owner_id: UUID
    # True when the Bearer token identifies the project owner
    is_owner: bool = False
    title: str
    short_description: Optional[str] = None
    full_description: str | None
    category: ProjectCategory
    category_other: Optional[str] = None
    visibility: ProjectVisibility

    cover_image_url: Optional[str] = None
    demo_video_url: Optional[str] = None
    github_url: Optional[str] = None
    live_url: Optional[str] = None
    impact_note: Optional[str] = None
    source_analyzed_at: Optional[datetime] = None
    source: Optional[ProjectSource] = None
    created_at: datetime
    updated_at: datetime

    owner_username: str
    tech_stacks: List[str] = []
    tech_stack_ids: List[int] = []
    languages: List[LanguageStat] = []
    detected_tech_stacks: List[str] = []
    detected_tech_stack_ids: List[int] = []
    likes_count: int = 0
    is_liked: bool = False
    comments_count: int = 0
    comments_enabled: bool = True
    access: Optional[Dict[str, bool]] = None  # owner, member, view, edit, files, secrets, comment, manage_team for this viewer

    class Config:
        from_attributes = True

class ProjectLikeResponse(BaseModel):
    project_id: UUID
    likes_count: int
    is_liked: bool

class ProjectCommentCreate(BaseModel):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

class ProjectCommentResponse(BaseModel):
    id: UUID
    project_id: UUID
    body: str
    created_at: datetime
    author: ConnectionUserSummary
    can_delete: bool

class ProjectCommentPage(BaseModel):
    items: List[ProjectCommentResponse]
    next_page: Optional[str] = None
