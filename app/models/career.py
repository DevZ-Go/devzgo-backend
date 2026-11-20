"""Structured career profile: experiences, education, certifications, import jobs, field visibility."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

EXPERIENCE_CATEGORIES = (
    "full_time", "part_time", "contract", "freelance", "coop", "internship",
    "volunteer", "academic_project", "independent_project", "other",
)
REVIEW_STATES = ("draft", "pending_review", "confirmed", "rejected")
AUDIENCES = ("private", "connections", "recruiters", "public")
IMPORT_STATUSES = ("queued", "processing", "ready", "failed", "confirmed")
IMPORT_FORMATS = ("pdf", "docx", "linkedin_csv")


class CareerExperience(Base):
    __tablename__ = "career_experiences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization = Column(String(200), nullable=False)
    title = Column(String(200), nullable=False)
    category = Column(String(40), nullable=False)
    start_year = Column(Integer, nullable=True)
    start_month = Column(Integer, nullable=True)
    end_year = Column(Integer, nullable=True)
    end_month = Column(Integer, nullable=True)
    is_current = Column(Boolean, nullable=False, default=False, server_default=false())
    location = Column(String(160), nullable=True)
    description = Column(Text, nullable=True)
    responsibilities = Column(JSONB, nullable=False, default=list)  # list[str]
    achievements = Column(JSONB, nullable=False, default=list)  # list[str]
    source = Column(String(40), nullable=False, default="manual", server_default="manual")
    provenance = Column(JSONB, nullable=True)
    review_state = Column(String(20), nullable=False, default="confirmed", server_default="confirmed")
    visibility = Column(String(20), nullable=False, default="connections", server_default="connections")
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    import_job_id = Column(UUID(as_uuid=True), ForeignKey("resume_import_jobs.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    skill_intervals = relationship("CareerSkillInterval", back_populates="experience", cascade="all, delete-orphan")
    project_links = relationship("CareerExperienceProject", back_populates="experience", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_career_experiences_user", "user_id"),
        Index("ix_career_experiences_user_review", "user_id", "review_state"),
    )


class CareerSkillInterval(Base):
    __tablename__ = "career_skill_intervals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experience_id = Column(UUID(as_uuid=True), ForeignKey("career_experiences.id", ondelete="CASCADE"), nullable=False)
    tech_stack_id = Column(Integer, ForeignKey("techstacks.id", ondelete="CASCADE"), nullable=False)
    start_year = Column(Integer, nullable=True)
    start_month = Column(Integer, nullable=True)
    end_year = Column(Integer, nullable=True)
    end_month = Column(Integer, nullable=True)
    review_state = Column(String(20), nullable=False, default="confirmed", server_default="confirmed")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    experience = relationship("CareerExperience", back_populates="skill_intervals")
    tech_stack = relationship("TechStack")

    __table_args__ = (
        UniqueConstraint("experience_id", "tech_stack_id", name="uq_career_skill_interval"),
        Index("ix_career_skill_intervals_tech", "tech_stack_id"),
    )


class CareerExperienceProject(Base):
    __tablename__ = "career_experience_projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experience_id = Column(UUID(as_uuid=True), ForeignKey("career_experiences.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    note = Column(String(280), nullable=True)

    experience = relationship("CareerExperience", back_populates="project_links")
    project = relationship("Project")

    __table_args__ = (
        UniqueConstraint("experience_id", "project_id", name="uq_career_experience_project"),
    )


class CareerEducation(Base):
    __tablename__ = "career_education"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    institution = Column(String(200), nullable=False)
    degree = Column(String(200), nullable=True)
    field_of_study = Column(String(200), nullable=True)
    start_year = Column(Integer, nullable=True)
    start_month = Column(Integer, nullable=True)
    end_year = Column(Integer, nullable=True)
    end_month = Column(Integer, nullable=True)
    is_current = Column(Boolean, nullable=False, default=False, server_default=false())
    description = Column(Text, nullable=True)
    source = Column(String(40), nullable=False, default="manual", server_default="manual")
    review_state = Column(String(20), nullable=False, default="confirmed", server_default="confirmed")
    visibility = Column(String(20), nullable=False, default="connections", server_default="connections")
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_career_education_user", "user_id"),)


class CareerCertification(Base):
    __tablename__ = "career_certifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    issuer = Column(String(200), nullable=True)
    year = Column(Integer, nullable=True)
    month = Column(Integer, nullable=True)
    credential_url = Column(String(500), nullable=True)
    source = Column(String(40), nullable=False, default="manual", server_default="manual")
    review_state = Column(String(20), nullable=False, default="confirmed", server_default="confirmed")
    visibility = Column(String(20), nullable=False, default="connections", server_default="connections")
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_career_certifications_user", "user_id"),)


class CareerFieldVisibility(Base):
    __tablename__ = "career_field_visibility"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    resource_type = Column(String(40), nullable=False)  # experience | education | certification | badge
    resource_id = Column(UUID(as_uuid=True), nullable=False)
    field_name = Column(String(60), nullable=False)
    audience = Column(String(20), nullable=False, default="private", server_default="private")

    __table_args__ = (
        UniqueConstraint("user_id", "resource_type", "resource_id", "field_name", name="uq_career_field_visibility"),
        Index("ix_career_field_visibility_resource", "resource_type", "resource_id"),
    )


class ResumeImportJob(Base):
    __tablename__ = "resume_import_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    storage_key = Column(String(500), nullable=False)
    original_filename = Column(String(255), nullable=False)
    format = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="queued", server_default="queued")
    error = Column(Text, nullable=True)
    analysis = Column(JSONB, nullable=True)  # free analysis summary
    lease_owner = Column(String(80), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    items = relationship("ResumeExtractedItem", back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_resume_import_jobs_user", "user_id"),
        Index("ix_resume_import_jobs_claim", "status", "lease_until"),
    )


class ResumeExtractedItem(Base):
    __tablename__ = "resume_extracted_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("resume_import_jobs.id", ondelete="CASCADE"), nullable=False)
    item_type = Column(String(40), nullable=False)  # experience | education | certification | skill | profile
    field_path = Column(String(120), nullable=False)
    extracted_value = Column(JSONB, nullable=False)
    normalized_value = Column(JSONB, nullable=True)
    source_location = Column(String(200), nullable=True)
    confidence = Column(String(20), nullable=False, default="medium", server_default="medium")
    review_state = Column(String(20), nullable=False, default="pending_review", server_default="pending_review")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship("ResumeImportJob", back_populates="items")

    __table_args__ = (Index("ix_resume_extracted_items_job", "job_id"),)
