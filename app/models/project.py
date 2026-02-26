import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Enum, Table, Text
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
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

    category = Column(Enum(ProjectCategory), nullable=False)
    visibility = Column(Enum(ProjectVisibility), nullable=False)

    cover_image_url = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner = relationship("User", back_populates="projects")
    tech_stacks = relationship(
        "TechStack",
        secondary=project_tech,
        back_populates="projects"
    )