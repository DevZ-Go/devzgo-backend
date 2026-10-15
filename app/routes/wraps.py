"""Monthly Wrap. The current month is computed live; finished months come only from stored snapshots."""

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.errors import NotFound
from app.db.session import get_db
from app.models.monthly_wrap import MonthlyWrap
from app.models.network import NetworkActivity
from app.models.user import User
from app.realtime.broadcaster import queue_event
from app.services.wrap import (
    HEADLINE,
    compute_metrics,
    current_month,
    is_empty,
    month_bounds,
    persona,
    user_timezone,
)

router = APIRouter(prefix="/wraps", tags=["Monthly Wrap"])
MONTH = Path(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="yyyy-mm")


class WrapResponse(BaseModel):
    month: str
    status: str  # in_progress | completed
    period: dict
    as_of: Optional[datetime] = None
    generated_at: Optional[datetime] = None
    metrics: dict
    empty: bool
    persona: Optional[dict] = None
    shared: bool = False


def _label(month: date) -> str:
    return month.strftime("%Y-%m")


def _period(month: date, tz: str) -> dict:
    start, end = month_bounds(month, tz)
    return {"start": start, "end": end, "timezone": tz}


def _stored(w: MonthlyWrap) -> WrapResponse:
    return WrapResponse(month=_label(w.month), status="completed", period=_period(w.month, w.timezone),
                        generated_at=w.generated_at, metrics=w.metrics, empty=is_empty(w.metrics),
                        persona=w.persona, shared=w.shared_activity_id is not None)


def _snapshot(db: Session, me: User, month: str) -> MonthlyWrap:
    wanted = date(int(month[:4]), int(month[5:]), 1)
    tz = user_timezone(me)
    if wanted >= current_month(tz):
        raise NotFound("not_finished", "This month is not over yet. See /wraps/current.")
    wrap = db.query(MonthlyWrap).filter_by(user_id=me.id, month=wanted).first()
    if wrap is None:
        raise NotFound("not_ready", "Your wrap for this month is being prepared.")
    return wrap


@router.get("/current", response_model=WrapResponse)
def current_wrap(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    tz = user_timezone(me)
    month = current_month(tz)
    start, end = month_bounds(month, tz)
    metrics = compute_metrics(db, me.id, start, end)
    return WrapResponse(month=_label(month), status="in_progress", period=_period(month, tz),
                        as_of=datetime.now(timezone.utc), metrics=metrics, empty=is_empty(metrics), persona=persona(metrics))


@router.get("")
def list_wraps(limit: int = Query(12, ge=1, le=36), db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    rows = db.query(MonthlyWrap).filter_by(user_id=me.id).order_by(MonthlyWrap.month.desc()).limit(limit).all()
    return {"items": [{"month": _label(w.month), "status": "completed", "generated_at": w.generated_at,
                       "headline_metrics": {k: w.metrics.get(k) for k in HEADLINE}, "persona": w.persona} for w in rows]}


@router.get("/{month}", response_model=WrapResponse)
def get_wrap(month: str = MONTH, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    return _stored(_snapshot(db, me, month))


@router.post("/{month}/share")
def share_wrap(month: str = MONTH, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    """Opt-in: post a finished month to the feed once. Sharing again returns the same post."""
    wrap = _snapshot(db, me, month)
    if wrap.shared_activity_id:
        return {"activity_id": wrap.shared_activity_id, "created": False}
    m = wrap.metrics
    parts = [f"{m['projects_created']} new project(s)", f"{m['connections_made']} new connection(s)",
             f"{m['posts_published']} post(s)"]
    title = f"My {wrap.month.strftime('%B %Y')} on DevZ-Go" + (f": {wrap.persona['label']}" if wrap.persona else "")
    activity = NetworkActivity(user_id=me.id, activity_type="wrap_up_published", title=title[:255], content=", ".join(parts) + ".")
    db.add(activity)
    db.flush()
    wrap.shared_activity_id = activity.id
    queue_event(db, me.id, {"type": "feed.new", "activity_id": str(activity.id)})
    db.commit()
    return {"activity_id": activity.id, "created": True}
