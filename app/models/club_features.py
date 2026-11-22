"""Club events, announcements, polls, funds, galleries, and role permissions."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

DEFAULT_PERMS = {
    "manage_roles": False,
    "manage_members": False,
    "events": False,
    "announcements": False,
    "media": False,
    "polls": False,
    "galleries": False,
    "settings": False,
    "funds": False,
    "broadcast_email": False,
}

PRESIDENT_PERMS = {k: True for k in DEFAULT_PERMS}


class ClubRolePermission(Base):
    __tablename__ = "club_role_permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id = Column(UUID(as_uuid=True), ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(32), nullable=False)
    permissions = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("club_id", "role", name="uq_club_role_permissions"),
        Index("ix_club_role_permissions_club", "club_id"),
    )


class ClubEvent(Base):
    __tablename__ = "club_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id = Column(UUID(as_uuid=True), ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="", server_default="")
    location = Column(String(280), nullable=True)
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    timezone = Column(String(64), nullable=True)
    modality = Column(String(20), nullable=False, default="in_person", server_default="in_person")
    venue_name = Column(String(200), nullable=True)
    venue_address = Column(Text, nullable=True)
    room_number = Column(String(80), nullable=True)
    meeting_url = Column(Text, nullable=True)
    meeting_platform = Column(String(80), nullable=True)
    poster_url = Column(Text, nullable=True)
    poster_format = Column(String(20), nullable=True)
    poster_orientation = Column(String(20), nullable=True)
    rsvp_url = Column(Text, nullable=True)
    # Free food, swag, prizes — highlighted on event cards
    attractions = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="draft", server_default="draft")
    academic_term = Column(String(40), nullable=True)
    academic_year = Column(String(20), nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    media = relationship("ClubEventMedia", back_populates="event", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_club_events_club_starts", "club_id", "starts_at"),)


class ClubAnnouncement(Base):
    __tablename__ = "club_announcements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id = Column(UUID(as_uuid=True), ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False, default="", server_default="")
    media_urls = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    badge = Column(String(60), nullable=False, default="General Update", server_default="General Update")
    pinned = Column(Boolean, nullable=False, default=False, server_default="false")
    status = Column(String(20), nullable=False, default="published", server_default="published")
    # public | members — who can read when published
    audience = Column(String(20), nullable=False, default="members", server_default="members")
    event_id = Column(UUID(as_uuid=True), ForeignKey("club_events.id", ondelete="SET NULL"), nullable=True)
    author_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    polls = relationship("ClubPoll", back_populates="announcement", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_club_announcements_club_created", "club_id", "created_at"),)


class ClubPoll(Base):
    __tablename__ = "club_polls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id = Column(UUID(as_uuid=True), ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False)
    announcement_id = Column(
        UUID(as_uuid=True), ForeignKey("club_announcements.id", ondelete="CASCADE"), nullable=True
    )
    question = Column(String(400), nullable=False)
    status = Column(String(20), nullable=False, default="open", server_default="open")
    closes_at = Column(DateTime(timezone=True), nullable=True)
    allow_vote_change = Column(Boolean, nullable=False, default=True, server_default="true")
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    announcement = relationship("ClubAnnouncement", back_populates="polls")
    options = relationship("ClubPollOption", back_populates="poll", cascade="all, delete-orphan")
    votes = relationship("ClubPollVote", back_populates="poll", cascade="all, delete-orphan")


class ClubPollOption(Base):
    __tablename__ = "club_poll_options"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    poll_id = Column(UUID(as_uuid=True), ForeignKey("club_polls.id", ondelete="CASCADE"), nullable=False)
    label = Column(String(200), nullable=False)
    sort_order = Column(String(10), nullable=False, default="0", server_default="0")

    poll = relationship("ClubPoll", back_populates="options")
    votes = relationship("ClubPollVote", back_populates="option", cascade="all, delete-orphan")


class ClubPollVote(Base):
    __tablename__ = "club_poll_votes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    poll_id = Column(UUID(as_uuid=True), ForeignKey("club_polls.id", ondelete="CASCADE"), nullable=False)
    option_id = Column(UUID(as_uuid=True), ForeignKey("club_poll_options.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    poll = relationship("ClubPoll", back_populates="votes")
    option = relationship("ClubPollOption", back_populates="votes")

    __table_args__ = (UniqueConstraint("poll_id", "user_id", name="uq_club_poll_vote_member"),)


class ClubFundEntry(Base):
    __tablename__ = "club_fund_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id = Column(UUID(as_uuid=True), ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(8), nullable=False, default="CAD", server_default="CAD")
    note = Column(Text, nullable=False, default="", server_default="")
    entry_type = Column(String(20), nullable=False, default="expense", server_default="expense")
    public_disclosure = Column(Boolean, nullable=False, default=False, server_default="false")
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_club_fund_entries_club", "club_id", "created_at"),)


class ClubEventMedia(Base):
    __tablename__ = "club_event_media"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("club_events.id", ondelete="CASCADE"), nullable=False)
    club_id = Column(UUID(as_uuid=True), ForeignKey("network_clubs.id", ondelete="CASCADE"), nullable=False)
    url = Column(Text, nullable=False)
    media_type = Column(String(40), nullable=False, default="image", server_default="image")
    academic_term = Column(String(40), nullable=True)
    academic_year = Column(String(20), nullable=True)
    uploaded_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    event = relationship("ClubEvent", back_populates="media")

    __table_args__ = (Index("ix_club_event_media_club_term", "club_id", "academic_term", "academic_year"),)
