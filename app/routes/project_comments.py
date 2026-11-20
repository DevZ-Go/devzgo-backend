"""Comments on projects — threaded replies and likes."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.project import Project
from app.models.project_comment import ProjectComment, ProjectCommentLike
from app.models.user import User
from app.schemas.project import (
    ProjectCommentCreate,
    ProjectCommentLikeResponse,
    ProjectCommentPage,
    ProjectCommentResponse,
)
from app.services.pagination import paginate
from app.services.social import can_view_project, display_name, may_comment_on_project, notify, user_summary

router = APIRouter(prefix="/projects/{project_id}/comments", tags=["Project comments"])


def _visible_project(db: Session, project_id: UUID, viewer: Optional[User]) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or not can_view_project(db, project, viewer.id if viewer else None):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _like_maps(db: Session, comment_ids: list[UUID], viewer_id: Optional[UUID]) -> tuple[dict, set]:
    if not comment_ids:
        return {}, set()
    counts = dict(
        db.query(ProjectCommentLike.comment_id, func.count())
        .filter(ProjectCommentLike.comment_id.in_(comment_ids))
        .group_by(ProjectCommentLike.comment_id)
        .all()
    )
    liked: set[UUID] = set()
    if viewer_id:
        liked = {
            row[0]
            for row in db.query(ProjectCommentLike.comment_id)
            .filter(
                ProjectCommentLike.comment_id.in_(comment_ids),
                ProjectCommentLike.user_id == viewer_id,
            )
            .all()
        }
    return counts, liked


def _response(
    c: ProjectComment,
    project: Project,
    viewer: Optional[User],
    *,
    likes_count: int = 0,
    is_liked: bool = False,
    replies: list[ProjectCommentResponse] | None = None,
) -> ProjectCommentResponse:
    can_delete = viewer is not None and viewer.id in (c.author_id, project.owner_id)
    return ProjectCommentResponse(
        id=c.id,
        project_id=c.project_id,
        parent_id=c.parent_id,
        body=c.body,
        created_at=c.created_at,
        author=user_summary(c.author),
        can_delete=can_delete,
        likes_count=likes_count,
        is_liked=is_liked,
        replies=replies or [],
    )


@router.get("", response_model=ProjectCommentPage)
def list_comments(
    project_id: UUID,
    page: Optional[str] = None,
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    project = _visible_project(db, project_id, viewer)
    query = (
        db.query(ProjectComment)
        .options(selectinload(ProjectComment.author).selectinload(User.profile))
        .filter(ProjectComment.project_id == project_id, ProjectComment.parent_id.is_(None))
    )
    rows, next_page = paginate(query, ProjectComment, page, limit)
    top_ids = [c.id for c in rows]
    replies = (
        db.query(ProjectComment)
        .options(selectinload(ProjectComment.author).selectinload(User.profile))
        .filter(ProjectComment.parent_id.in_(top_ids))
        .order_by(ProjectComment.created_at.asc())
        .all()
        if top_ids
        else []
    )
    all_ids = top_ids + [r.id for r in replies]
    counts, liked = _like_maps(db, all_ids, viewer.id if viewer else None)
    by_parent: dict[UUID, list[ProjectComment]] = {}
    for r in replies:
        by_parent.setdefault(r.parent_id, []).append(r)

    items = []
    for c in rows:
        nested = [
            _response(
                r,
                project,
                viewer,
                likes_count=int(counts.get(r.id, 0)),
                is_liked=r.id in liked,
            )
            for r in by_parent.get(c.id, [])
        ]
        items.append(
            _response(
                c,
                project,
                viewer,
                likes_count=int(counts.get(c.id, 0)),
                is_liked=c.id in liked,
                replies=nested,
            )
        )
    return {"items": items, "next_page": next_page}


@router.post(
    "",
    response_model=ProjectCommentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("comment", 20, 60)],
)
def add_comment(
    project_id: UUID,
    data: ProjectCommentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = _visible_project(db, project_id, user)
    if not project.comments_enabled:
        raise HTTPException(status_code=403, detail="Comments are turned off for this project")
    if not may_comment_on_project(db, user.id, project):
        raise HTTPException(status_code=403, detail="This person only accepts comments from connections")

    parent = None
    if data.parent_id:
        parent = (
            db.query(ProjectComment)
            .filter_by(id=data.parent_id, project_id=project_id)
            .first()
        )
        if not parent:
            raise HTTPException(status_code=404, detail="Parent comment not found")
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="Replies can only be one level deep")

    comment = ProjectComment(
        project_id=project_id,
        author_id=user.id,
        body=data.body,
        parent_id=parent.id if parent else None,
    )
    db.add(comment)
    db.flush()
    notify(
        db,
        project.owner_id,
        user,
        "project_comment",
        f"{project_id}:{comment.id}",
        f"{display_name(user)} {'replied on' if parent else 'commented on'} your project \"{project.title}\".",
    )
    if parent and parent.author_id != user.id and parent.author_id != project.owner_id:
        notify(
            db,
            parent.author_id,
            user,
            "project_comment_reply",
            f"{project_id}:{comment.id}",
            f"{display_name(user)} replied to your comment on \"{project.title}\".",
        )
    db.commit()
    db.refresh(comment)
    comment = (
        db.query(ProjectComment)
        .options(selectinload(ProjectComment.author).selectinload(User.profile))
        .filter_by(id=comment.id)
        .first()
    )
    return _response(comment, project, user)


@router.post(
    "/{comment_id}/like",
    response_model=ProjectCommentLikeResponse,
    dependencies=[rate_limit("comment-like", 60, 60)],
)
def toggle_comment_like(
    project_id: UUID,
    comment_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _visible_project(db, project_id, user)
    comment = db.query(ProjectComment).filter_by(id=comment_id, project_id=project_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    existing = (
        db.query(ProjectCommentLike)
        .filter_by(comment_id=comment_id, user_id=user.id)
        .first()
    )
    if existing:
        db.delete(existing)
        is_liked = False
    else:
        db.add(ProjectCommentLike(comment_id=comment_id, user_id=user.id))
        is_liked = True
        if comment.author_id != user.id:
            notify(
                db,
                comment.author_id,
                user,
                "project_comment_like",
                f"{comment_id}:{user.id}",
                f"{display_name(user)} liked your comment.",
                once=True,
            )
    db.commit()
    likes_count = db.query(ProjectCommentLike).filter_by(comment_id=comment_id).count()
    return ProjectCommentLikeResponse(comment_id=comment_id, likes_count=likes_count, is_liked=is_liked)


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(
    project_id: UUID,
    comment_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = _visible_project(db, project_id, user)
    comment = db.query(ProjectComment).filter_by(id=comment_id, project_id=project_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if user.id not in (comment.author_id, project.owner_id):
        raise HTTPException(status_code=403, detail="Only the author or the project owner can delete this comment")
    db.delete(comment)
    db.commit()
