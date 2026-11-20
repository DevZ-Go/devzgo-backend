"""Reclaim queued/failed resume import jobs. Prefer inline processing on upload; this recovers stuck rows.

Run: python -m app.worker.career_import_worker
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from app.db.session import SessionLocal
from app.models.career import ResumeImportJob
from app.routes.career_imports import process_job
from app.services import storage

log = logging.getLogger("career_import_worker")
OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
POLL = float(os.getenv("CAREER_IMPORT_WORKER_POLL_SECONDS", "5"))
BATCH = int(os.getenv("CAREER_IMPORT_WORKER_BATCH", "5"))
LEASE = int(os.getenv("CAREER_IMPORT_WORKER_LEASE_SECONDS", "120"))


def _claim(db) -> list[ResumeImportJob]:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(ResumeImportJob)
        .filter(
            ResumeImportJob.status.in_(("queued", "failed")),
            (ResumeImportJob.lease_until.is_(None)) | (ResumeImportJob.lease_until < now),
        )
        .order_by(ResumeImportJob.created_at.asc())
        .limit(BATCH)
        .with_for_update(skip_locked=True)
        .all()
    )
    for row in rows:
        row.status = "processing"
        row.lease_owner = OWNER
        row.lease_until = now + timedelta(seconds=LEASE)
    db.commit()
    return rows


def _run_once() -> int:
    db = SessionLocal()
    try:
        jobs = _claim(db)
        for job in jobs:
            try:
                raw = storage.read(job.storage_key)
                process_job(db, job, raw)
                job.lease_until = None
                job.lease_owner = None
                db.commit()
            except Exception as exc:  # noqa: BLE001
                log.exception("import job %s failed: %s", job.id, exc)
                job.status = "failed"
                job.error = str(exc)[:500]
                job.lease_until = None
                db.commit()
        return len(jobs)
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("career import worker started as %s", OWNER)
    while True:
        try:
            _run_once()
        except Exception:
            log.exception("career import worker loop error")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
