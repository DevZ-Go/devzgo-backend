"""Technical challenges: a recruiter sets a task, developers submit work, the recruiter reviews it by hand.

Nothing is run or graded automatically. A review stays between the developer and the recruiter until
the developer chooses to show it on their portfolio.
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

CHALLENGE_TYPES = ("frontend", "backend", "full_stack", "bug_fix", "code_review", "api_design", "database_design",
                   "architecture")
DIFFICULTIES = ("introductory", "intermediate", "advanced")
CHALLENGE_STATUSES = ("draft", "open", "closed")
VISIBILITIES = ("invitation", "public")
INVITATION_STATUSES = ("pending", "accepted", "declined", "withdrawn")
SUBMISSION_STATUSES = ("submitted", "withdrawn", "in_review", "reviewed")
RATINGS = ("needs_improvement", "meets_expectations", "strong")
DEFAULT_CRITERIA = [
    {"key": "correctness", "label": "Correctness", "description": "Does the work do what the task asks?"},
    {"key": "code_quality", "label": "Code quality", "description": "Readable, well structured and maintainable."},
    {"key": "problem_solving", "label": "Problem solving", "description": "A sound approach, including edge cases."},
    {"key": "testing", "label": "Testing", "description": "Tests or other evidence that the work behaves as intended."},
    {"key": "documentation", "label": "Documentation", "description": "Clear setup notes and explanation."},
    {"key": "communication", "label": "Technical communication", "description": "Decisions and trade-offs explained."},
]


class TechnicalChallenge(Base):
    __tablename__ = "technical_challenges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recruiter_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(120), nullable=False)
    challenge_type = Column(String(30), nullable=False)
    technologies = Column(JSONB, nullable=False, default=list)
    difficulty = Column(String(20), nullable=False)
    estimated_minutes = Column(Integer, nullable=True)
    description = Column(Text, nullable=False)
    instructions = Column(Text, nullable=False)
    criteria = Column(JSONB, nullable=False, default=list)  # [{"key", "label", "description"}]
    deadline_at = Column(DateTime(timezone=True), nullable=True)  # UTC; submissions close at this moment
    visibility = Column(String(20), nullable=False, default="invitation", server_default="invitation")
    status = Column(String(20), nullable=False, default="draft", server_default="draft")
    published_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    recruiter = relationship("User", foreign_keys=[recruiter_id])
    invitations = relationship("ChallengeInvitation", back_populates="challenge", cascade="all, delete-orphan")
    submissions = relationship("ChallengeSubmission", back_populates="challenge", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_technical_challenges_recruiter", "recruiter_id"),
        Index("ix_technical_challenges_status_visibility", "status", "visibility"),
    )


class ChallengeInvitation(Base):
    __tablename__ = "challenge_invitations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id = Column(UUID(as_uuid=True), ForeignKey("technical_challenges.id", ondelete="CASCADE"), nullable=False)
    developer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending", server_default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    responded_at = Column(DateTime(timezone=True), nullable=True)

    challenge = relationship("TechnicalChallenge", back_populates="invitations")
    developer = relationship("User", foreign_keys=[developer_id])

    __table_args__ = (
        UniqueConstraint("challenge_id", "developer_id", name="uq_challenge_invitations_developer"),
        Index("ix_challenge_invitations_developer_status", "developer_id", "status"),
    )


class ChallengeSubmission(Base):
    """One per developer per challenge. Updated in place on resubmission; `revision` counts the versions."""
    __tablename__ = "challenge_submissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id = Column(UUID(as_uuid=True), ForeignKey("technical_challenges.id", ondelete="CASCADE"), nullable=False)
    developer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    invitation_id = Column(UUID(as_uuid=True), ForeignKey("challenge_invitations.id", ondelete="SET NULL"), nullable=True)
    repo_url = Column(String(500), nullable=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    explanation = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="submitted", server_default="submitted")
    revision = Column(Integer, nullable=False, default=1, server_default="1")
    share_ratings = Column(Boolean, nullable=False, default=False, server_default=false())
    share_feedback = Column(Boolean, nullable=False, default=False, server_default=false())
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    withdrawn_at = Column(DateTime(timezone=True), nullable=True)

    challenge = relationship("TechnicalChallenge", back_populates="submissions")
    developer = relationship("User", foreign_keys=[developer_id])
    project = relationship("Project", foreign_keys=[project_id])
    review = relationship("ChallengeReview", back_populates="submission", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("challenge_id", "developer_id", name="uq_challenge_submissions_developer"),
        Index("ix_challenge_submissions_challenge_status", "challenge_id", "status"),
        Index("ix_challenge_submissions_developer", "developer_id"),
    )


class ChallengeReview(Base):
    """The recruiter's rubric ratings and feedback. Drafts are visible to the reviewer only."""
    __tablename__ = "challenge_reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(UUID(as_uuid=True), ForeignKey("challenge_submissions.id", ondelete="CASCADE"),
                           nullable=False, unique=True)
    reviewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ratings = Column(JSONB, nullable=False, default=dict)  # {"correctness": "strong", ...}
    feedback = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="draft", server_default="draft")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    released_at = Column(DateTime(timezone=True), nullable=True)

    submission = relationship("ChallengeSubmission", back_populates="review")
