"""Reclaim queued Project Intelligence jobs. Inline processing is preferred on API calls.

Run: python -m app.worker.project_intelligence_worker
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone

from app.core.feature_flags import project_intelligence_enabled
from app.db.session import SessionLocal
from app.models.project_intelligence import ProjectAnalysisJob
from app.services.project_intelligence.pipeline import process_job

log = logging.getLogger("project_intelligence_worker")
OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
POLL = float(os.getenv("PI_WORKER_POLL_SECONDS", "5"))
BATCH = int(os.getenv("PI_WORKER_BATCH", "2"))
LEASE = int(os.getenv("PI_WORKER_LEASE_SECONDS", "180"))


def _claim(db) -> list[ProjectAnalysisJob]:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(ProjectAnalysisJob)
        .filter(
            ProjectAnalysisJob.status.in_(("queued", "failed")),
            (ProjectAnalysisJob.lease_until.is_(None)) | (ProjectAnalysisJob.lease_until < now),
            ProjectAnalysisJob.cancel_requested.is_(False),
        )
        .order_by(ProjectAnalysisJob.created_at.asc())
        .limit(BATCH)
        .with_for_update(skip_locked=True)
        .all()
    )
    for row in rows:
        # Do not auto-retry failed generate jobs indefinitely — only reclaim queued + failed analyze once
        if row.status == "failed" and row.job_kind == "generate":
            continue
        if row.status == "failed" and (row.attempt_count or 0) >= 3:
            continue
        row.status = "queued"
        row.lease_owner = OWNER
        row.lease_until = now + timedelta(seconds=LEASE)
    db.commit()
    return [r for r in rows if r.lease_owner == OWNER]


def _run_once() -> int:
    if not project_intelligence_enabled():
        return 0
    db = SessionLocal()
    try:
        jobs = _claim(db)
        for job in jobs:
            try:
                process_job(db, job)
                job.lease_until = None
                job.lease_owner = None
                db.commit()
            except Exception as exc:  # noqa: BLE001
                log.exception("pi job %s failed: %s", job.id, exc)
                job.status = "failed"
                job.error = str(exc)[:500]
                job.lease_until = None
                db.commit()
        return len(jobs)
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("project intelligence worker started as %s", OWNER)
    while True:
        try:
            _run_once()
        except Exception:
            log.exception("project intelligence worker loop error")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
