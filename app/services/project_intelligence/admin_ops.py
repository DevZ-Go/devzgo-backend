"""Admin-facing Project Intelligence usage detail and forced regenerations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.project_intelligence import (
    PiGenerationRecord,
    PiMonthlyUsage,
    PiVisitorAiRequest,
    ProjectAiDescriptionDraft,
    ProjectAiPublication,
    ProjectAiUsageLedger,
)
from app.models.user import User
from app.services.project_intelligence.ai_generate import (
    approve_draft,
    get_publication,
    grant_external_ai_consent,
    snapshot_revision,
)
from app.services.project_intelligence.jobs import enqueue_analyze, enqueue_generate, latest_insight
from app.services.project_intelligence.pipeline import process_job, quick_content_hash
from app.services.project_intelligence.preferences import get_project_settings
from app.services.project_intelligence.quota import QuotaExceeded, reserve_generation, utc_year_month
from app.services.social import display_name


def list_ai_users(db: Session, *, q: str | None = None, limit: int = 50) -> list[dict]:
    """Users with any PI usage this month or historically, highest spend first."""
    ym = utc_year_month()
    limit = max(1, min(int(limit or 50), 200))
    term = (q or "").strip()

    month_rows = (
        db.query(PiMonthlyUsage, User)
        .join(User, User.id == PiMonthlyUsage.user_id)
        .filter(PiMonthlyUsage.year_month == ym)
    )
    if term:
        like = f"%{term}%"
        month_rows = month_rows.filter(or_(User.email.ilike(like), User.username.ilike(like)))
    month_rows = (
        month_rows.order_by(PiMonthlyUsage.cost_usd.desc(), PiMonthlyUsage.ai_projects_used.desc())
        .limit(limit)
        .all()
    )

    out: list[dict] = []
    seen: set[UUID] = set()
    for row, user in month_rows:
        seen.add(user.id)
        out.append(_user_usage_summary(db, user, row))

    if len(out) < limit:
        ledger_users = (
            db.query(
                ProjectAiUsageLedger.user_id,
                func.coalesce(func.sum(ProjectAiUsageLedger.estimated_cost_usd), 0.0).label("cost"),
                func.coalesce(func.sum(ProjectAiUsageLedger.tokens_in), 0).label("tin"),
                func.coalesce(func.sum(ProjectAiUsageLedger.tokens_out), 0).label("tout"),
                func.count(ProjectAiUsageLedger.id).label("calls"),
            )
            .group_by(ProjectAiUsageLedger.user_id)
            .order_by(func.coalesce(func.sum(ProjectAiUsageLedger.estimated_cost_usd), 0.0).desc())
            .limit(limit)
            .all()
        )
        for uid, cost, tin, tout, calls in ledger_users:
            if uid in seen:
                continue
            user = db.query(User).filter(User.id == uid).first()
            if not user:
                continue
            if term:
                blob = f"{user.email} {user.username}".lower()
                if term.lower() not in blob:
                    continue
            month = (
                db.query(PiMonthlyUsage)
                .filter(PiMonthlyUsage.user_id == uid, PiMonthlyUsage.year_month == ym)
                .first()
            )
            out.append(_user_usage_summary(db, user, month, lifetime_fallback=(cost, tin, tout, calls)))
            seen.add(uid)
            if len(out) >= limit:
                break
    return out


def _user_usage_summary(
    db: Session,
    user: User,
    month: PiMonthlyUsage | None,
    lifetime_fallback: tuple | None = None,
) -> dict:
    ym = utc_year_month()
    lifetime = (
        db.query(
            func.coalesce(func.sum(ProjectAiUsageLedger.estimated_cost_usd), 0.0),
            func.coalesce(func.sum(ProjectAiUsageLedger.tokens_in), 0),
            func.coalesce(func.sum(ProjectAiUsageLedger.tokens_out), 0),
            func.count(ProjectAiUsageLedger.id),
        )
        .filter(ProjectAiUsageLedger.user_id == user.id)
        .first()
    )
    if lifetime_fallback and (not lifetime or not lifetime[3]):
        lifetime = lifetime_fallback

    visitor_used = int(
        db.query(func.count(func.distinct(PiVisitorAiRequest.project_id)))
        .filter(
            PiVisitorAiRequest.user_id == user.id,
            PiVisitorAiRequest.was_cache_hit.is_(False),
        )
        .scalar()
        or 0
    )
    owned = int(db.query(func.count(Project.id)).filter(Project.owner_id == user.id).scalar() or 0)
    pubs = int(
        db.query(func.count(ProjectAiPublication.project_id))
        .join(Project, Project.id == ProjectAiPublication.project_id)
        .filter(Project.owner_id == user.id)
        .scalar()
        or 0
    )
    return {
        "user_id": str(user.id),
        "email": user.email,
        "username": user.username,
        "full_name": display_name(user),
        "year_month": ym,
        "month": {
            "ai_projects_used": int(month.ai_projects_used) if month else 0,
            "regenerations_used": int(month.regenerations_used) if month else 0,
            "requests_count": int(month.requests_count) if month else 0,
            "tokens_in": int(month.tokens_in) if month else 0,
            "tokens_out": int(month.tokens_out) if month else 0,
            "cost_usd": float(month.cost_usd) if month else 0.0,
        },
        "lifetime": {
            "cost_usd": float(lifetime[0] or 0) if lifetime else 0.0,
            "tokens_in": int(lifetime[1] or 0) if lifetime else 0,
            "tokens_out": int(lifetime[2] or 0) if lifetime else 0,
            "ledger_calls": int(lifetime[3] or 0) if lifetime else 0,
        },
        "visitor_ai_projects": visitor_used,
        "owned_projects": owned,
        "projects_with_ai_publication": pubs,
    }


def user_ai_detail(db: Session, user_id: UUID, *, ledger_limit: int = 100) -> dict:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")

    month = (
        db.query(PiMonthlyUsage)
        .filter(PiMonthlyUsage.user_id == user_id, PiMonthlyUsage.year_month == utc_year_month())
        .first()
    )
    summary = _user_usage_summary(db, user, month)

    months = (
        db.query(PiMonthlyUsage)
        .filter(PiMonthlyUsage.user_id == user_id)
        .order_by(PiMonthlyUsage.year_month.desc())
        .limit(24)
        .all()
    )
    ledger = (
        db.query(ProjectAiUsageLedger)
        .filter(ProjectAiUsageLedger.user_id == user_id)
        .order_by(ProjectAiUsageLedger.created_at.desc())
        .limit(max(1, min(ledger_limit, 500)))
        .all()
    )
    owned_project_ids = [
        pid
        for (pid,) in db.query(Project.id).filter(Project.owner_id == user_id).all()
    ]

    # Generations attributed to this user, plus any runs against their owned projects (e.g. admin regen).
    gen_filters = [PiGenerationRecord.user_id == user_id]
    if owned_project_ids:
        gen_filters.append(PiGenerationRecord.project_id.in_(owned_project_ids))
    generations = (
        db.query(PiGenerationRecord)
        .filter(or_(*gen_filters))
        .order_by(PiGenerationRecord.created_at.desc())
        .limit(100)
        .all()
    )

    visitor = (
        db.query(PiVisitorAiRequest)
        .filter(PiVisitorAiRequest.user_id == user_id)
        .order_by(PiVisitorAiRequest.created_at.desc())
        .limit(50)
        .all()
    )
    projects = (
        db.query(Project, ProjectAiPublication)
        .outerjoin(ProjectAiPublication, ProjectAiPublication.project_id == Project.id)
        .filter(Project.owner_id == user_id)
        .order_by(Project.updated_at.desc())
        .limit(100)
        .all()
    )
    titles = {str(p.id): p.title for p, _ in projects}

    return {
        **summary,
        "months": [
            {
                "year_month": m.year_month,
                "ai_projects_used": m.ai_projects_used,
                "regenerations_used": m.regenerations_used,
                "requests_count": m.requests_count,
                "tokens_in": m.tokens_in,
                "tokens_out": m.tokens_out,
                "cost_usd": float(m.cost_usd or 0),
            }
            for m in months
        ],
        "ledger": [
            {
                "id": str(r.id),
                "project_id": str(r.project_id) if r.project_id else None,
                "project_title": titles.get(str(r.project_id)) if r.project_id else None,
                "job_id": str(r.job_id) if r.job_id else None,
                "provider": r.provider,
                "model": r.model,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "estimated_cost_usd": float(r.estimated_cost_usd or 0),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in ledger
        ],
        "generations": [
            {
                "id": str(g.id),
                "user_id": str(g.user_id),
                "project_id": str(g.project_id),
                "project_title": titles.get(str(g.project_id)),
                "kind": g.kind,
                "status": g.status,
                "model": g.model,
                "prompt_version": g.prompt_version,
                "counts_as_project": bool(g.counts_as_project),
                "tokens_in": g.tokens_in,
                "tokens_out": g.tokens_out,
                "actual_cost_usd": float(g.actual_cost_usd or 0) if g.actual_cost_usd is not None else None,
                "error": g.error,
                "created_at": g.created_at.isoformat() if g.created_at else None,
                "completed_at": g.completed_at.isoformat() if g.completed_at else None,
            }
            for g in generations
        ],
        "visitor_requests": [
            {
                "id": str(v.id),
                "project_id": str(v.project_id),
                "was_cache_hit": bool(v.was_cache_hit),
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in visitor
        ],
        "projects": [
            {
                "id": str(p.id),
                "title": p.title,
                "visibility": p.visibility,
                "has_publication": pub is not None,
                "quality_status": getattr(pub, "quality_status", None) if pub else None,
                "schema_version": getattr(pub, "schema_version", None) if pub else None,
                "publish_simple": bool(pub.publish_simple) if pub else False,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p, pub in projects
        ],
    }


def admin_regenerate_project(
    db: Session,
    *,
    project_id: UUID,
    admin_user: User,
    publish: bool = True,
    include_detailed: bool = False,
) -> dict:
    """Force a fresh AI brief without consuming the project owner's monthly allowance."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ValueError("Project not found")

    pref = get_project_settings(db, project_id)
    pref.external_ai_allowed = True
    grant_external_ai_consent(db, admin_user.id, project_id)
    db.flush()

    insight = latest_insight(db, project_id)
    snapshot_id = insight.snapshot_id if insight else None
    if not snapshot_id:
        try:
            content_hash, file_count, byte_total = quick_content_hash(db, project_id)
            analyze_job = enqueue_analyze(
                db,
                project,
                requested_by=admin_user.id,
                content_hash=content_hash,
                file_count=file_count,
                byte_total=byte_total,
                force=True,
            )
            db.flush()
            if analyze_job.status == "queued":
                process_job(db, analyze_job)
            insight = latest_insight(db, project_id)
            snapshot_id = insight.snapshot_id if insight else analyze_job.snapshot_id
        except Exception:
            snapshot_id = None

    idem = f"admin-regen:{project_id}:{admin_user.id}:{uuid.uuid4().hex}"
    try:
        record, cached_draft = reserve_generation(
            db,
            user_id=admin_user.id,
            project_id=project_id,
            snapshot_id=snapshot_id,
            snapshot_revision=snapshot_revision(db, snapshot_id),
            selection={"detailed_description": bool(include_detailed)},
            idempotency_key=idem[:200],
            is_regeneration=True,
            admin=True,
            force=True,
        )
    except QuotaExceeded as exc:
        raise RuntimeError(str(exc)) from exc

    draft = cached_draft
    job_id = None
    if draft is None:
        job = enqueue_generate(
            db,
            project,
            requested_by=admin_user.id,
            snapshot_id=snapshot_id,
            parent_draft_id=None,
            generation_record_id=record.id,
            output_selection=record.output_selection or {},
            max_output_tokens=None,
            idempotency_key=f"job:{idem}"[:200],
        )
        progress = dict(job.progress or {})
        progress["admin"] = True
        progress["community"] = False
        job.progress = progress
        record.job_id = job.id
        db.flush()
        process_job(db, job)
        db.refresh(job)
        job_id = str(job.id)
        if job.status == "failed":
            raise RuntimeError(job.error or "Admin AI regeneration failed")
        draft = (
            db.query(ProjectAiDescriptionDraft)
            .filter(ProjectAiDescriptionDraft.job_id == job.id)
            .order_by(ProjectAiDescriptionDraft.created_at.desc())
            .first()
        )
        if not draft and record.draft_id:
            draft = db.query(ProjectAiDescriptionDraft).filter_by(id=record.draft_id).first()

    if not draft:
        raise RuntimeError("Generation finished but no draft was produced")

    published = False
    quality_status = None
    if publish:
        try:
            approve_draft(
                db,
                project,
                draft,
                apply_fields={
                    "publish_simple": True,
                    "show_detailed_on_page": bool(include_detailed or draft.technical_explanation),
                },
                user_id=admin_user.id,
            )
            published = True
            pub = get_publication(db, project_id)
            quality_status = getattr(pub, "quality_status", None) if pub else None
        except RuntimeError as exc:
            # Keep the draft; surface quality failure to the admin.
            quality_status = "low_quality"
            raise RuntimeError(str(exc)) from exc

    return {
        "ok": True,
        "project_id": str(project_id),
        "project_title": project.title,
        "draft_id": str(draft.id),
        "job_id": job_id,
        "generation_record_id": str(record.id),
        "published": published,
        "quality_status": quality_status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
