import uuid
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from app.db.session import Base


class MonthlyWrap(Base):
    """A finished month's snapshot, written once by app.scripts.generate_wraps and never recomputed."""
    __tablename__ = "monthly_wraps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    month = Column(Date, nullable=False)  # first day of the month, in `timezone`
    timezone = Column(String(64), nullable=False)
    metrics = Column(JSONB, nullable=False)
    persona = Column(JSONB, nullable=True)
    schema_version = Column(Integer, nullable=False, server_default="1")
    generated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    shared_activity_id = Column(UUID(as_uuid=True), ForeignKey("network_activities.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (UniqueConstraint("user_id", "month", name="uq_monthly_wraps_user_month"),)
