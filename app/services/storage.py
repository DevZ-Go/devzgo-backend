"""
File storage behind one interface: local disk (default) or any S3-compatible bucket
(MinIO, Cloudflare R2, Backblaze B2, AWS S3). Pick with STORAGE_BACKEND=local|s3.

Keys mirror the local layout: covers/<name>, videos/<name>, project_<id>/<path>.
"""

from __future__ import annotations

import mimetypes
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

ROOT = Path("storage")


def is_s3() -> bool:
    return os.getenv("STORAGE_BACKEND", "local").lower() == "s3"


def _bucket() -> str:
    return os.getenv("S3_BUCKET", "devzgo")


@lru_cache(maxsize=2)
def _client(public: bool = False):
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("S3_PUBLIC_ENDPOINT_URL") if public else None
    return boto3.client(
        "s3",
        endpoint_url=endpoint or os.getenv("S3_ENDPOINT_URL") or None,
        region_name=os.getenv("S3_REGION", "auto"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def save(key: str, fileobj: BinaryIO) -> None:
    if is_s3():
        # Without a ContentType the bucket serves binary/octet-stream, which some browsers won't play.
        content_type = mimetypes.guess_type(key)[0]
        _client().upload_fileobj(fileobj, _bucket(), key, ExtraArgs={"ContentType": content_type} if content_type else None)
        return
    path = ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as out:
        shutil.copyfileobj(fileobj, out)


def upload_dir(local_dir: Path, prefix: str = "") -> None:
    """Copy every regular file under local_dir to <prefix>/<relative path>."""
    for path in local_dir.rglob("*"):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(local_dir).as_posix()
            _client().upload_file(str(path), _bucket(), f"{prefix}/{rel}" if prefix else rel)


def read(key: str) -> bytes:
    """Return the object's bytes; FileNotFoundError if missing."""
    if not is_s3():
        return (ROOT / key).read_bytes()
    try:
        return _client().get_object(Bucket=_bucket(), Key=key)["Body"].read()
    except _client().exceptions.NoSuchKey as exc:
        raise FileNotFoundError(key) from exc


def url(key: str, expires: int = 3600, filename: str | None = None) -> str:
    """Short-lived signed download link (S3 mode only)."""
    params = {"Bucket": _bucket(), "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'inline; filename="{filename}"'
    return _client(public=True).generate_presigned_url("get_object", Params=params, ExpiresIn=expires)


def delete_prefix(prefix: str) -> None:
    """Delete one key, a folder ("project_<id>/"), or every key starting with prefix."""
    if is_s3():
        pages = _client().get_paginator("list_objects_v2").paginate(Bucket=_bucket(), Prefix=prefix)
        for page in pages:
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                _client().delete_objects(Bucket=_bucket(), Delete={"Objects": keys})
        return
    path = ROOT / prefix
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.parent.is_dir():
        for f in path.parent.glob(f"{path.name}*"):
            if f.is_file():
                f.unlink(missing_ok=True)
