"""Who did what in a repository imported from GitHub, captured at import and on each owner refresh.

Contributors and commits are replaced on every refresh. Push events are kept and added to,
because GitHub only returns roughly the last 90 days of them.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.session import Base


class ProjectContributor(Base):
    __tablename__ = "project_contributors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    github_id = Column(String(32), nullable=True, index=True)
    login = Column(String(100), nullable=True)
    name = Column(String(200), nullable=True)
    email = Column(String(255), nullable=True, index=True)  # used for matching, never shown
    avatar_url = Column(String(500), nullable=True)
    commits = Column(Integer, nullable=False, default=0)
    additions = Column(Integer, nullable=True)
    deletions = Column(Integer, nullable=True)
    first_commit_at = Column(DateTime(timezone=True), nullable=True)
    last_commit_at = Column(DateTime(timezone=True), nullable=True)
    weekly = Column(JSONB, nullable=True)  # [[week start as unix seconds, commits], ...]


class ProjectCommit(Base):
    __tablename__ = "project_commits"
    __table_args__ = (
        UniqueConstraint("project_id", "sha", name="uq_project_commits_sha"),
        Index("ix_project_commits_project_time", "project_id", "committed_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    sha = Column(String(40), nullable=False)
    message = Column(String(300), nullable=False, default="")
    author_github_id = Column(String(32), nullable=True)
    author_login = Column(String(100), nullable=True)
    author_name = Column(String(200), nullable=True)
    author_email = Column(String(255), nullable=True)
    committed_at = Column(DateTime(timezone=True), nullable=True)


class ProjectPush(Base):
    __tablename__ = "project_pushes"
    __table_args__ = (
        UniqueConstraint("project_id", "event_id", name="uq_project_pushes_event"),
        Index("ix_project_pushes_project_time", "project_id", "pushed_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(String(32), nullable=False)
    actor_github_id = Column(String(32), nullable=True, index=True)
    actor_login = Column(String(100), nullable=True)
    branch = Column(String(200), nullable=True)
    commit_count = Column(Integer, nullable=True)
    pushed_at = Column(DateTime(timezone=True), nullable=True)
