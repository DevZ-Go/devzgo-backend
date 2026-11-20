"""Project Intelligence API: settings, analyze, insights, AI drafts."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user_optional, require_developer
from app.core.feature_flags import project_intelligence_enabled
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.project import Project
from app.models.project_intelligence import ProjectAiDescriptionDraft, ProjectEvidenceRef, PiVisitorAiRequest
from app.models.user import User
from app.schemas.project_intelligence import (
    AnalyzeIn,
    ApproveDraftIn,
    ClaimOut,
    DraftOut,
    GenerateIn,
    GlobalPrefIn,
    GlobalPrefOut,
    InsightsOut,
    JobStatusOut,
    ProjectPrefIn,
    ProjectPrefOut,
    PublicationOut,
    QuotaOut,
    VisitorAiStatusOut,
)
from app.services.project_intelligence.access import can_see_insights, redact_technical
from app.services.project_intelligence.ai_generate import (
    approve_draft,
    get_publication,
    grant_external_ai_consent,
    restore_developer_text,
)
from app.services.project_intelligence.jobs import (
    clear_cached_findings,
    enqueue_analyze,
    enqueue_generate,
    latest_insight,
    latest_status,
    list_claims,
    list_drafts,
    request_cancel,
)
from app.services.project_intelligence.pipeline import process_job, quick_content_hash
from app.services.project_intelligence.preferences import (
    analysis_allowed,
    get_project_settings,
    get_user_pref,
    set_user_pref,
    update_project_settings,
)
from app.services.project_intelligence.quota import (
    QuotaExceeded as PiQuotaExceeded,
    assert_can_analyze,
    get_pi_settings,
    quota_status,
    set_pi_settings,
)
from app.services.social import can_view_project, project_access

router = APIRouter(tags=["Project Intelligence"])


def _require_flag() -> None:
    if not project_intelligence_enabled():
        raise HTTPException(status_code=404, detail="Project Intelligence is not enabled")


def _project(db: Session, project_id: UUID) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _team(db: Session, project: Project, user: User):
    from app.services.project_intelligence.visitor_ai import is_platform_admin

    access = project_access(db, project, user.id)
    if access.get("owner") or access.get("files") or is_platform_admin(db, user):
        return access
    raise HTTPException(status_code=403, detail="Only the project team can manage Project Intelligence")


def _job_out(job) -> JobStatusOut:
    if not job:
        return JobStatusOut()
    return JobStatusOut(
        job_id=job.id,
        status=job.status,
        job_kind=job.job_kind,
        coverage_pct=job.coverage_pct,
        progress=job.progress or {},
        error=job.error,
        cancel_requested=bool(job.cancel_requested),
        created_at=job.created_at,
        completed_at=job.completed_at,
        snapshot_id=job.snapshot_id,
    )


def _draft_out(d: ProjectAiDescriptionDraft) -> DraftOut:
    uncertainties = []
    if isinstance(d.raw_payload, dict):
        uncertainties = d.raw_payload.get("uncertainties") or []
    return DraftOut(
        id=d.id,
        status=d.status,
        model=d.model,
        short_description=d.short_description,
        full_overview=d.full_overview,
        key_features=d.key_features or [],
        problem_solved=d.problem_solved,
        potential_audience=d.potential_audience,
        how_it_works_simple=d.how_it_works_simple,
        technical_explanation=d.technical_explanation,
        resume_bullets=d.resume_bullets or [],
        evidence_refs=d.evidence_refs or [],
        parent_draft_id=d.parent_draft_id,
        created_at=d.created_at,
        approved_at=d.approved_at,
        uncertainties=uncertainties,
    )


@router.get("/settings/project-intelligence", response_model=GlobalPrefOut)
def get_global_pref(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_flag()
    pref = get_user_pref(db, me.id)
    return GlobalPrefOut(auto_analyze_default=bool(pref.auto_analyze_default))


@router.patch("/settings/project-intelligence", response_model=GlobalPrefOut)
def patch_global_pref(data: GlobalPrefIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_flag()
    pref = set_user_pref(db, me.id, auto_analyze_default=data.auto_analyze_default)
    db.commit()
    return GlobalPrefOut(auto_analyze_default=bool(pref.auto_analyze_default))


@router.get("/projects/{project_id}/intelligence/preferences", response_model=ProjectPrefOut)
def get_project_pref(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    settings = get_project_settings(db, project_id)
    return ProjectPrefOut(
        intelligence_enabled=settings.intelligence_enabled,
        external_ai_allowed=bool(settings.external_ai_allowed),
        insight_audience=settings.insight_audience or "public",
        effective_auto_analyze=analysis_allowed(db, project_id, project.owner_id),
    )


@router.patch("/projects/{project_id}/intelligence/preferences", response_model=ProjectPrefOut)
def patch_project_pref(
    project_id: UUID,
    data: ProjectPrefIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    settings = update_project_settings(db, project_id, data.model_dump(exclude_unset=True))
    if settings.intelligence_enabled is False:
        open_job = latest_status(db, project_id)
        if open_job and open_job.status in ("queued", "analyzing", "generating"):
            request_cancel(db, open_job)
    db.commit()
    return ProjectPrefOut(
        intelligence_enabled=settings.intelligence_enabled,
        external_ai_allowed=bool(settings.external_ai_allowed),
        insight_audience=settings.insight_audience or "public",
        effective_auto_analyze=analysis_allowed(db, project_id, project.owner_id),
    )


@router.post(
    "/projects/{project_id}/intelligence/analyze",
    response_model=JobStatusOut,
    dependencies=[rate_limit("pi-analyze", 10, 600)],
)
def analyze_project(
    project_id: UUID,
    data: AnalyzeIn = AnalyzeIn(),
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    if not analysis_allowed(db, project_id, project.owner_id) and not data.force:
        raise HTTPException(status_code=403, detail="Project Intelligence is disabled for this project")
    try:
        assert_can_analyze(db, project.owner_id, project_id)
    except PiQuotaExceeded as exc:
        raise HTTPException(status_code=402, detail={"message": str(exc), "quota": exc.status})
    try:
        content_hash, file_count, byte_total = quick_content_hash(db, project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=409, detail="There is no stored source to analyse yet.")
    job = enqueue_analyze(
        db,
        project,
        requested_by=me.id,
        content_hash=content_hash,
        file_count=file_count,
        byte_total=byte_total,
        force=data.force,
    )
    db.commit()
    # Process inline when still queued (MVP durability like resume import)
    if job.status == "queued":
        process_job(db, job)
        db.refresh(job)
    return _job_out(job)


@router.post("/projects/{project_id}/intelligence/cancel", response_model=JobStatusOut)
def cancel_job(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    job = latest_status(db, project_id)
    if not job:
        raise HTTPException(status_code=404, detail="No analysis job found")
    request_cancel(db, job)
    db.commit()
    db.refresh(job)
    return _job_out(job)


@router.get("/projects/{project_id}/intelligence/status", response_model=JobStatusOut)
def get_status(
    project_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    return _job_out(latest_status(db, project_id))


@router.get("/projects/{project_id}/intelligence/quota", response_model=QuotaOut)
def get_quota(
    project_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    return QuotaOut(**quota_status(db, project.owner_id, project_id))


@router.get("/projects/{project_id}/intelligence/insights", response_model=InsightsOut)
def get_insights(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _require_flag()
    project = _project(db, project_id)
    viewer_id = current_user.id if current_user else None
    if not can_view_project(db, project, viewer_id):
        raise HTTPException(status_code=404, detail="Project not found")
    allowed, is_team = can_see_insights(db, project, viewer_id, current_user)
    if not allowed:
        return InsightsOut(available=False, redacted=True)
    settings = get_pi_settings(db)
    if not settings.get("visitor_insights_enabled", True) and not is_team:
        return InsightsOut(available=False, redacted=True)
    from app.services.project_intelligence.quality import (
        QUALITY_DETERMINISTIC,
        QUALITY_LOW,
        QUALITY_VALID,
        brief_from_legacy_simple,
        developer_brief,
        is_valid_ai_explanation,
        score_explanation_quality,
    )

    insight = latest_insight(db, project_id)
    pub = get_publication(db, project_id)
    developer = {
        "title": project.title,
        "short_description": project.short_description or "",
        "full_description": project.full_description or "",
    }

    visitor_ok = bool(settings.get("visitor_insights_enabled", True))
    pub_simple = dict(pub.simple_explanation) if pub and pub.simple_explanation else {}
    if pub and pub.key_features and not pub_simple.get("features") and not pub_simple.get("key_features"):
        pub_simple["key_features"] = pub.key_features

    # Re-score legacy publications so low-quality cached AI is not treated as final.
    stored_status = getattr(pub, "quality_status", None) if pub else None
    if pub and pub_simple and stored_status in (None, "", "unknown"):
        scored = score_explanation_quality(pub_simple, title=project.title or "")
        stored_status = scored["status"]
    ai_valid = bool(
        pub
        and pub_simple
        and (
            stored_status == QUALITY_VALID
            or (stored_status not in (QUALITY_LOW, "rejected", QUALITY_DETERMINISTIC) and is_valid_ai_explanation(pub_simple, title=project.title or ""))
        )
    )
    publish_ok = bool(pub and (is_team or pub.publish_simple or (visitor_ok and ai_valid)))
    show_simple_ai = bool(ai_valid and publish_ok)
    show_detailed_ai = bool(
        pub
        and ai_valid
        and (
            is_team
            or pub.show_detailed_on_page
            or (visitor_ok and (bool(pub.technical_explanation) or bool(pub.ai_detailed_description)))
        )
    )

    project_brief: dict = {}
    explanation_kind = "none"
    quality_status = stored_status
    simple_english: dict = {}

    if show_simple_ai and pub_simple:
        project_brief = brief_from_legacy_simple(pub_simple, title=project.title or "", features=pub.key_features if pub else None)
        explanation_kind = "ai"
        quality_status = QUALITY_VALID
        # Legacy Q&A mirror for older clients (only populate non-empty)
        simple_english = {
            "what_is_this": project_brief.get("headline") or "",
            "what_it_does": project_brief.get("overview") or "",
            "why_use_it": project_brief.get("purpose") or "",
            "who_benefits": project_brief.get("audience") or "",
            "how_it_works": project_brief.get("workflow") or "",
            "key_features": project_brief.get("features") or [],
            "capabilities": project_brief.get("features") or [],
            "uncertainties": project_brief.get("limitations") or [],
        }
    else:
        # Prefer deterministic composer brief, then developer text — never title-only placeholders.
        everyday = (insight.everyday if insight else {}) or {}
        det = everyday.get("project_brief") if isinstance(everyday.get("project_brief"), dict) else None
        if det and (det.get("overview") or det.get("headline")):
            project_brief = dict(det)
            explanation_kind = "deterministic"
            quality_status = QUALITY_DETERMINISTIC
            simple_english = {
                "what_is_this": project_brief.get("headline") or "",
                "what_it_does": project_brief.get("overview") or "",
                "why_use_it": project_brief.get("purpose") or "",
                "who_benefits": project_brief.get("audience") or "",
                "how_it_works": project_brief.get("workflow") or "",
                "key_features": project_brief.get("features") or everyday.get("capabilities") or [],
                "capabilities": project_brief.get("features") or everyday.get("capabilities") or [],
                "uncertainties": project_brief.get("limitations")
                or (list(insight.uncertainties) if insight and insight.uncertainties else []),
            }
        else:
            dev_b = developer_brief(
                project.title or "",
                project.short_description or "",
                project.full_description or "",
            )
            if dev_b:
                project_brief = dev_b
                explanation_kind = "developer"
                quality_status = QUALITY_DETERMINISTIC
                simple_english = {
                    "what_is_this": project_brief.get("headline") or "",
                    "what_it_does": project_brief.get("overview") or "",
                    "how_it_works": project_brief.get("workflow") or "",
                    "key_features": [],
                    "capabilities": [],
                }
            elif insight:
                simple_english = {
                    "what_it_does": everyday.get("what_it_does") or everyday.get("one_sentence") or "",
                    "why_use_it": everyday.get("problem") or "",
                    "who_benefits": everyday.get("who_could_use_it") or "",
                    "capabilities": everyday.get("capabilities") or [],
                    "how_it_works": everyday.get("how_it_works") or "",
                    "uncertainties": insight.uncertainties or everyday.get("uncertainties") or [],
                }
                if simple_english.get("what_it_does"):
                    project_brief = brief_from_legacy_simple(simple_english, title=project.title or "")
                    explanation_kind = "deterministic"
                    quality_status = QUALITY_DETERMINISTIC

    technical = (insight.technical if insight else {}) or {}
    if pub and pub.technical_explanation and (is_team or show_detailed_ai):
        technical = {**technical, "architecture": pub.technical_explanation}
    if pub and pub.ai_detailed_description and show_detailed_ai:
        technical = {**technical, "ai_detailed_description": pub.ai_detailed_description}

    redacted = False
    if not is_team:
        technical = redact_technical(technical)
        redacted = True

    has_content = bool(project_brief or insight or show_detailed_ai or project.short_description)
    if not has_content:
        return InsightsOut(available=False)

    return InsightsOut(
        available=True,
        audience=insight.audience if insight else "public",
        everyday=(insight.everyday if insight else {}) or {},
        technical=technical,
        uncertainties=(insight.uncertainties if insight else []) or project_brief.get("limitations") or [],
        snapshot_id=insight.snapshot_id if insight else None,
        composer_version=insight.composer_version if insight else None,
        redacted=redacted,
        developer=developer,
        simple_english=simple_english,
        project_brief=project_brief,
        explanation_kind=explanation_kind,
        quality_status=quality_status,
        ai_explanation_valid=show_simple_ai,
        schema_version=project_brief.get("schema_version") if project_brief else None,
    )


@router.get("/projects/{project_id}/intelligence/claims", response_model=List[ClaimOut])
def get_claims(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _require_flag()
    project = _project(db, project_id)
    viewer_id = current_user.id if current_user else None
    if not can_view_project(db, project, viewer_id):
        raise HTTPException(status_code=404, detail="Project not found")
    allowed, is_team = can_see_insights(db, project, viewer_id, current_user)
    if not allowed:
        return []
    insight = latest_insight(db, project_id)
    snapshot_id = insight.snapshot_id if insight else None
    rows = list_claims(db, project_id, snapshot_id)
    out: list[ClaimOut] = []
    for row in rows:
        if row.visibility == "team" and not is_team:
            continue
        refs = []
        if is_team:
            for ref in db.query(ProjectEvidenceRef).filter(ProjectEvidenceRef.claim_id == row.id).all():
                refs.append({
                    "source_file": ref.source_file,
                    "line_start": ref.line_start,
                    "line_end": ref.line_end,
                    "label": ref.label,
                })
        out.append(ClaimOut(
            id=row.id,
            claim_type=row.claim_type,
            wording=row.wording,
            support_class=row.support_class,
            source_file=row.source_file if is_team else None,
            source_location=row.source_location if is_team else None,
            source_revision=row.source_revision,
            visibility=row.visibility,
            attribution_status=row.attribution_status,
            refs=refs,
        ))
    return out


@router.get("/projects/{project_id}/intelligence/visitor-ai", response_model=VisitorAiStatusOut)
def get_visitor_ai_status(
    project_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    from app.services.project_intelligence.visitor_ai import (
        community_summary_ready,
        is_platform_admin,
        visitor_quota_status,
    )

    _require_flag()
    project = _project(db, project_id)
    if not can_view_project(db, project, me.id):
        raise HTTPException(status_code=404, detail="Project not found")
    access = project_access(db, project, me.id)
    is_team = bool(access.get("owner") or access.get("files") or is_platform_admin(db, me))
    status = visitor_quota_status(db, me)
    ready = community_summary_ready(db, project_id)
    can_generate = False
    if (status.get("visitor_ai_enabled") or status.get("is_admin")) and not ready:
        if status.get("can_request_new") or status.get("is_admin"):
            can_generate = True
        else:
            prior = (
                db.query(PiVisitorAiRequest)
                .filter(PiVisitorAiRequest.user_id == me.id, PiVisitorAiRequest.project_id == project_id)
                .first()
            )
            can_generate = bool(prior)
    # Project team uses the owner Generate panel; visitors (and admins on others' projects) use this CTA
    show_visitor_cta = can_generate and not bool(access.get("owner") or access.get("files"))
    return VisitorAiStatusOut(
        **{k: status[k] for k in ("is_admin", "visitor_ai_enabled", "used", "limit", "remaining", "can_request_new")},
        summary_ready=ready,
        can_generate=show_visitor_cta,
        is_team=is_team,
    )


@router.post(
    "/projects/{project_id}/intelligence/visitor-generate",
    response_model=JobStatusOut,
    dependencies=[rate_limit("pi-visitor-generate", 4, 600)],
)
def visitor_generate_ai(
    project_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    """Signed-in visitors may generate a shared AI summary (≤N projects); then anyone can read it."""
    from app.services.project_intelligence.ai_generate import (
        approve_draft,
        grant_external_ai_consent,
        snapshot_revision,
    )
    from app.services.project_intelligence.quota import reserve_generation
    from app.services.project_intelligence.visitor_ai import (
        VisitorAiError,
        assert_can_visitor_generate,
        community_summary_ready,
        record_visitor_request,
    )

    _require_flag()
    project = _project(db, project_id)
    if not can_view_project(db, project, me.id):
        raise HTTPException(status_code=404, detail="Project not found")
    # Team / admins should use the owner generate path, but allow admins via this too
    try:
        vstatus = assert_can_visitor_generate(db, me, project)
    except VisitorAiError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"message": str(exc), "quota": exc.status})

    if community_summary_ready(db, project_id):
        record_visitor_request(db, user_id=me.id, project_id=project_id, was_cache_hit=True)
        db.commit()
        return JobStatusOut(
            status="ready",
            job_kind="visitor_generate",
            progress={"stage": "cache_hit", "community": True},
        )

    # Reuse an unpublished draft if one already exists (no extra OpenAI call)
    existing_draft = (
        db.query(ProjectAiDescriptionDraft)
        .filter(
            ProjectAiDescriptionDraft.project_id == project_id,
            ProjectAiDescriptionDraft.status.in_(("draft", "approved")),
        )
        .order_by(ProjectAiDescriptionDraft.created_at.desc())
        .first()
    )
    if existing_draft and (
        existing_draft.how_it_works_simple
        or existing_draft.short_description
        or (existing_draft.key_features or [])
    ):
        try:
            approve_draft(
                db,
                project,
                existing_draft,
                apply_fields={
                    "publish_simple": True,
                    "show_detailed_on_page": bool(existing_draft.technical_explanation),
                },
                user_id=me.id,
            )
            record_visitor_request(db, user_id=me.id, project_id=project_id, was_cache_hit=True)
            db.commit()
            return JobStatusOut(
                status="ready",
                job_kind="visitor_generate",
                progress={"stage": "published_existing", "draft_id": str(existing_draft.id), "community": True},
            )
        except RuntimeError:
            # Low-quality legacy draft — fall through and generate a fresh brief.
            pass

    # Community generation: grant consent for this requester; enable external AI for the shared summary
    pref = get_project_settings(db, project_id)
    pref.external_ai_allowed = True
    grant_external_ai_consent(db, me.id, project_id)
    db.flush()

    insight = latest_insight(db, project_id)
    snapshot_id = insight.snapshot_id if insight else None
    if not snapshot_id:
        try:
            content_hash, file_count, byte_total = quick_content_hash(db, project_id)
            analyze_job = enqueue_analyze(
                db, project, requested_by=me.id, content_hash=content_hash,
                file_count=file_count, byte_total=byte_total, force=False,
            )
            db.commit()
            if analyze_job.status == "queued":
                process_job(db, analyze_job)
            insight = latest_insight(db, project_id)
            snapshot_id = insight.snapshot_id if insight else analyze_job.snapshot_id
        except Exception:
            pass

    settings = get_pi_settings(db)
    idem = f"visitor:{project_id}:{me.id}:{snapshot_id}"
    try:
        record, cached_draft = reserve_generation(
            db,
            user_id=me.id,
            project_id=project_id,
            snapshot_id=snapshot_id,
            snapshot_revision=snapshot_revision(db, snapshot_id),
            selection={"detailed_description": False},
            idempotency_key=idem[:200],
            is_regeneration=False,
            community=True,
        )
    except PiQuotaExceeded as exc:
        raise HTTPException(status_code=402, detail={"message": str(exc), "quota": {**vstatus, **(exc.status or {})}})

    if cached_draft is not None and record.status == "cache_hit":
        try:
            approve_draft(
                db,
                project,
                cached_draft,
                apply_fields={
                    "publish_simple": True,
                    "show_detailed_on_page": bool(cached_draft.technical_explanation),
                },
                user_id=me.id,
            )
            record_visitor_request(
                db,
                user_id=me.id,
                project_id=project_id,
                was_cache_hit=True,
                generation_record_id=record.id,
            )
            db.commit()
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JobStatusOut(
            status="ready",
            job_kind="visitor_generate",
            progress={"stage": "cache_hit", "draft_id": str(cached_draft.id), "community": True},
        )

    job = enqueue_generate(
        db,
        project,
        requested_by=me.id,
        snapshot_id=snapshot_id,
        parent_draft_id=None,
        generation_record_id=record.id,
        output_selection=record.output_selection or {},
        max_output_tokens=int(settings.get("free_max_output_tokens") or 2000),
        idempotency_key=f"job:{idem}"[:200],
    )
    # Mark job as community so pipeline auto-publishes
    progress = dict(job.progress or {})
    progress["community"] = True
    job.progress = progress
    record.job_id = job.id
    db.flush()

    # Count this project against the visitor's 5-project allowance once generation starts
    record_visitor_request(
        db,
        user_id=me.id,
        project_id=project_id,
        was_cache_hit=False,
        generation_record_id=record.id,
    )
    db.commit()
    process_job(db, job)
    db.refresh(job)
    if job.status == "failed":
        err = job.error or "AI generation failed"
        lower = err.lower()
        if "consent" in lower:
            raise HTTPException(status_code=403, detail=err)
        if "budget" in lower or "quota" in lower or "allowance" in lower:
            raise HTTPException(status_code=429, detail=err)
        raise HTTPException(status_code=502, detail=err)
    return _job_out(job)


@router.post(
    "/projects/{project_id}/intelligence/generate",
    response_model=JobStatusOut,
    dependencies=[rate_limit("pi-generate", 6, 600)],
)
def generate_ai(
    project_id: UUID,
    data: GenerateIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    from app.services.project_intelligence.ai_generate import snapshot_revision
    from app.services.project_intelligence.quota import normalize_outputs, reserve_generation

    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    settings = get_pi_settings(db)
    if not settings.get("ai_generate_enabled", True):
        raise HTTPException(status_code=403, detail="AI description generation is disabled by an administrator")
    if data.grant_consent:
        pref = get_project_settings(db, project_id)
        pref.external_ai_allowed = True
        grant_external_ai_consent(db, me.id, project_id)
        db.flush()
    insight = latest_insight(db, project_id)
    snapshot_id = insight.snapshot_id if insight else None
    if not snapshot_id:
        try:
            content_hash, file_count, byte_total = quick_content_hash(db, project_id)
            analyze_job = enqueue_analyze(
                db, project, requested_by=me.id, content_hash=content_hash,
                file_count=file_count, byte_total=byte_total, force=False,
            )
            db.commit()
            if analyze_job.status == "queued":
                process_job(db, analyze_job)
            insight = latest_insight(db, project_id)
            snapshot_id = insight.snapshot_id if insight else analyze_job.snapshot_id
        except Exception:
            pass

    status = quota_status(db, project.owner_id, project_id)
    premium = bool(status.get("premium"))
    raw_outputs = data.outputs.model_dump() if data.outputs else {}
    if data.include_detailed and premium:
        raw_outputs["detailed_description"] = True
        raw_outputs["technical_details"] = True
    selection = normalize_outputs(raw_outputs, premium=premium)

    is_regen = bool(data.regenerate or data.parent_draft_id)
    idem = data.idempotency_key or f"gen:{project_id}:{me.id}:{snapshot_id}:{selection}"
    try:
        record, cached_draft = reserve_generation(
            db,
            user_id=me.id,
            project_id=project_id,
            snapshot_id=snapshot_id,
            snapshot_revision=snapshot_revision(db, snapshot_id),
            selection=selection,
            idempotency_key=idem[:200],
            is_regeneration=is_regen,
            advanced=bool(selection.get("architecture_walkthrough") or selection.get("recruiter_report")),
        )
    except PiQuotaExceeded as exc:
        raise HTTPException(status_code=402, detail={"message": str(exc), "quota": exc.status})

    if cached_draft is not None and record.status == "cache_hit":
        db.commit()
        return JobStatusOut(
            job_id=None,
            status="ready",
            job_kind="generate",
            progress={"stage": "cache_hit", "draft_id": str(cached_draft.id), "generation_record_id": str(record.id)},
            error=None,
            cancel_requested=False,
            snapshot_id=snapshot_id,
        )

    qstatus = quota_status(db, project.owner_id, project_id)
    job = enqueue_generate(
        db,
        project,
        requested_by=me.id,
        snapshot_id=snapshot_id,
        parent_draft_id=data.parent_draft_id,
        generation_record_id=record.id,
        output_selection=selection,
        max_output_tokens=qstatus.get("max_output_tokens"),
        idempotency_key=f"job:{idem}"[:200],
    )
    record.job_id = job.id
    db.commit()
    process_job(db, job)
    db.refresh(job)
    if job.status == "failed":
        err = job.error or "AI generation failed"
        lower = err.lower()
        if "consent" in lower:
            raise HTTPException(status_code=403, detail=err)
        if "budget" in lower or "quota" in lower or "allowance" in lower:
            raise HTTPException(status_code=429, detail=err)
        raise HTTPException(status_code=502, detail=err)
    return _job_out(job)


@router.get("/projects/{project_id}/intelligence/drafts", response_model=List[DraftOut])
def get_drafts(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    return [_draft_out(d) for d in list_drafts(db, project_id)]


@router.post("/projects/{project_id}/intelligence/drafts/{draft_id}/approve", response_model=DraftOut)
def approve(
    project_id: UUID,
    draft_id: UUID,
    data: ApproveDraftIn,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    draft = (
        db.query(ProjectAiDescriptionDraft)
        .filter(ProjectAiDescriptionDraft.id == draft_id, ProjectAiDescriptionDraft.project_id == project_id)
        .first()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    try:
        approve_draft(
            db,
            project,
            draft,
            apply_fields={
                "short_description": data.apply_short_description,
                "full_description": data.apply_full_description,
                "publish_simple": data.publish_simple,
                "show_detailed_on_page": data.show_detailed_on_page,
                "adopt_short": data.adopt_short,
                "adopt_detailed": data.adopt_detailed,
            },
            user_id=me.id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(draft)
    return _draft_out(draft)


@router.get("/projects/{project_id}/intelligence/publication", response_model=PublicationOut)
def get_ai_publication(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _require_flag()
    project = _project(db, project_id)
    viewer_id = current_user.id if current_user else None
    if not can_view_project(db, project, viewer_id):
        raise HTTPException(status_code=404, detail="Project not found")
    pub = get_publication(db, project_id)
    if not pub:
        return PublicationOut()
    # Visitors only see published simple / opted-in detailed
    is_team = False
    if current_user:
        try:
            _team(db, project, current_user)
            is_team = True
        except HTTPException:
            is_team = False
    if not is_team and not pub.publish_simple and not pub.show_detailed_on_page:
        return PublicationOut()
    return PublicationOut(
        ai_short_description=pub.ai_short_description if (is_team or pub.publish_simple) else None,
        ai_detailed_description=pub.ai_detailed_description if (is_team or pub.show_detailed_on_page) else None,
        simple_explanation=pub.simple_explanation if (is_team or pub.publish_simple) else {},
        key_features=pub.key_features if (is_team or pub.publish_simple) else [],
        technical_explanation=pub.technical_explanation if is_team or pub.show_detailed_on_page else None,
        resume_bullets=pub.resume_bullets if is_team else [],
        publish_simple=pub.publish_simple,
        show_detailed_on_page=pub.show_detailed_on_page,
        adopted_short=pub.adopted_short if is_team else False,
        adopted_detailed=pub.adopted_detailed if is_team else False,
        has_developer_short_backup=bool(pub.developer_short_backup) if is_team else False,
        has_developer_full_backup=bool(pub.developer_full_backup) if is_team else False,
    )


@router.post("/projects/{project_id}/intelligence/restore-developer")
def restore_developer(
    project_id: UUID,
    body: dict,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    restore_developer_text(
        db,
        project,
        short=bool(body.get("short")),
        full=bool(body.get("full")),
    )
    db.commit()
    return {"ok": True}


@router.post("/projects/{project_id}/intelligence/drafts/{draft_id}/discard", response_model=DraftOut)
def discard_draft(
    project_id: UUID,
    draft_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    draft = (
        db.query(ProjectAiDescriptionDraft)
        .filter(ProjectAiDescriptionDraft.id == draft_id, ProjectAiDescriptionDraft.project_id == project_id)
        .first()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.status = "discarded"
    db.commit()
    db.refresh(draft)
    return _draft_out(draft)


@router.post("/projects/{project_id}/intelligence/drafts/{draft_id}/restore", response_model=DraftOut)
def restore_draft(
    project_id: UUID,
    draft_id: UUID,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    """Restore a discarded draft back to editable draft status (compare/undo support)."""
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    draft = (
        db.query(ProjectAiDescriptionDraft)
        .filter(ProjectAiDescriptionDraft.id == draft_id, ProjectAiDescriptionDraft.project_id == project_id)
        .first()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.status = "draft"
    draft.approved_at = None
    db.commit()
    db.refresh(draft)
    return _draft_out(draft)


@router.post("/projects/{project_id}/intelligence/consent", status_code=201)
def post_consent(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_flag()
    project = _project(db, project_id)
    _team(db, project, me)
    settings = get_project_settings(db, project_id)
    settings.external_ai_allowed = True
    row = grant_external_ai_consent(db, me.id, project_id)
    db.commit()
    return {"consent_id": str(row.id), "purpose": row.purpose}


@router.delete("/projects/{project_id}/intelligence/findings", status_code=204)
def delete_findings(project_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    _require_flag()
    project = _project(db, project_id)
    access = project_access(db, project, me.id)
    if not access.get("owner"):
        raise HTTPException(status_code=403, detail="Only the owner can withdraw cached findings")
    clear_cached_findings(db, project_id)
    db.commit()
    return None


@router.post("/projects/{project_id}/intelligence/talent-link")
def talent_link(
    project_id: UUID,
    attributed: bool = False,
    db: Session = Depends(get_db),
    me: User = Depends(require_developer),
):
    """Create candidate SkillEvidenceRecord rows from framework claims for the project owner only."""
    _require_flag()
    project = _project(db, project_id)
    access = project_access(db, project, me.id)
    if not access.get("owner"):
        raise HTTPException(status_code=403, detail="Only the owner can link talent evidence")
    from app.services.project_intelligence.talent_link import link_framework_claims_as_candidates
    count = link_framework_claims_as_candidates(
        db, user_id=me.id, project_id=project_id, attributed=attributed, audience="connections",
    )
    db.commit()
    return {"linked": count, "attributed": attributed}
