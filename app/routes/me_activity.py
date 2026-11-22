"""Private My Activity: likes, comments, and saves for the signed-in user."""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.content_save import ContentSave
from app.models.network import ActivityComment, ActivityLike, NetworkActivity
from app.models.project import Project
from app.models.project_comment import ProjectComment
from app.models.project_like import ProjectLike
from app.models.user import User
from app.services.social import display_name

router = APIRouter(prefix="/me", tags=["My activity"])


@router.get("/activity")
def my_activity(
    tab: Literal["liked", "comments", "saved"] = Query("liked"),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    items: list[dict] = []
    if tab == "liked":
        likes = (
            db.query(ActivityLike)
            .options(selectinload(ActivityLike.activity).selectinload(NetworkActivity.user))
            .filter(ActivityLike.user_id == me.id)
            .order_by(ActivityLike.created_at.desc())
            .limit(limit)
            .all()
        )
        for like in likes:
            act = like.activity
            if not act:
                continue
            items.append(
                {
                    "kind": "activity_like",
                    "id": str(like.id),
                    "created_at": like.created_at.isoformat() if like.created_at else None,
                    "title": act.title or f"Post by {display_name(act.user) if act.user else 'someone'}",
                    "preview": (act.content or "")[:180] or None,
                    "href": f"/network/post/{act.id}",
                }
            )
        proj_likes = (
            db.query(ProjectLike, Project)
            .join(Project, Project.id == ProjectLike.project_id)
            .filter(ProjectLike.user_id == me.id)
            .order_by(ProjectLike.created_at.desc())
            .limit(limit)
            .all()
        )
        for like, p in proj_likes:
            items.append(
                {
                    "kind": "project_like",
                    "id": str(like.id),
                    "created_at": like.created_at.isoformat() if like.created_at else None,
                    "title": p.title,
                    "preview": p.short_description,
                    "href": f"/project/{p.id}",
                }
            )
    elif tab == "comments":
        comments = (
            db.query(ActivityComment)
            .options(selectinload(ActivityComment.activity))
            .filter(ActivityComment.author_id == me.id)
            .order_by(ActivityComment.created_at.desc())
            .limit(limit)
            .all()
        )
        for c in comments:
            act = c.activity
            items.append(
                {
                    "kind": "activity_comment",
                    "id": str(c.id),
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "title": "Comment on a network post",
                    "preview": c.comment_text[:180],
                    "href": f"/network/post/{act.id}?comment={c.id}" if act else "/network",
                }
            )
        pcomments = (
            db.query(ProjectComment)
            .filter(ProjectComment.author_id == me.id)
            .order_by(ProjectComment.created_at.desc())
            .limit(limit)
            .all()
        )
        for c in pcomments:
            items.append(
                {
                    "kind": "project_comment",
                    "id": str(c.id),
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "title": "Comment on a project",
                    "preview": (c.body or "")[:180],
                    "href": f"/project/{c.project_id}",
                }
            )
    else:
        saves = (
            db.query(ContentSave)
            .filter(ContentSave.user_id == me.id)
            .order_by(ContentSave.created_at.desc())
            .limit(limit)
            .all()
        )
        for s in saves:
            if s.target_type == "activity":
                act = db.query(NetworkActivity).filter(NetworkActivity.id == s.target_id).first()
                items.append(
                    {
                        "kind": "saved_activity",
                        "id": str(s.id),
                        "created_at": s.created_at.isoformat() if s.created_at else None,
                        "title": (act.title if act else None) or "Saved post",
                        "preview": (act.content if act else "")[:180] or None,
                        "href": f"/network/post/{s.target_id}",
                    }
                )
            else:
                p = db.query(Project).filter(Project.id == s.target_id).first()
                items.append(
                    {
                        "kind": "saved_project",
                        "id": str(s.id),
                        "created_at": s.created_at.isoformat() if s.created_at else None,
                        "title": p.title if p else "Saved project",
                        "preview": p.short_description if p else None,
                        "href": f"/project/{s.target_id}",
                    }
                )

    items.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {"tab": tab, "items": items[:limit]}
