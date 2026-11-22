"""Cached Open Graph / link metadata for network posts."""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.session import Base


class LinkPreview(Base):
    __tablename__ = "link_previews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url = Column(String(1000), nullable=False, unique=True)
    normalized_url = Column(String(1000), nullable=False)
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    domain = Column(String(255), nullable=True)
    kind = Column(String(32), nullable=False, default="external", server_default="external")
    project_id = Column(UUID(as_uuid=True), nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_link_previews_normalized", "normalized_url"),)
