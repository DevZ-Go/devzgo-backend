import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, desc, func
from typing import Dict, List, Optional, Set
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.project import Project
from app.models.network import NetworkActivity, ActivityLike, ActivityComment
from app.models.connection import Connection
from app.core.dependencies import get_current_user, get_current_user_optional
from app.services.social import display_name, notify, project_stats, visible_project_filter
from app.schemas.network import (
    FeedActivityResponse,
    FeedPostCreate,
    ActivityCommentCreate,
    ActivityCommentResponse,
    ActivityLikeResponse,
    ProjectPreview,
)

router = APIRouter(prefix="/network", tags=["Network"])

_EAGER = (
    selectinload(NetworkActivity.user).selectinload(User.profile),
    selectinload(NetworkActivity.project).selectinload(Project.tech_stacks),
    selectinload(NetworkActivity.project).selectinload(Project.owner),
    selectinload(NetworkActivity.comments).selectinload(ActivityComment.author).selectinload(User.profile),
)


def _comment_response(c: ActivityComment) -> ActivityCommentResponse:
    return ActivityCommentResponse(
        id=c.id,
        activity_id=c.activity_id,
        author_id=c.author_id,
        author_name=display_name(c.author),
        author_username=c.author.username,
        author_avatar=c.author.profile.avatar_url if c.author.profile else None,
        comment_text=c.comment_text,
        created_at=c.created_at,
    )


def _format_activity(act: NetworkActivity, likes_count: int, is_liked: bool, project_stats_: dict) -> FeedActivityResponse:
    p = act.project
    project_preview = ProjectPreview(
        **project_stats_.get(p.id, {}) if p else {},
        id=p.id,
        title=p.title,
        short_description=p.short_description,
        cover_image_url=p.cover_image_url,
        tech_stacks=[t.name for t in p.tech_stacks],
        category=str(p.category.value if hasattr(p.category, "value") else p.category),
        github_url=p.github_url,
        complexity=p.complexity,
        contribution_info=p.contribution_info,
        owner_username=p.owner.username if p.owner else None,
    ) if p else None
    comments = [_comment_response(c) for c in act.comments]
    return FeedActivityResponse(
        id=act.id,
        user_id=act.user_id,
        author_name=display_name(act.user),
        author_username=act.user.username,
        author_avatar=act.user.profile.avatar_url if act.user.profile else None,
        author_headline=act.user.profile.headline if act.user.profile else None,
        activity_type=act.activity_type,
        title=act.title,
        content=act.content,
        media_url=act.media_url,
        is_demo=act.is_demo,
        created_at=act.created_at,
        project=project_preview,
        likes_count=likes_count,
        is_liked=is_liked,
        comments_count=len(comments),
        comments=comments,
    )


def _format_many(db: Session, acts: List[NetworkActivity], viewer_id: Optional[UUID]) -> List[FeedActivityResponse]:
    ids = [a.id for a in acts]
    counts: Dict[UUID, int] = dict(
        db.query(ActivityLike.activity_id, func.count()).filter(ActivityLike.activity_id.in_(ids))
        .group_by(ActivityLike.activity_id).all()
    ) if ids else {}
    liked: Set[UUID] = {
        r[0] for r in db.query(ActivityLike.activity_id)
        .filter(ActivityLike.activity_id.in_(ids), ActivityLike.user_id == viewer_id).all()
    } if ids and viewer_id else set()
    stats = project_stats(db, [a.project_id for a in acts if a.project_id], viewer_id)
    return [_format_activity(a, counts.get(a.id, 0), a.id in liked, stats) for a in acts]


@router.get("/feed", response_model=List[FeedActivityResponse])
def get_feed(
    filter: str = Query("all", pattern="^(all|projects|developers|collaboration|following)$"),
    search: Optional[str] = None,
    user_id: Optional[UUID] = None,
    before: Optional[datetime] = Query(None, description="Return activities older than this (pagination)"),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    viewer_id = current_user.id if current_user else None
    query = db.query(NetworkActivity).options(*_EAGER).filter(
        or_(NetworkActivity.project_id.is_(None), NetworkActivity.project.has(visible_project_filter(viewer_id)))
    )

    if filter == "projects":
        query = query.filter(
            or_(
                NetworkActivity.activity_type.in_(["project_added", "project_analyzed", "project_completed"]),
                NetworkActivity.project_id.isnot(None),
            )
        )
    elif filter == "developers":
        query = query.filter(
            NetworkActivity.activity_type.in_(["profile_updated", "wrap_up_published", "post"])
        )
    elif filter == "collaboration":
        query = query.filter(NetworkActivity.activity_type == "collaboration")
    elif filter == "following":
        if not current_user:
            return []
        connections = db.query(Connection.requester_id, Connection.receiver_id).filter(
            or_(Connection.requester_id == viewer_id, Connection.receiver_id == viewer_id),
            Connection.status == "accepted",
        ).all()
        friend_ids = {r if r != viewer_id else s for r, s in connections} | {viewer_id}
        query = query.filter(NetworkActivity.user_id.in_(friend_ids))

    if user_id:
        query = query.filter(NetworkActivity.user_id == user_id)
    if before:
        query = query.filter(NetworkActivity.created_at < before)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.outerjoin(NetworkActivity.project).join(NetworkActivity.user).filter(
            or_(
                NetworkActivity.title.ilike(term),
                NetworkActivity.content.ilike(term),
                User.username.ilike(term),
                Project.title.ilike(term),
            )
        )

    activities = query.order_by(desc(NetworkActivity.created_at)).limit(limit).all()
    return _format_many(db, activities, viewer_id)


@router.post("/posts", response_model=FeedActivityResponse)
def create_feed_post(
    post_data: FeedPostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    content = post_data.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Post cannot be empty")
    if post_data.project_id and not db.query(Project.id).filter(
        Project.id == post_data.project_id, Project.owner_id == current_user.id
    ).first():
        raise HTTPException(status_code=403, detail="You can only post about your own projects")

    new_activity = NetworkActivity(
        user_id=current_user.id,
        activity_type=post_data.activity_type,
        title=post_data.title,
        content=content,
        project_id=post_data.project_id,
        media_url=post_data.media_url,
        is_demo=False,
    )
    db.add(new_activity)
    db.commit()
    db.refresh(new_activity)
    return _format_many(db, [new_activity], current_user.id)[0]


@router.post("/activities/{activity_id}/like", response_model=ActivityLikeResponse)
def toggle_activity_like(
    activity_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    activity = db.query(NetworkActivity).filter(NetworkActivity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    existing_like = db.query(ActivityLike).filter_by(activity_id=activity_id, user_id=current_user.id).first()
    if existing_like:
        db.delete(existing_like)
        db.commit()
        is_liked = False
    else:
        try:
            db.add(ActivityLike(activity_id=activity_id, user_id=current_user.id))
            notify(db, activity.user_id, current_user, "like", str(activity.id),
                   f"{display_name(current_user)} liked your activity.", once=True)
            db.commit()
        except IntegrityError:
            # A parallel request (e.g. double click) already inserted the like.
            db.rollback()
        is_liked = True

    likes_count = db.query(ActivityLike).filter(ActivityLike.activity_id == activity_id).count()
    return ActivityLikeResponse(activity_id=activity_id, likes_count=likes_count, is_liked=is_liked)


@router.post("/activities/{activity_id}/comments", response_model=ActivityCommentResponse)
def add_activity_comment(
    activity_id: UUID,
    comment_data: ActivityCommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    activity = db.query(NetworkActivity).filter(NetworkActivity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    text = comment_data.comment_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Comment cannot be empty")

    comment = ActivityComment(activity_id=activity_id, author_id=current_user.id, comment_text=text)
    db.add(comment)
    snippet = text if len(text) <= 40 else text[:40] + "..."
    notify(db, activity.user_id, current_user, "comment", str(activity.id),
           f"{display_name(current_user)} commented: \"{snippet}\"")
    db.commit()
    db.refresh(comment)
    return _comment_response(comment)


@router.delete("/comments/{comment_id}")
def delete_activity_comment(
    comment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comment = db.query(ActivityComment).filter(ActivityComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own comments")

    db.delete(comment)
    db.commit()
    return {"message": "Comment deleted successfully"}


@router.post("/seed-demo", include_in_schema=False)
def trigger_seed_demo():
    if os.getenv("ALLOW_DEMO_SEED", "").lower() != "true":
        raise HTTPException(status_code=404, detail="Not Found")
    from app.scripts.seed_demo_network import seed_demo_data
    seed_demo_data()
    return {"message": "Demo data seeded successfully"}
