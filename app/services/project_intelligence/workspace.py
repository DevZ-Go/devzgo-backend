"""Resolve a local workspace path for analysis (local disk or temp extract from S3/R2)."""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from app.services import storage
from app.services.workspace_extract import safe_extract_zip
from app.services.workspace_files import workspace_dir_for_project


@contextmanager
def open_workspace(project_id: UUID):
    """Yield a Path to readable workspace files. Cleans temp dirs when using object storage."""
    if not storage.is_s3():
        root = workspace_dir_for_project(project_id)
        if not root.is_dir():
            raise FileNotFoundError("workspace missing")
        yield root
        return

    tmp = Path(tempfile.mkdtemp(prefix="pi_ws_"))
    try:
        try:
            data = storage.read(f"sources/{project_id}.zip")
        except FileNotFoundError:
            # Fall back to prefix download if available
            prefix = f"project_{project_id}"
            dest = tmp / "w"
            dest.mkdir()
            if hasattr(storage, "download_prefix"):
                storage.download_prefix(prefix, dest)
                yield dest
                return
            raise
        zip_path = tmp / "source.zip"
        zip_path.write_bytes(data)
        root = tmp / "w"
        root.mkdir()
        safe_extract_zip(zip_path, root, skip_symlinks=True)
        entries = list(root.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            wrapper = entries[0]
            for child in list(wrapper.iterdir()):
                child.rename(root / child.name)
            wrapper.rmdir()
        yield root
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
