"""
Tests for workspace ZIP safety and path helpers.

Run from repo root:
  pip install -r requirements.txt
  PYTHONPATH=. pytest tests/ -q
"""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.workspace_extract import safe_extract_zip
from app.services.workspace_files import (
    is_secret_path,
    normalize_relative_path,
    sniff_is_binary,
)


def _make_zip(tmp_path: Path, entries: dict[str, bytes], *, symlink_name: str | None = None) -> Path:
    zip_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink_name:
            info = zipfile.ZipInfo(symlink_name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, b"target.txt")
    return zip_path


def test_normalize_rejects_traversal():
    with pytest.raises(ValueError):
        normalize_relative_path("../etc/passwd")
    with pytest.raises(ValueError):
        normalize_relative_path("foo/../../bar")


def test_normalize_accepts_nested():
    assert normalize_relative_path("./src/App.tsx") == "src/App.tsx"
    assert normalize_relative_path("src\\App.tsx") == "src/App.tsx"


def test_secret_paths():
    assert is_secret_path(".env")
    assert is_secret_path("config/.env.local")
    assert is_secret_path("certs/server.pem")
    assert not is_secret_path("src/main.py")


def test_sniff_binary_null_byte():
    assert sniff_is_binary(b"hello\x00world", "x.bin")
    assert not sniff_is_binary(b"print('hi')\n", "main.py")
    assert sniff_is_binary(b"\x89PNG", "logo.png")


def test_safe_extract_blocks_zip_slip(tmp_path: Path):
    zip_path = _make_zip(tmp_path, {"../../evil.txt": b"pwned", "ok.txt": b"safe"})
    dest = tmp_path / "dest"
    safe_extract_zip(zip_path, dest)
    assert (dest / "ok.txt").read_text() == "safe"
    assert not (tmp_path / "evil.txt").exists()


def test_safe_extract_rejects_symlink(tmp_path: Path):
    zip_path = _make_zip(tmp_path, {"ok.txt": b"x"}, symlink_name="link.txt")
    dest = tmp_path / "dest"
    with pytest.raises(HTTPException) as exc:
        safe_extract_zip(zip_path, dest)
    assert exc.value.status_code == 400
    assert "symlink" in str(exc.value.detail).lower()


def test_safe_extract_rejects_too_many_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.services.workspace_extract.MAX_FILE_COUNT", 3)
    entries = {f"f{i}.txt": b"x" for i in range(5)}
    zip_path = _make_zip(tmp_path, entries)
    dest = tmp_path / "dest"
    with pytest.raises(HTTPException) as exc:
        safe_extract_zip(zip_path, dest)
    assert exc.value.status_code == 400
    assert "maximum" in str(exc.value.detail).lower()


def test_safe_extract_rejects_extracted_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.services.workspace_extract.MAX_EXTRACTED_BYTES", 100)
    zip_path = _make_zip(tmp_path, {"big.txt": b"x" * 200})
    dest = tmp_path / "dest"
    with pytest.raises(HTTPException) as exc:
        safe_extract_zip(zip_path, dest)
    assert exc.value.status_code == 400
