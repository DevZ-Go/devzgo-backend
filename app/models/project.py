import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Enum, Text
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.db.session import Base
from app.models.enums import ProjectCategory, ProjectVisibility

from app.models.associations import project_tech


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

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

    # Auto-analysis (separate from confirmed tech_stacks M2M).
    # [{"name": "TypeScript", "percentage": 64.8}, ...]
    language_stats = Column(JSONB, nullable=True)
    # [1, 5, 12] — TechStack catalog ids from last workspace analysis
    detected_tech_stack_ids = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner = relationship("User", back_populates="projects")
    tech_stacks = relationship(
        "TechStack",
        secondary=project_tech,
        back_populates="projects",
    )
