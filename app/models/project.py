import uuid
from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Table, Text, true
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.db.session import Base
from app.models.enums import ProjectCategory, ProjectVisibility

from app.models.associations import project_tech


''' The below class represents the Project model with its attributes and relationships to TechStack. 
 It includes fields for name, description, category, visibility, and timestamps for creation and 
 updates. The many-to-many relationship with TechStack is established through the project_techS 
 association table. '''

class Project(Base):
    __tablename__ = 'projects'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)

    title = Column(String, index=True, nullable=False)
    short_description = Column(String, nullable=True)
    full_description = Column(Text, nullable=True)

    files = relationship("File", backref="project", cascade="all, delete-orphan")

    category = Column(Enum(ProjectCategory), nullable=False)
    # When category is Other: short custom label (1–2 words), optional
    category_other = Column(String(64), nullable=True)
    visibility = Column(Enum(ProjectVisibility), nullable=False)

    cover_image_url = Column(String, nullable=True)
    demo_video_url = Column(String, nullable=True)

    # Extended project analysis & collaboration fields
    github_url = Column(String, nullable=True)
    github_commit_count = Column(Integer, nullable=True)  # repository total at the last import or refresh
    github_push_count = Column(Integer, nullable=True)  # push events stored for this repository
    github_pushed_at = Column(DateTime(timezone=True), nullable=True)
    github_owner_id = Column(String(32), nullable=True)  # GitHub account of whoever imported or refreshed it
    github_synced_at = Column(DateTime(timezone=True), nullable=True)
    analysis_summary = Column(Text, nullable=True)
    complexity = Column(String(32), nullable=True)  # Beginner, Intermediate, Advanced
    contribution_info = Column(Text, nullable=True)
    # Auto-analysis (separate from confirmed tech_stacks and from analysis_summary text).
    # [{"name": "TypeScript", "percentage": 64.8}, ...]
    language_stats = Column(JSONB, nullable=True)
    # [1, 5, 12] — TechStack catalog ids from the last workspace analysis
    detected_tech_stack_ids = Column(JSONB, nullable=True)

    comments_enabled = Column(Boolean, nullable=False, server_default=true())
    owner_focus = Column(String(200), nullable=True)  # what the owner says they are working on

    live_url = Column(String(500), nullable=True)  # demo or deployment link
    impact_note = Column(Text, nullable=True)  # outcomes the developer reports; shown as their own claim
    # Facts recorded at the last upload, import or re-analysis (app.services.source_facts); None before that.
    source_facts = Column(JSONB, nullable=True)
    source_analyzed_at = Column(DateTime(timezone=True), nullable=True)
    source_facts_version = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner = relationship("User", back_populates="projects")
    tech_stacks = relationship(
        "TechStack",
        secondary=project_tech,
        back_populates="projects"
    )

    collaborators = relationship("ProjectCollaborator", back_populates="project", cascade="all, delete-orphan")
    collaboration_requests = relationship("CollaborationRequest", back_populates="project", cascade="all, delete-orphan")
    activities = relationship("NetworkActivity", back_populates="project", cascade="all, delete-orphan")