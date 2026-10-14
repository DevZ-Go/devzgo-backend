from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from uuid import UUID
import uuid

from app.db.session import get_db
from app.models.project import Project
from app.models.user import User
from app.core.dependencies import get_current_user
from app.services import storage

router = APIRouter(prefix="/projects", tags=["Project Media"])



@router.post("/{project_id}/media")
def upload_project_media(
    project_id: UUID,
    cover_image: UploadFile | None = File(default=None),
    demo_video: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if cover_image:
        cover_ext = cover_image.filename.split(".")[-1]
        cover_name = f"{project_id}_{uuid.uuid4()}.{cover_ext}"
        storage.save(f"covers/{cover_name}", cover_image.file)
        project.cover_image_url = f"/storage/covers/{cover_name}"

    if demo_video:
        video_ext = demo_video.filename.split(".")[-1]
        video_name = f"{project_id}_{uuid.uuid4()}.{video_ext}"
        storage.save(f"videos/{video_name}", demo_video.file)
        project.demo_video_url = f"/storage/videos/{video_name}"

    db.commit()
    db.refresh(project)

    return {
        "project_id": str(project.id),
        "cover_image_url": project.cover_image_url,
        "demo_video_url": project.demo_video_url,
    }