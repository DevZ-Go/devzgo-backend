"""Network clubs / student organizations (distinct from chat rooms)."""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

# president | vice_president | treasurer | executive | member
CLUB_ROLES = ("president", "vice_president", "treasurer", "executive", "member")
EXEC_ROLES = ("president", "vice_president", "treasurer", "executive")


class NetworkClub(Base):
    __tablename__ = "network_clubs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(120), nullable=False, unique=True)
    name = Column(String(120), nullable=False)
    tagline = Column(String(280), nullable=False, default="", server_default="")
    about = Column(Text, nullable=False, default="", server_default="")
    category = Column(String(40), nullable=False, default="general", server_default="general")
    banner_url = Column(Text, nullable=True)
    avatar_url = Column(Text, nullable=True)
    tags = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    policies = Column(Text, nullable=False, default="", server_default="")
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    members = relationship("ClubMember", back_populates="club", cascade="all, delete-orphan")
    custom_roles = relationship("ClubCustomRole", back_populates="club", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_network_clubs_category", "category", "created_at"),)


class ClubMember(Base):
    __tablename__ = "club_members"

    club_id = Column(UUID(as_uuid=True), ForeignKey("network_clubs.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(32), nullable=False, default="member", server_default="member")
    custom_title = Column(String(120), nullable=True)
    joined_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    club = relationship("NetworkClub", back_populates="members")
    user = relationship("User")

    __table_args__ = (Index("ix_club_members_user", "user_id", "joined_at"),)


class ClubCustomRole(Base):
    """Club-defined executive titles (beyond the built-in roles)."""

    __tablename__ = "club_custom_roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id = Column(UUID(as_uuid=True), ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(80), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    club = relationship("NetworkClub", back_populates="custom_roles")

    __table_args__ = (UniqueConstraint("club_id", "title", name="uq_club_custom_role_title"),)
