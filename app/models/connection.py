import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Index, UniqueConstraint, func as sa_func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base

class Connection(Base):
    """One row per pair of users. Withdrawing or removing deletes the row; declined rows stay for the re-request cooldown.
    Rows with the legacy status 'removed' count as no connection."""
    __tablename__ = "connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    receiver_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(32), default="pending", nullable=False)  # pending, accepted, declined (legacy: removed)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    accepted_at = Column(DateTime(timezone=True), nullable=True)

    requester = relationship("User", foreign_keys=[requester_id], back_populates="connections_sent")
    receiver = relationship("User", foreign_keys=[receiver_id], back_populates="connections_received")

    __table_args__ = (
        UniqueConstraint("requester_id", "receiver_id", name="uq_requester_receiver"),
        Index("ix_connections_receiver", "receiver_id"),
        Index("uq_connections_pair", sa_func.least(requester_id, receiver_id), sa_func.greatest(requester_id, receiver_id), unique=True),
    )
