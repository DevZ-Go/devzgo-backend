from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from uuid import UUID
from pathlib import Path
import shutil
import uuid

from app.db.session import get_db
from app.models.project import Project
from app.models.user import User
from app.core.dependencies import get_current_user

router = APIRouter(prefix="/projects", tags=["Project Media"])

BASE_STORAGE = Path("storage")
COVER_DIR = BASE_STORAGE / "covers"
VIDEO_DIR = BASE_STORAGE / "videos"

COVER_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)


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
        cover_path = COVER_DIR / cover_name

        with open(cover_path, "wb") as buffer:
            shutil.copyfileobj(cover_image.file, buffer)

        project.cover_image_url = f"/storage/covers/{cover_name}"

    if demo_video:
        video_ext = demo_video.filename.split(".")[-1]
        video_name = f"{project_id}_{uuid.uuid4()}.{video_ext}"
        video_path = VIDEO_DIR / video_name

        with open(video_path, "wb") as buffer:
            shutil.copyfileobj(demo_video.file, buffer)

        project.demo_video_url = f"/storage/videos/{video_name}"

    db.commit()
    db.refresh(project)

    return {
        "project_id": str(project.id),
        "cover_image_url": project.cover_image_url,
        "demo_video_url": project.demo_video_url,
    }