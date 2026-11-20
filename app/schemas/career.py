from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

ExperienceCategory = Literal[
    "full_time", "part_time", "contract", "freelance", "coop", "internship",
    "volunteer", "academic_project", "independent_project", "other",
]
Audience = Literal["private", "connections", "recruiters", "public"]
ReviewState = Literal["draft", "pending_review", "confirmed", "rejected"]


class SkillIntervalIn(BaseModel):
    tech_stack_id: int
    start_year: Optional[int] = None
    start_month: Optional[int] = Field(default=None, ge=1, le=12)
    end_year: Optional[int] = None
    end_month: Optional[int] = Field(default=None, ge=1, le=12)


class SkillIntervalOut(SkillIntervalIn):
    id: UUID
    tech_name: Optional[str] = None
    review_state: ReviewState = "confirmed"


class ExperienceIn(BaseModel):
    organization: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    category: ExperienceCategory
    start_year: Optional[int] = None
    start_month: Optional[int] = Field(default=None, ge=1, le=12)
    end_year: Optional[int] = None
    end_month: Optional[int] = Field(default=None, ge=1, le=12)
    is_current: bool = False
    location: Optional[str] = Field(default=None, max_length=160)
    description: Optional[str] = None
    responsibilities: List[str] = Field(default_factory=list)
    achievements: List[str] = Field(default_factory=list)
    visibility: Audience = "connections"
    sort_order: int = 0
    skill_intervals: List[SkillIntervalIn] = Field(default_factory=list)
    project_ids: List[UUID] = Field(default_factory=list)


class ExperienceUpdate(BaseModel):
    organization: Optional[str] = Field(default=None, min_length=1, max_length=200)
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    category: Optional[ExperienceCategory] = None
    start_year: Optional[int] = None
    start_month: Optional[int] = Field(default=None, ge=1, le=12)
    end_year: Optional[int] = None
    end_month: Optional[int] = Field(default=None, ge=1, le=12)
    is_current: Optional[bool] = None
    location: Optional[str] = Field(default=None, max_length=160)
    description: Optional[str] = None
    responsibilities: Optional[List[str]] = None
    achievements: Optional[List[str]] = None
    visibility: Optional[Audience] = None
    sort_order: Optional[int] = None
    skill_intervals: Optional[List[SkillIntervalIn]] = None
    project_ids: Optional[List[UUID]] = None
    review_state: Optional[ReviewState] = None


class ExperienceOut(BaseModel):
    id: UUID
    organization: str
    title: str
    category: ExperienceCategory
    start_year: Optional[int] = None
    start_month: Optional[int] = None
    end_year: Optional[int] = None
    end_month: Optional[int] = None
    is_current: bool
    location: Optional[str] = None
    description: Optional[str] = None
    responsibilities: List[str] = []
    achievements: List[str] = []
    source: str
    review_state: ReviewState
    visibility: Audience
    sort_order: int
    skill_intervals: List[SkillIntervalOut] = []
    project_ids: List[UUID] = []
    duration: Optional[dict] = None
    created_at: Optional[datetime] = None


class EducationIn(BaseModel):
    institution: str = Field(min_length=1, max_length=200)
    degree: Optional[str] = Field(default=None, max_length=200)
    field_of_study: Optional[str] = Field(default=None, max_length=200)
    start_year: Optional[int] = None
    start_month: Optional[int] = Field(default=None, ge=1, le=12)
    end_year: Optional[int] = None
    end_month: Optional[int] = Field(default=None, ge=1, le=12)
    is_current: bool = False
    description: Optional[str] = None
    visibility: Audience = "connections"
    sort_order: int = 0


class EducationOut(EducationIn):
    id: UUID
    source: str = "manual"
    review_state: ReviewState = "confirmed"


class CertificationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    issuer: Optional[str] = Field(default=None, max_length=200)
    year: Optional[int] = None
    month: Optional[int] = Field(default=None, ge=1, le=12)
    credential_url: Optional[str] = Field(default=None, max_length=500)
    visibility: Audience = "connections"
    sort_order: int = 0


class CertificationOut(CertificationIn):
    id: UUID
    source: str = "manual"
    review_state: ReviewState = "confirmed"


class ReorderIn(BaseModel):
    ids: List[UUID]


class CareerSummary(BaseModel):
    experiences: List[ExperienceOut]
    education: List[EducationOut]
    certifications: List[CertificationOut]
    category_durations: dict[str, Any]
    skill_durations: dict[str, Any]


class FieldVisibilityIn(BaseModel):
    resource_type: Literal["experience", "education", "certification", "badge"]
    resource_id: UUID
    field_name: str = Field(min_length=1, max_length=60)
    audience: Audience


class ExtractedItemOut(BaseModel):
    id: UUID
    item_type: str
    field_path: str
    extracted_value: Any
    normalized_value: Optional[Any] = None
    source_location: Optional[str] = None
    confidence: str
    review_state: ReviewState


class ImportJobOut(BaseModel):
    id: UUID
    original_filename: str
    format: str
    status: str
    error: Optional[str] = None
    analysis: Optional[dict] = None
    items: List[ExtractedItemOut] = []
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ImportItemReview(BaseModel):
    item_id: UUID
    review_state: ReviewState
    normalized_value: Optional[Any] = None
    visibility: Audience = "connections"
    # Publish extracted skill (or any item) as a different career type after edits.
    save_as: Optional[Literal["experience", "education", "skill", "certification"]] = None


class ImportConfirmIn(BaseModel):
    items: List[ImportItemReview]
