"""A signed-in person can leave a quiet note that they opened a page or chose a gallery direction."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.services.usage import event_for_path, event_for_scroll, record

router = APIRouter(prefix="/usage", tags=["Usage"])


class UsageNote(BaseModel):
    kind: str = Field(min_length=1, max_length=16)
    path: str | None = Field(default=None, max_length=180)
    mode: str | None = Field(default=None, max_length=16)


@router.post("", status_code=204)
def note(body: UsageNote, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.kind == "visit":
        record(db, user, event_for_path(body.path or ""))
    elif body.kind == "scroll":
        record(db, user, event_for_scroll(body.mode or ""))
    return None
