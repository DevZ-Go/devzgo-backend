from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel

EvidenceClass = Literal["contribution_linked", "detected", "declared"]
CheckStatus = Literal["found", "not_detected", "not_assessed", "needs_review"]


class EvidenceFile(BaseModel):
    kind: Optional[str] = None
    file: str


class SkillProject(BaseModel):
    project_id: UUID
    title: str
    relation: Literal["owner", "member"]
    role: str
    visibility: str
    detected: bool
    kinds: List[str] = []
    files: List[EvidenceFile] = []
    language_share: Optional[float] = None
    unrecorded: bool = False
    commits: int = 0
    last_activity: Optional[datetime] = None


class Skill(BaseModel):
    name: str
    category: str
    classes: List[EvidenceClass]
    sources: List[str]
    project_count: int
    owned_projects: int
    team_projects: int
    commits: int
    last_activity: Optional[datetime] = None
    projects: List[SkillProject] = []


class SkillCategory(BaseModel):
    key: str
    label: str
    skills: List[Skill]


class SkillTotals(BaseModel):
    skills: int
    contribution_linked: int
    detected: int
    declared: int


class SkillMatrix(BaseModel):
    user_id: UUID
    restricted: bool
    projects_considered: int
    projects_with_file_evidence: int
    totals: SkillTotals
    categories: List[SkillCategory]


class CheckEvidence(BaseModel):
    file: Optional[str] = None
    line: Optional[int] = None
    url: Optional[str] = None
    label: Optional[str] = None


class QualityCheck(BaseModel):
    key: str
    label: str
    status: CheckStatus
    detail: str
    evidence: List[CheckEvidence] = []


class StatusCounts(BaseModel):
    found: int = 0
    not_detected: int = 0
    not_assessed: int = 0
    needs_review: int = 0


class QualitySection(BaseModel):
    key: str
    label: str
    restricted: bool = False
    checks: List[QualityCheck]
    summary: StatusCounts


class Recommendation(BaseModel):
    check: str
    status: CheckStatus
    text: str


class QualityReport(BaseModel):
    project_id: UUID
    audience: Literal["team", "viewer"]
    analyzed_at: Optional[datetime] = None
    has_source: bool
    source_recorded: bool
    can_refresh: bool
    totals: StatusCounts
    sections: List[QualitySection]
    recommendations: List[Recommendation] = []
