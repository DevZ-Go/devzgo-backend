import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base

class File(Base):
    __tablename__ = "files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Links every file to a project.
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)

    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    # Indicates whether the file is a directory or a regular file. This allows us to represent both files and folders in the same table.
    is_directory = Column(Boolean, default=False, nullable=False)
    # The parent_path column stores the path of the parent directory for each file. This is useful for reconstructing the file hierarchy and navigating through directories. For files in the root directory, this can be null or an empty string.
    parent_path = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())