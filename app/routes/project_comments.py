"""Comments on projects. Distinct from comments on Network posts (app/routes/network.py)."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.project import Project
from app.models.project_comment import ProjectComment
from app.models.user import User
from app.schemas.project import ProjectCommentCreate, ProjectCommentPage, ProjectCommentResponse
from app.services.pagination import paginate
from app.services.social import can_view_project, display_name, may_comment_on_project, notify, user_summary

router = APIRouter(prefix="/projects/{project_id}/comments", tags=["Project comments"])


def _visible_project(db: Session, project_id: UUID, viewer: Optional[User]) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or not can_view_project(db, project, viewer.id if viewer else None):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _response(c: ProjectComment, project: Project, viewer: Optional[User]) -> ProjectCommentResponse:
    can_delete = viewer is not None and viewer.id in (c.author_id, project.owner_id)
    return ProjectCommentResponse(id=c.id, project_id=c.project_id, body=c.body, created_at=c.created_at,
                                  author=user_summary(c.author), can_delete=can_delete)


@router.get("", response_model=ProjectCommentPage)
def list_comments(project_id: UUID, page: Optional[str] = None, limit: int = Query(20, ge=1, le=50),
                  db: Session = Depends(get_db), viewer: Optional[User] = Depends(get_current_user_optional)):
    project = _visible_project(db, project_id, viewer)
    query = db.query(ProjectComment).options(selectinload(ProjectComment.author).selectinload(User.profile)) \
        .filter(ProjectComment.project_id == project_id)
    rows, next_page = paginate(query, ProjectComment, page, limit)
    return {"items": [_response(c, project, viewer) for c in rows], "next_page": next_page}


@router.post("", response_model=ProjectCommentResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[rate_limit("comment", 20, 60)])
def add_comment(project_id: UUID, data: ProjectCommentCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    project = _visible_project(db, project_id, user)
    if not project.comments_enabled:
        raise HTTPException(status_code=403, detail="Comments are turned off for this project")
    if not may_comment_on_project(db, user.id, project):
        raise HTTPException(status_code=403, detail="This person only accepts comments from connections")
    comment = ProjectComment(project_id=project_id, author_id=user.id, body=data.body)
    db.add(comment)
    notify(db, project.owner_id, user, "project_comment", str(project_id),
           f"{display_name(user)} commented on your project \"{project.title}\".")
    db.commit()
    db.refresh(comment)
    return _response(comment, project, user)


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(project_id: UUID, comment_id: UUID, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    project = _visible_project(db, project_id, user)
    comment = db.query(ProjectComment).filter_by(id=comment_id, project_id=project_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if user.id not in (comment.author_id, project.owner_id):
        raise HTTPException(status_code=403, detail="Only the author or the project owner can delete this comment")
    db.delete(comment)
    db.commit()
