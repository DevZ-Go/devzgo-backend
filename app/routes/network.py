import os
from collections import defaultdict
from typing import Dict, List, Optional, Set
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.network import USER_POST_TYPES, ActivityComment, ActivityLike, NetworkActivity
from app.models.project import Project
from app.models.user import User
from app.realtime.broadcaster import queue_event
from app.routes.connections import partner_ids
from app.schemas.network import (
    ActivityCommentCreate,
    ActivityCommentPage,
    ActivityCommentResponse,
    ActivityLikeResponse,
    FeedActivityResponse,
    FeedPage,
    FeedPostCreate,
    FeedPostUpdate,
    ProjectPreview,
)
from app.services.pagination import paginate
from app.services.social import display_name, notify, project_access, project_stats, visible_project_filter

router = APIRouter(prefix="/network", tags=["Network"])

_EAGER = (
    selectinload(NetworkActivity.user).selectinload(User.profile),
    selectinload(NetworkActivity.project).selectinload(Project.tech_stacks),
    selectinload(NetworkActivity.project).selectinload(Project.owner),
)
_FILTERS = {
    "projects": lambda: or_(NetworkActivity.activity_type.in_(["project_added", "project_analyzed", "project_completed"]),
                            NetworkActivity.project_id.isnot(None)),
    "developers": lambda: NetworkActivity.activity_type.in_(["profile_updated", "wrap_up_published", *USER_POST_TYPES]),
    "collaboration": lambda: NetworkActivity.activity_type.in_(["collaboration", "collab_call"]),
}


def _visible(viewer_id: Optional[UUID]):
    """Activities about a project are only shown to people who may see that project."""
    return or_(NetworkActivity.project_id.is_(None), NetworkActivity.project.has(visible_project_filter(viewer_id)))


def _activity(db: Session, activity_id: UUID, viewer_id: Optional[UUID]) -> NetworkActivity:
    act = db.query(NetworkActivity).options(*_EAGER).filter(NetworkActivity.id == activity_id, _visible(viewer_id)).first()
    if not act:
        raise HTTPException(status_code=404, detail="Activity not found")
    return act


def _own_post(db: Session, activity_id: UUID, me: User) -> NetworkActivity:
    act = _activity(db, activity_id, me.id)
    if act.user_id != me.id or act.activity_type not in USER_POST_TYPES:
        raise HTTPException(status_code=403, detail="You can only change your own posts")
    return act


def _audience(db: Session, author_id: UUID) -> Set[UUID]:
    return partner_ids(db, author_id) | {author_id}


def _comment_response(c: ActivityComment, viewer_id: Optional[UUID], post_author_id: UUID) -> ActivityCommentResponse:
    return ActivityCommentResponse(
        id=c.id, activity_id=c.activity_id, author_id=c.author_id, author_name=display_name(c.author),
        author_username=c.author.username, author_avatar=c.author.profile.avatar_url if c.author.profile else None,
        comment_text=c.comment_text, created_at=c.created_at,
        can_delete=viewer_id is not None and viewer_id in (c.author_id, post_author_id),
    )


def _preview(p: Project, stats: dict) -> ProjectPreview:
    return ProjectPreview(
        **stats.get(p.id, {}), id=p.id, title=p.title, short_description=p.short_description,
        cover_image_url=p.cover_image_url, tech_stacks=[t.name for t in p.tech_stacks],
        category=str(p.category.value if hasattr(p.category, "value") else p.category), github_url=p.github_url,
        complexity=p.complexity, contribution_info=p.contribution_info, owner_username=p.owner.username if p.owner else None,
    )


def _grouped_counts(db: Session, model, ids) -> Dict[UUID, int]:
    return dict(db.query(model.activity_id, func.count()).filter(model.activity_id.in_(ids)).group_by(model.activity_id).all())


def _format_many(db: Session, acts: List[NetworkActivity], viewer_id: Optional[UUID]) -> List[FeedActivityResponse]:
    """Counts, the viewer's likes, the latest two comments and project stats for a page, in a handful of queries."""
    ids = [a.id for a in acts]
    if not ids:
        return []
    likes, comments = _grouped_counts(db, ActivityLike, ids), _grouped_counts(db, ActivityComment, ids)
    liked = {r[0] for r in db.query(ActivityLike.activity_id).filter(
        ActivityLike.activity_id.in_(ids), ActivityLike.user_id == viewer_id)} if viewer_id else set()
    rank = func.row_number().over(partition_by=ActivityComment.activity_id,
                                  order_by=(ActivityComment.created_at.desc(), ActivityComment.id.desc())).label("rank")
    latest = db.query(ActivityComment.id, rank).filter(ActivityComment.activity_id.in_(ids)).subquery()
    previews = defaultdict(list)
    for c in (db.query(ActivityComment).options(selectinload(ActivityComment.author).selectinload(User.profile))
              .join(latest, latest.c.id == ActivityComment.id).filter(latest.c.rank <= 2)
              .order_by(ActivityComment.created_at, ActivityComment.id)):
        previews[c.activity_id].append(c)
    stats = project_stats(db, [a.project_id for a in acts if a.project_id], viewer_id)

    return [FeedActivityResponse(
        id=a.id, user_id=a.user_id, author_name=display_name(a.user), author_username=a.user.username,
        author_avatar=a.user.profile.avatar_url if a.user.profile else None,
        author_headline=a.user.profile.headline if a.user.profile else None,
        activity_type=a.activity_type, title=a.title, content=a.content, media_url=a.media_url, is_demo=a.is_demo,
        created_at=a.created_at, project=_preview(a.project, stats) if a.project else None,
        likes_count=likes.get(a.id, 0), is_liked=a.id in liked, comments_count=comments.get(a.id, 0),
        comments=[_comment_response(c, viewer_id, a.user_id) for c in previews[a.id]],
        can_edit=viewer_id == a.user_id and a.activity_type in USER_POST_TYPES,
    ) for a in acts]


@router.get("/feed", response_model=FeedPage)
def get_feed(
    filter: str = Query("all", pattern="^(all|projects|developers|collaboration|following)$"),
    search: Optional[str] = Query(None, max_length=100),
    user_id: Optional[UUID] = None,
    page: Optional[str] = None,
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    viewer_id = current_user.id if current_user else None
    query = db.query(NetworkActivity).options(*_EAGER).filter(_visible(viewer_id))
    if filter in _FILTERS:
        query = query.filter(_FILTERS[filter]())
    elif filter == "following":
        if not current_user:
            return {"items": [], "next_page": None}
        query = query.filter(NetworkActivity.user_id.in_(_audience(db, viewer_id)))
    if user_id:
        query = query.filter(NetworkActivity.user_id == user_id)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.outerjoin(NetworkActivity.project).join(NetworkActivity.user).filter(or_(
            NetworkActivity.title.ilike(term), NetworkActivity.content.ilike(term),
            User.username.ilike(term), Project.title.ilike(term)))
    rows, next_page = paginate(query, NetworkActivity, page, limit)
    return {"items": _format_many(db, rows, viewer_id), "next_page": next_page}


@router.post("/posts", response_model=FeedActivityResponse, dependencies=[rate_limit("post", 10, 60)])
def create_feed_post(post_data: FeedPostCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    content = post_data.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Post cannot be empty")
    if post_data.project_id:
        project = db.query(Project).filter(Project.id == post_data.project_id).first()
        access = project_access(db, project, current_user.id) if project else {}
        if not (access.get("owner") or access.get("member")):
            raise HTTPException(status_code=403, detail="You can only post about projects you own or collaborate on")

    act = NetworkActivity(user_id=current_user.id, activity_type=post_data.activity_type, title=post_data.title,
                          content=content, project_id=post_data.project_id, media_url=post_data.media_url, is_demo=False)
    db.add(act)
    db.flush()
    for uid in _audience(db, current_user.id):
        queue_event(db, uid, {"type": "feed.new", "activity_id": str(act.id)})
    db.commit()
    db.refresh(act)
    return _format_many(db, [act], current_user.id)[0]


@router.get("/activities/{activity_id}", response_model=FeedActivityResponse)
def get_activity(activity_id: UUID, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user_optional)):
    viewer_id = current_user.id if current_user else None
    return _format_many(db, [_activity(db, activity_id, viewer_id)], viewer_id)[0]


@router.patch("/activities/{activity_id}", response_model=FeedActivityResponse)
def edit_post(activity_id: UUID, data: FeedPostUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    act = _own_post(db, activity_id, current_user)
    changes = data.model_dump(exclude_unset=True)
    if "content" in changes:
        if not (changes["content"] or "").strip():
            raise HTTPException(status_code=400, detail="Post cannot be empty")
        act.content = changes["content"].strip()
    if "title" in changes:
        act.title = (changes["title"] or "").strip() or None
    for uid in _audience(db, current_user.id):
        queue_event(db, uid, {"type": "feed.updated", "activity_id": str(act.id)})
    db.commit()
    return _format_many(db, [act], current_user.id)[0]


@router.delete("/activities/{activity_id}")
def delete_post(activity_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    act = _own_post(db, activity_id, current_user)
    for uid in _audience(db, current_user.id):
        queue_event(db, uid, {"type": "feed.deleted", "activity_id": str(act.id)})
    db.delete(act)
    db.commit()
    return {"message": "Post deleted"}


@router.post("/activities/{activity_id}/like", response_model=ActivityLikeResponse, dependencies=[rate_limit("like", 60, 60)])
def toggle_activity_like(activity_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    activity = _activity(db, activity_id, current_user.id)
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


@router.get("/activities/{activity_id}/comments", response_model=ActivityCommentPage)
def list_activity_comments(activity_id: UUID, page: Optional[str] = None, limit: int = Query(20, ge=1, le=50),
                           db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user_optional)):
    viewer_id = current_user.id if current_user else None
    act = _activity(db, activity_id, viewer_id)
    query = db.query(ActivityComment).options(selectinload(ActivityComment.author).selectinload(User.profile)).filter(
        ActivityComment.activity_id == activity_id)
    rows, next_page = paginate(query, ActivityComment, page, limit)
    return {"items": [_comment_response(c, viewer_id, act.user_id) for c in rows], "next_page": next_page}


@router.post("/activities/{activity_id}/comments", response_model=ActivityCommentResponse, dependencies=[rate_limit("comment", 20, 60)])
def add_activity_comment(activity_id: UUID, comment_data: ActivityCommentCreate, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    activity = _activity(db, activity_id, current_user.id)
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
    return _comment_response(comment, current_user.id, activity.user_id)


@router.delete("/comments/{comment_id}")
def delete_activity_comment(comment_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    comment = db.query(ActivityComment).filter(ActivityComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if current_user.id not in (comment.author_id, comment.activity.user_id):
        raise HTTPException(status_code=403, detail="Only the comment's author or the post's author can delete it")
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
