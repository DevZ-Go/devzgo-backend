"""After GitHub accepts a commit, bring the stored snapshot and history up to date.

The commit and this job are separate. A failed job never creates another commit, and Retry runs
only the job. Commits on a branch other than the tracked one leave the snapshot alone.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.file import File as FileRecord
from app.models.project import Project
from app.models.user import User
from app.models.workspace import SYNC_JOB_DAYS, WorkspaceDraft, WorkspaceSyncJob
from app.services import github_history as history
from app.services import github_import as gh_import
from app.services import source_state
from app.services.file_types import classify
from app.services.social import notify
from app.services.workspace_files import write_workspace_bytes
from app.services.github_connection import access_token

MANIFESTS = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "requirements.txt", "pyproject.toml",
    "pipfile", "go.mod", "go.sum", "cargo.toml", "cargo.lock", "pom.xml", "build.gradle", "build.gradle.kts",
    "composer.json", "composer.lock", "gemfile", "gemfile.lock", "packages.config",
}
STUCK_MINUTES = 15


def matters(path: str | None) -> bool:
    if not path:
        return True
    name = Path(path).name.lower()
    return name in MANIFESTS or name.startswith(("dockerfile", "compose", ".github"))


def enqueue(db: Session, project: Project, user_id: UUID, audit_id: UUID | None, trigger: str,
            branch: str | None, commit_sha: str | None, path: str | None) -> WorkspaceSyncJob:
    job = WorkspaceSyncJob(
        project_id=project.id, user_id=user_id, audit_event_id=audit_id, trigger=trigger,
        branch=branch, commit_sha=commit_sha, path=path, status="pending", steps={}, attempts=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _step(job: WorkspaceSyncJob, name: str, state: str, detail: str | None = None) -> None:
    steps = dict(job.steps or {})
    steps[name] = {"state": state, "detail": detail}
    job.steps = steps


def _patch(db: Session, project: Project, path: str, content: str, commit_sha: str | None, ref: str | None) -> None:
    data = content.encode()
    write_workspace_bytes(project.id, path, data)
    row = db.query(FileRecord).filter_by(project_id=project.id, file_path=path, is_directory=False).one_or_none()
    if row is None:
        parent = str(Path(path).parent) if Path(path).parent.as_posix() != "." else None
        row = FileRecord(project_id=project.id, file_name=Path(path).name, file_path=path, is_directory=False, parent_path=parent)
        db.add(row)
    row.size_bytes = len(data)
    row.kind = classify(path, data[:4096]).kind
    source_state.record_snapshot(project, "github", ref, commit_sha)


def run_job(job_id: UUID) -> None:
    with SessionLocal() as db:
        job = db.query(WorkspaceSyncJob).filter_by(id=job_id).one_or_none()
        if job is None or job.status == "ok":
            return
        project = db.query(Project).filter_by(id=job.project_id).one_or_none()
        user = db.query(User).filter_by(id=job.user_id).one_or_none() if job.user_id else None
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        job.attempts = (job.attempts or 0) + 1
        db.commit()
        if project is None or user is None:
            job.status, job.error, job.finished_at = "failed", "The project or account is gone.", datetime.now(timezone.utc)
            db.commit()
            return
        try:
            token = access_token(db, user)
            tracked = project.github_tracked_branch or project.github_default_branch
            if job.branch and tracked and job.branch != tracked:
                _step(job, "snapshot", "skipped", "A different branch")
            elif job.path and not matters(job.path) and job.commit_sha:
                draft = db.query(WorkspaceDraft).filter_by(project_id=project.id, user_id=user.id, branch=job.branch, path=job.path).one_or_none()
                # The draft is deleted after a commit. The job carries the bytes only when retrying a patch
                # is unnecessary: a patch retry re-reads the file from GitHub.
                from app.services.workspace_git import file_at
                remote = file_at(token, project.github_full_name, job.path, job.commit_sha)
                if remote.get("content") is None:
                    raise RuntimeError("The committed file is not UTF-8 text, so the snapshot was left as it was.")
                _patch(db, project, job.path, remote["content"], job.commit_sha, job.branch)
                _step(job, "snapshot", "ok")
            else:
                with tempfile.TemporaryDirectory(prefix="sync_") as tmp:
                    archive = Path(tmp) / "repo.zip"
                    ref = job.commit_sha or job.branch or tracked or "HEAD"
                    if not gh_import.download_archive(token, project.github_full_name, ref, archive):
                        raise RuntimeError("GitHub has no archive for that commit.")
                    from app.routes.workspace_upload import install_workspace_zip
                    install_workspace_zip(db, project, archive, from_github=True, ref=job.branch, commit_sha=job.commit_sha)
                _step(job, "snapshot", "ok")
            if history.capture(db, project, token, project.github_full_name):
                _step(job, "history", "ok")
            else:
                _step(job, "history", "failed", "GitHub history could not be read.")
            for member_id in {project.owner_id, *(row.user_id for row in project.collaborators)}:
                if member_id != user.id:
                    notify(db, member_id, user, "workspace_commit", str(project.id),
                           f"{user.username} committed to {project.title}"[:255])
            db.commit()
            job = db.query(WorkspaceSyncJob).filter_by(id=job_id).one()
            job.status = "ok" if (job.steps or {}).get("history", {}).get("state") != "failed" else "failed"
            if job.status == "failed" and not job.error:
                job.error = "History could not be refreshed. The commit is on GitHub."
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.query(WorkspaceSyncJob).filter_by(id=job_id).one()
            message = getattr(exc, "detail", None) or getattr(exc, "message", None) or "Synchronisation failed."
            if not isinstance(message, str):
                message = "Synchronisation failed."
            job.status, job.error, job.finished_at = "failed", message[:300], datetime.now(timezone.utc)
            _step(job, "snapshot", "failed", job.error)
            db.commit()


def recover(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_MINUTES)
    stuck = db.query(WorkspaceSyncJob).filter(WorkspaceSyncJob.status == "running", WorkspaceSyncJob.started_at < cutoff).all()
    for job in stuck:
        job.status, job.error, job.finished_at = "failed", "Synchronisation stopped. Retry it.", datetime.now(timezone.utc)
    old = datetime.now(timezone.utc) - timedelta(days=SYNC_JOB_DAYS)
    removed = db.query(WorkspaceSyncJob).filter(WorkspaceSyncJob.status.in_(("ok", "failed")), WorkspaceSyncJob.finished_at < old).delete(synchronize_session=False)
    db.commit()
    return len(stuck) + (removed or 0)
