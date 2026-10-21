from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from uuid import UUID
import uuid

from app.db.session import get_db
from app.models.project import Project
from app.models.user import User
from app.core.dependencies import get_current_user
from app.services import storage
from app.services.social import project_access

router = APIRouter(prefix="/projects", tags=["Project Media"])

_LIMITS = {
    "cover": ({"png", "jpg", "jpeg", "gif", "webp"}, "image/", 10 * 1024 * 1024),
    "video": ({"mp4", "webm", "mov"}, "video/", 200 * 1024 * 1024),
}


def _checked_ext(upload: UploadFile, kind: str) -> str:
    exts, mime_prefix, max_bytes = _LIMITS[kind]
    ext = (upload.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in exts or not (upload.content_type or "").startswith(mime_prefix):
        raise HTTPException(status_code=400, detail=f"Unsupported {kind} file type")
    upload.file.seek(0, 2)
    too_big = upload.file.tell() > max_bytes
    upload.file.seek(0)
    if too_big:
        raise HTTPException(status_code=413, detail=f"{kind.capitalize()} is larger than {max_bytes // (1024 * 1024)} MB")
    return ext


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

    if not project_access(db, project, current_user.id)["files"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    cover_ext = _checked_ext(cover_image, "cover") if cover_image else None
    video_ext = _checked_ext(demo_video, "video") if demo_video else None

    if cover_image:
        cover_name = f"{project_id}_{uuid.uuid4()}.{cover_ext}"
        storage.save(f"covers/{cover_name}", cover_image.file)
        project.cover_image_url = f"/storage/covers/{cover_name}"

    if demo_video:
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