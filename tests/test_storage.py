import io
import os
import uuid
from pathlib import Path

import pytest

from app.services import storage


@pytest.fixture(params=["local", "s3"])
def backend(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run each test on disk and, when TEST_S3_ENDPOINT_URL is set (e.g. the compose RustFS), on a real bucket."""
    if request.param == "s3":
        endpoint = os.getenv("TEST_S3_ENDPOINT_URL")
        if not endpoint:
            pytest.skip("TEST_S3_ENDPOINT_URL not set")
        monkeypatch.setenv("S3_ENDPOINT_URL", endpoint)
    monkeypatch.setenv("STORAGE_BACKEND", request.param)
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    return f"test_{uuid.uuid4().hex}"


def test_save_read_delete(backend: str):
    storage.save(f"{backend}/a.txt", io.BytesIO(b"hello"))
    storage.save(f"{backend}/sub/b.txt", io.BytesIO(b"world"))
    assert storage.read(f"{backend}/sub/b.txt") == b"world"
    storage.delete_prefix(f"{backend}/")
    with pytest.raises(FileNotFoundError):
        storage.read(f"{backend}/a.txt")


def test_delete_by_name_prefix(backend: str):
    storage.save(f"{backend}/p1_x.png", io.BytesIO(b"1"))
    storage.save(f"{backend}/p2_y.png", io.BytesIO(b"2"))
    storage.delete_prefix(f"{backend}/p1_")
    assert storage.read(f"{backend}/p2_y.png") == b"2"
    with pytest.raises(FileNotFoundError):
        storage.read(f"{backend}/p1_x.png")
    storage.delete_prefix(f"{backend}/")
