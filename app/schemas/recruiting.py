from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.network import (
    ConnectionUserSummary,
    JoinedProject,
    Standing,
    UserProfileResponse,
    WorkType,
)

EntryStatus = Literal["new", "reviewing", "contacted", "interviewing", "hired", "not_a_fit"]


class ShortlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=280)


class ShortlistUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=280)


class EntryCreate(BaseModel):
    candidate_id: UUID
    status: EntryStatus = "new"
    note: Optional[str] = Field(default=None, max_length=2000)


class EntryUpdate(BaseModel):
    status: Optional[EntryStatus] = None
    note: Optional[str] = Field(default=None, max_length=2000)


class EntryResponse(BaseModel):
    id: UUID
    shortlist_id: UUID
    candidate: ConnectionUserSummary
    status: str
    note: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class ShortlistResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    count: int = 0
    statuses: dict = {}


class ShortlistDetail(ShortlistResponse):
    entries: List[EntryResponse] = []


class Membership(BaseModel):
    """Where a candidate sits in the viewer's own shortlists."""
    shortlist_id: UUID
    shortlist_name: str
    entry_id: UUID
    status: str
    note: Optional[str] = None


class RequestCreate(BaseModel):
    candidate_id: UUID
    role_title: str = Field(min_length=2, max_length=120)
    work_type: Optional[WorkType] = None
    message: str = Field(min_length=20, max_length=1500)


class RequestResponse(BaseModel):
    id: UUID
    recruiter: ConnectionUserSummary
    recruiter_company: Optional[str] = None
    candidate: ConnectionUserSummary
    role_title: str
    work_type: Optional[str] = None
    message: str
    status: str
    created_at: datetime
    responded_at: Optional[datetime] = None


class ProjectEvidence(BaseModel):
    id: UUID
    title: str
    short_description: Optional[str] = None
    cover_image_url: Optional[str] = None
    tech_stacks: List[str] = []
    languages: List[dict] = []
    category: Optional[str] = None
    complexity: Optional[str] = None
    github_url: Optional[str] = None
    has_source: bool = False
    commits: Optional[int] = None  # repository total from the last GitHub import
    likes_count: int = 0
    updated_at: Optional[datetime] = None


class PublishedWrap(BaseModel):
    month: str
    title: Optional[str] = None
    summary: Optional[str] = None
    persona: Optional[str] = None
    published_at: Optional[datetime] = None


class ContactState(BaseModel):
    allowed: bool
    reason: Optional[str] = None
    pending_request_id: Optional[UUID] = None
    accepted: bool = False


class CandidateView(BaseModel):
    profile: UserProfileResponse
    standing: Standing
    projects: List[ProjectEvidence] = []
    collaborations: List[JoinedProject] = []
    wraps: List[PublishedWrap] = []
    shortlists: List[Membership] = []
    contact: ContactState


class DashboardResponse(BaseModel):
    shortlists: int
    candidates: int
    statuses: dict
    requests: dict
    recent_entries: List[EntryResponse] = []
    recent_requests: List[RequestResponse] = []
