from datetime import datetime
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.network import ConnectionUserSummary

ChallengeType = Literal["frontend", "backend", "full_stack", "bug_fix", "code_review", "api_design", "database_design",
                        "architecture"]
Difficulty = Literal["introductory", "intermediate", "advanced"]
Visibility = Literal["invitation", "public"]
Rating = Literal["needs_improvement", "meets_expectations", "strong"]


class Criterion(BaseModel):
    key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)


class ChallengeIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    challenge_type: ChallengeType
    technologies: List[str] = Field(default_factory=list, max_length=12)
    difficulty: Difficulty
    estimated_minutes: Optional[int] = Field(default=None, ge=15, le=60 * 40)
    description: str = Field(min_length=10, max_length=2000)
    instructions: str = Field(min_length=10, max_length=10_000)
    criteria: Optional[List[Criterion]] = Field(default=None, max_length=10)  # None = the default rubric
    deadline_at: Optional[datetime] = None
    visibility: Visibility = "invitation"

    @field_validator("technologies")
    @classmethod
    def clean_technologies(cls, value: List[str]) -> List[str]:
        out: List[str] = []
        for name in value:
            name = " ".join(name.split())[:60]
            if name and name.lower() not in {n.lower() for n in out}:
                out.append(name)
        return out

    @field_validator("deadline_at")
    @classmethod
    def require_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            raise ValueError("Send the deadline with a time zone (UTC)")
        return value


class ChallengeSummary(BaseModel):
    id: UUID
    title: str
    challenge_type: str
    technologies: List[str]
    difficulty: str
    estimated_minutes: Optional[int] = None
    deadline_at: Optional[datetime] = None
    visibility: str
    status: str
    accepting: bool  # open and before the deadline
    recruiter: ConnectionUserSummary
    company: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: datetime


class ChallengeDetail(ChallengeSummary):
    description: str
    instructions: str
    criteria: List[Criterion]


class ChallengeCounts(BaseModel):
    invited: int = 0
    accepted: int = 0
    submitted: int = 0
    in_review: int = 0
    reviewed: int = 0


class RecruiterChallenge(ChallengeDetail):
    counts: ChallengeCounts
    has_reviews: bool = False


class InviteIn(BaseModel):
    developer_ids: List[UUID] = Field(min_length=1, max_length=25)
    message: Optional[str] = Field(default=None, max_length=1000)


class InviteResult(BaseModel):
    invited: List[UUID]
    skipped: Dict[str, str]  # developer id -> reason


class InvitationOut(BaseModel):
    id: UUID
    challenge_id: UUID
    developer: ConnectionUserSummary
    message: Optional[str] = None
    status: str
    created_at: datetime
    responded_at: Optional[datetime] = None


class SubmissionIn(BaseModel):
    repo_url: Optional[str] = Field(default=None, max_length=500)
    project_id: Optional[UUID] = None
    explanation: str = Field(min_length=10, max_length=5000)

    @field_validator("repo_url")
    @classmethod
    def https_only(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not value.lower().startswith("https://") or " " in value:
            raise ValueError("Use a full https:// link")
        return value


class ReviewIn(BaseModel):
    ratings: Dict[str, Rating] = Field(default_factory=dict)
    feedback: Optional[str] = Field(default=None, max_length=5000)
    release: bool = False


class ReviewOut(BaseModel):
    status: str
    ratings: Dict[str, Rating]
    feedback: Optional[str] = None
    released_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SubmissionProject(BaseModel):
    id: UUID
    title: str


class SubmissionOut(BaseModel):
    id: UUID
    challenge_id: UUID
    developer: ConnectionUserSummary
    repo_url: Optional[str] = None
    project: Optional[SubmissionProject] = None
    explanation: str
    status: str
    revision: int
    submitted_at: datetime
    updated_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    share_ratings: bool = False
    share_feedback: bool = False
    review: Optional[ReviewOut] = None  # developers only ever see a released review


class SharingIn(BaseModel):
    share_ratings: bool
    share_feedback: bool = False


class DeveloperInvitation(BaseModel):
    id: UUID
    status: str
    message: Optional[str] = None
    created_at: datetime
    challenge: ChallengeSummary


class DeveloperSubmission(SubmissionOut):
    challenge: ChallengeDetail


class AssessmentsOut(BaseModel):
    invitations: List[DeveloperInvitation]
    open_challenges: List[ChallengeSummary]
    submissions: List[DeveloperSubmission]


class SharedRating(BaseModel):
    label: str
    rating: Rating


class SharedAssessment(BaseModel):
    submission_id: UUID
    title: str
    challenge_type: str
    difficulty: str
    technologies: List[str]
    company: Optional[str] = None
    reviewer: ConnectionUserSummary
    reviewed_at: Optional[datetime] = None
    ratings: List[SharedRating]
    feedback: Optional[str] = None
