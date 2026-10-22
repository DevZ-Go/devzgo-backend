from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class SuggestedRequirement(BaseModel):
    name: str
    category: str
    reason: str
    excerpt: str


class AmbiguousTerm(BaseModel):
    term: str
    name: str
    category: str
    excerpt: str


class ExtractResponse(BaseModel):
    required: List[SuggestedRequirement]
    preferred: List[SuggestedRequirement]
    ambiguous: List[AmbiguousTerm]


class MatchRequest(BaseModel):
    required: List[str] = Field(default_factory=list, max_length=25)
    preferred: List[str] = Field(default_factory=list, max_length=25)
    open_to_work: bool = False
    work_type: str = Field(default="", max_length=20)
    arrangement: str = Field(default="", max_length=20)
    location: str = Field(default="", max_length=80)
    min_relevant_projects: int = Field(default=0, ge=0, le=20)
    require_all_required: bool = False


class MatchProject(BaseModel):
    project_id: UUID
    title: str
    relation: Literal["owner", "member"]
    role: str
    detected: bool
    commits: int = 0


class MatchRequirement(BaseModel):
    name: str
    importance: Literal["required", "preferred"]
    status: Literal["evidenced", "declared_project", "declared_profile", "missing"]
    weight: float
    contribution_linked: bool
    project_count: int
    projects: List[MatchProject]


class MatchResult(BaseModel):
    id: UUID
    username: str
    full_name: Optional[str] = None
    headline: Optional[str] = None
    avatar_url: Optional[str] = None
    location: Optional[str] = None
    open_to_work: bool
    work_types: List[str]
    work_arrangements: List[str]
    percent: int
    points: float
    max_points: int
    required_met: int
    required_total: int
    preferred_met: int
    preferred_total: int
    relevant_projects: int
    contribution_linked: int
    requirements: List[MatchRequirement]


class MatchResponse(BaseModel):
    required: List[str]
    preferred: List[str]
    formula: str
    considered: int
    restricted: int
    results: List[MatchResult]
