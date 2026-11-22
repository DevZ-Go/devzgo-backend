"""Club project showcases (Devpost-style)."""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.db.session import Base


class ClubShowcase(Base):
    __tablename__ = "club_showcases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id = Column(UUID(as_uuid=True), ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(200), nullable=False)
    tagline = Column(String(280), nullable=False, default="", server_default="")
    description = Column(Text, nullable=False, default="", server_default="")
    how_we_built = Column(Text, nullable=True)
    challenges = Column(Text, nullable=True)
    kind = Column(String(40), nullable=False, default="software", server_default="software")
    sub_category = Column(String(120), nullable=True)
    cover_url = Column(Text, nullable=True)
    tech_stacks = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    github_url = Column(Text, nullable=True)
    live_url = Column(Text, nullable=True)
    hardware_cad_url = Column(Text, nullable=True)
    embedded_specs = Column(JSONB, nullable=True)
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_club_showcases_club_created", "club_id", "created_at"),)
