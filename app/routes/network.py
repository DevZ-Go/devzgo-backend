from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.profile import UserProfile
from app.models.project import Project
from app.models.network import NetworkActivity, ActivityLike, ActivityComment
from app.models.connection import Connection
from app.models.notification import Notification
from app.core.dependencies import get_current_user, get_current_user_optional
from app.schemas.network import (
    FeedActivityResponse,
    FeedPostCreate,
    ActivityCommentCreate,
    ActivityCommentResponse,
    ActivityLikeResponse,
    ProjectPreview,
)

router = APIRouter(prefix="/network", tags=["Network"])

def _format_author_info(user: User):
    full_name = user.profile.full_name if user.profile and user.profile.full_name else user.username
    headline = user.profile.headline if user.profile else None
    avatar_url = user.profile.avatar_url if user.profile else None
    return full_name, headline, avatar_url

def _format_activity(act: NetworkActivity, viewer_id: Optional[UUID] = None) -> FeedActivityResponse:
    author_name, author_headline, author_avatar = _format_author_info(act.user)
    
    # Project preview
    project_preview = None
    if act.project:
        p = act.project
        techs = [t.name for t in p.tech_stacks] if p.tech_stacks else []
        project_preview = ProjectPreview(
            id=p.id,
            title=p.title,
            short_description=p.short_description,
            cover_image_url=p.cover_image_url,
            tech_stacks=techs,
            category=str(p.category.value if hasattr(p.category, "value") else p.category),
            github_url=p.github_url,
            complexity=p.complexity,
            contribution_info=p.contribution_info,
            owner_username=p.owner.username if p.owner else None,
        )

    # Likes & comments
    likes_count = len(act.likes)
    is_liked = any(like.user_id == viewer_id for like in act.likes) if viewer_id else False
    
    comments = []
    for c in act.comments:
        c_name, _, c_avatar = _format_author_info(c.author)
        comments.append(ActivityCommentResponse(
            id=c.id,
            activity_id=c.activity_id,
            author_id=c.author_id,
            author_name=c_name,
            author_username=c.author.username,
            author_avatar=c_avatar,
            comment_text=c.comment_text,
            created_at=c.created_at,
        ))

    return FeedActivityResponse(
        id=act.id,
        user_id=act.user_id,
        author_name=author_name,
        author_username=act.user.username,
        author_avatar=author_avatar,
        author_headline=author_headline,
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

@router.get("/feed", response_model=List[FeedActivityResponse])
def get_feed(
    filter: str = Query("all", regex="^(all|projects|developers|collaboration|following)$"),
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    query = db.query(NetworkActivity).order_by(desc(NetworkActivity.created_at))

    # Apply category filter
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
        # Get user's accepted connections
        connections = db.query(Connection).filter(
            or_(
                Connection.requester_id == current_user.id,
                Connection.receiver_id == current_user.id,
            ),
            Connection.status == "accepted",
        ).all()
        friend_ids = {
            c.receiver_id if c.requester_id == current_user.id else c.requester_id
            for c in connections
        }
        friend_ids.add(current_user.id)
        query = query.filter(NetworkActivity.user_id.in_(friend_ids))

    # Apply search filter
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.outerjoin(NetworkActivity.project).outerjoin(NetworkActivity.user).filter(
            or_(
                NetworkActivity.title.ilike(term),
                NetworkActivity.content.ilike(term),
                User.username.ilike(term),
                Project.title.ilike(term),
            )
        )

    activities = query.limit(limit).all()
    viewer_id = current_user.id if current_user else None
    return [_format_activity(act, viewer_id) for act in activities]

@router.post("/posts", response_model=FeedActivityResponse)
def create_feed_post(
    post_data: FeedPostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    new_activity = NetworkActivity(
        user_id=current_user.id,
        activity_type=post_data.activity_type,
        title=post_data.title,
        content=post_data.content,
        project_id=post_data.project_id,
        media_url=post_data.media_url,
        is_demo=False,
    )
    db.add(new_activity)
    db.commit()
    db.refresh(new_activity)

    return _format_activity(new_activity, current_user.id)

@router.post("/activities/{activity_id}/like", response_model=ActivityLikeResponse)
def toggle_activity_like(
    activity_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    activity = db.query(NetworkActivity).filter(NetworkActivity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    existing_like = db.query(ActivityLike).filter(
        ActivityLike.activity_id == activity_id,
        ActivityLike.user_id == current_user.id,
    ).first()

    if existing_like:
        db.delete(existing_like)
        db.commit()
        likes_count = db.query(ActivityLike).filter(ActivityLike.activity_id == activity_id).count()
        return ActivityLikeResponse(activity_id=activity_id, likes_count=likes_count, is_liked=False)
    else:
        new_like = ActivityLike(activity_id=activity_id, user_id=current_user.id)
        db.add(new_like)

        # Notify activity owner
        if activity.user_id != current_user.id:
            author_name = current_user.profile.full_name if current_user.profile and current_user.profile.full_name else current_user.username
            notif = Notification(
                recipient_id=activity.user_id,
                actor_id=current_user.id,
                type="like",
                related_id=str(activity.id),
                message=f"{author_name} liked your activity.",
            )
            db.add(notif)

        db.commit()
        likes_count = db.query(ActivityLike).filter(ActivityLike.activity_id == activity_id).count()
        return ActivityLikeResponse(activity_id=activity_id, likes_count=likes_count, is_liked=True)

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

    if not comment_data.comment_text.strip():
        raise HTTPException(status_code=400, detail="Comment cannot be empty")

    comment = ActivityComment(
        activity_id=activity_id,
        author_id=current_user.id,
        comment_text=comment_data.comment_text.strip(),
    )
    db.add(comment)

    # Notify activity owner
    if activity.user_id != current_user.id:
        author_name = current_user.profile.full_name if current_user.profile and current_user.profile.full_name else current_user.username
        notif = Notification(
            recipient_id=activity.user_id,
            actor_id=current_user.id,
            type="comment",
            related_id=str(activity.id),
            message=f"{author_name} commented: \"{comment.comment_text[:40]}...\"",
        )
        db.add(notif)

    db.commit()
    db.refresh(comment)

    author_name, _, author_avatar = _format_author_info(current_user)
    return ActivityCommentResponse(
        id=comment.id,
        activity_id=comment.activity_id,
        author_id=comment.author_id,
        author_name=author_name,
        author_username=current_user.username,
        author_avatar=author_avatar,
        comment_text=comment.comment_text,
        created_at=comment.created_at,
    )

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

@router.post("/seed-demo")
def trigger_seed_demo():
    from app.scripts.seed_demo_network import seed_demo_data
    seed_demo_data()
    return {"message": "Demo data seeded successfully"}

