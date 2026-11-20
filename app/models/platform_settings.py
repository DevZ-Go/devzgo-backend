"""Key/value platform settings editable by admins (e.g. Project Intelligence free quota)."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.db.session import Base

PI_SETTINGS_KEY = "project_intelligence"


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key = Column(String(80), primary_key=True)
    value = Column(JSONB, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
