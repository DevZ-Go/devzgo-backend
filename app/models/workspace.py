"""GitHub editing: one connection per user, their drafts, an audit trail, and sync jobs.

Drafts not touched for 30 days are deleted. Finished sync jobs are kept for 90 days.
Audit rows are kept, and they never store file content or tokens.
"""

import uuid

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, false as sa_false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.db.session import Base

DRAFT_DAYS = 30
SYNC_JOB_DAYS = 90


class GitHubConnection(Base):
    __tablename__ = "github_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    github_user_id = Column(BigInteger, nullable=True)
    login = Column(String(100), nullable=True)
    access_token_enc = Column(Text, nullable=True)
    refresh_token_enc = Column(Text, nullable=True)
    access_expires_at = Column(DateTime(timezone=True), nullable=True)
    refresh_expires_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(16), nullable=False, default="active", server_default="active")
    last_error = Column(String(300), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkspaceDraft(Base):
    __tablename__ = "workspace_drafts"
    __table_args__ = (UniqueConstraint("user_id", "project_id", "branch", "path", name="uq_workspace_draft"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    repo_id = Column(BigInteger, nullable=True)
    branch = Column(String(255), nullable=False)
    path = Column(String(500), nullable=False)
    base_blob_sha = Column(String(64), nullable=True)
    base_commit_sha = Column(String(40), nullable=True)
    content_enc = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    line_ending = Column(String(8), nullable=False, default="lf", server_default="lf")
    has_bom = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    revision = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkspaceAuditEvent(Base):
    __tablename__ = "workspace_audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(32), nullable=False)
    outcome = Column(String(16), nullable=False)
    repo_full_name = Column(String(200), nullable=True)
    branch = Column(String(255), nullable=True)
    path = Column(String(500), nullable=True)
    commit_sha = Column(String(40), nullable=True)
    url = Column(String(500), nullable=True)
    error_code = Column(String(40), nullable=True)
    idempotency_key = Column(String(80), nullable=True, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WorkspaceSyncJob(Base):
    __tablename__ = "workspace_sync_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    audit_event_id = Column(UUID(as_uuid=True), ForeignKey("workspace_audit_events.id", ondelete="SET NULL"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    trigger = Column(String(32), nullable=False)
    branch = Column(String(255), nullable=True)
    commit_sha = Column(String(40), nullable=True)
    path = Column(String(500), nullable=True)
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    steps = Column(JSONB, nullable=True)
    error = Column(String(300), nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
