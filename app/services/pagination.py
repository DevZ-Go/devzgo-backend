"""Keyset pagination on (created_at, id), newest first. Stable while rows are added, unlike offsets."""

import base64
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import tuple_


def _encode(row) -> str:
    return base64.urlsafe_b64encode(f"{row.created_at.isoformat()}|{row.id}".encode()).decode()


def _decode(page: str) -> tuple[datetime, UUID]:
    try:
        ts, id_ = base64.urlsafe_b64decode(page.encode()).decode().split("|")
        return datetime.fromisoformat(ts), UUID(id_)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid page token")


def paginate(query, model, page: str | None, limit: int):
    """Returns (rows, next_page); next_page is None on the last page."""
    if page:
        query = query.filter(tuple_(model.created_at, model.id) < _decode(page))
    rows = query.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1).all()
    return rows[:limit], (_encode(rows[limit - 1]) if len(rows) > limit else None)
