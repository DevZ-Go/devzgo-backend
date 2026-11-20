"""PI 2.0 quotas: calendar-month UTC AI packages, regenerations, concurrency, admin settings.

Deterministic analyze never consumes AI package entitlement.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.platform_settings import PI_SETTINGS_KEY, PlatformSetting
from app.models.project_intelligence import PiGenerationRecord, PiMonthlyUsage, ProjectAiDescriptionDraft
from app.services.entitlements import has_feature
from app.services.project_intelligence.constants import ANALYZER_VERSION, PROMPT_VERSION
from app.services.project_intelligence.costing import (
    BudgetExceeded,
    assert_budget_allows,
    estimate_worst_case_usd,
    pick_model,
    utc_year_month,
)

FEATURE_PI_PREMIUM = "project_intelligence"

DEFAULT_PI_SETTINGS: dict = {
    "free_project_limit": 10,
    "premium_project_limit": 30,
    "free_regenerations_per_project": 1,
    "premium_regenerations_per_project": 3,
    "free_concurrent": 1,
    "premium_concurrent": 2,
    # Output budgets must leave headroom for reasoning-model thinking tokens.
    "free_max_input_tokens": 6000,
    "free_max_output_tokens": 2000,
    "premium_max_input_tokens": 12000,
    "premium_max_output_tokens": 3500,
    "per_request_cost_ceiling_usd": 0.15,
    "user_daily_cost_ceiling_usd": 1.0,
    "user_monthly_cost_ceiling_usd": 5.0,
    "global_daily_budget_usd": 50.0,
    "global_monthly_budget_usd": 500.0,
    "max_concurrent_paid_calls": 8,
    "visitor_insights_enabled": True,
    "visitor_ai_enabled": True,
    "visitor_ai_project_limit": 5,
    "ai_generate_enabled": True,
    "auto_analyze_on_upload": True,
    "approved_models": ["gpt-5.4-mini", "gpt-5-mini"],
    "default_model": "gpt-5.4-mini",
    "premium_model": "gpt-5.4-mini",
}

_INT_KEYS = {
    "free_project_limit",
    "premium_project_limit",
    "free_regenerations_per_project",
    "premium_regenerations_per_project",
    "free_concurrent",
    "premium_concurrent",
    "free_max_input_tokens",
    "free_max_output_tokens",
    "premium_max_input_tokens",
    "premium_max_output_tokens",
    "max_concurrent_paid_calls",
    "visitor_ai_project_limit",
}
_FLOAT_KEYS = {
    "per_request_cost_ceiling_usd",
    "user_daily_cost_ceiling_usd",
    "user_monthly_cost_ceiling_usd",
    "global_daily_budget_usd",
    "global_monthly_budget_usd",
}
_BOOL_KEYS = {
    "visitor_insights_enabled",
    "visitor_ai_enabled",
    "ai_generate_enabled",
    "auto_analyze_on_upload",
}


def get_pi_settings(db: Session) -> dict:
    row = db.query(PlatformSetting).filter(PlatformSetting.key == PI_SETTINGS_KEY).first()
    raw = dict(DEFAULT_PI_SETTINGS)
    if row and isinstance(row.value, dict):
        raw.update(row.value)
    return _coerce_settings(raw)


def set_pi_settings(db: Session, patch: dict, *, updated_by: UUID | None = None) -> dict:
    current = get_pi_settings(db)
    for key, value in (patch or {}).items():
        if key in DEFAULT_PI_SETTINGS:
            current[key] = value
    current = _coerce_settings(current)
    row = db.query(PlatformSetting).filter(PlatformSetting.key == PI_SETTINGS_KEY).first()
    if row:
        row.value = current
        row.updated_by = updated_by
    else:
        db.add(PlatformSetting(key=PI_SETTINGS_KEY, value=current, updated_by=updated_by))
    db.flush()
    return current


def _coerce_settings(raw: dict) -> dict:
    out = dict(DEFAULT_PI_SETTINGS)
    out.update(raw or {})
    for key in _INT_KEYS:
        try:
            out[key] = max(0, int(out.get(key, DEFAULT_PI_SETTINGS[key])))
        except (TypeError, ValueError):
            out[key] = DEFAULT_PI_SETTINGS[key]
    for key in _FLOAT_KEYS:
        try:
            out[key] = max(0.0, float(out.get(key, DEFAULT_PI_SETTINGS[key])))
        except (TypeError, ValueError):
            out[key] = DEFAULT_PI_SETTINGS[key]
    for key in _BOOL_KEYS:
        out[key] = bool(out.get(key, DEFAULT_PI_SETTINGS[key]))
    models = out.get("approved_models") or DEFAULT_PI_SETTINGS["approved_models"]
    if not isinstance(models, list) or not models:
        models = list(DEFAULT_PI_SETTINGS["approved_models"])
    out["approved_models"] = [str(m) for m in models]
    out["default_model"] = str(out.get("default_model") or "gpt-5.4-mini")
    out["premium_model"] = str(out.get("premium_model") or out["default_model"])
    return out


def normalize_outputs(selection: dict | None, *, premium: bool = False) -> dict:
    """Bundle selection. Simple English is always included. Long description is Premium-only."""
    sel = selection or {}
    detailed = bool(sel.get("detailed_description", False)) if premium else False
    return {
        "short_description": True,
        "detailed_description": detailed,
        "simple_english": True,  # compulsory whenever AI generation succeeds
        "technical_details": bool(sel.get("technical_details", detailed)) if premium else False,
        "architecture_walkthrough": bool(sel.get("architecture_walkthrough", False)) if premium else False,
        "recruiter_report": bool(sel.get("recruiter_report", False)) if premium else False,
    }


def output_fingerprint(selection: dict) -> str:
    norm = normalize_outputs(selection)
    blob = json.dumps(norm, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def make_cache_key(
    *,
    project_id: UUID,
    snapshot_revision: str,
    selection: dict,
    model: str,
    prompt_version: str = PROMPT_VERSION,
    analyzer_version: str = ANALYZER_VERSION,
) -> str:
    raw = "|".join(
        [
            str(project_id),
            snapshot_revision or "none",
            output_fingerprint(selection),
            model,
            prompt_version,
            analyzer_version,
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:48]


def _month_row(db: Session, user_id: UUID, year_month: str | None = None) -> PiMonthlyUsage:
    ym = year_month or utc_year_month()
    row = (
        db.query(PiMonthlyUsage)
        .filter(PiMonthlyUsage.user_id == user_id, PiMonthlyUsage.year_month == ym)
        .with_for_update()
        .first()
    )
    if row:
        return row
    row = PiMonthlyUsage(user_id=user_id, year_month=ym)
    db.add(row)
    db.flush()
    return row


def project_has_successful_ai(db: Session, project_id: UUID) -> bool:
    return (
        db.query(PiGenerationRecord.id)
        .filter(
            PiGenerationRecord.project_id == project_id,
            PiGenerationRecord.status.in_(("succeeded", "cache_hit")),
            PiGenerationRecord.kind == "initial",
        )
        .first()
        is not None
    )


def regenerations_used_for_project(db: Session, project_id: UUID) -> int:
    return int(
        db.query(func.count(PiGenerationRecord.id))
        .filter(
            PiGenerationRecord.project_id == project_id,
            PiGenerationRecord.kind == "regeneration",
            PiGenerationRecord.status.in_(("succeeded", "cache_hit", "reserved")),
        )
        .scalar()
        or 0
    )


def concurrent_for_user(db: Session, user_id: UUID) -> int:
    return int(
        db.query(func.count(PiGenerationRecord.id))
        .filter(PiGenerationRecord.user_id == user_id, PiGenerationRecord.status == "reserved")
        .scalar()
        or 0
    )


def quota_status(db: Session, owner_id: UUID, project_id: UUID | None = None) -> dict:
    settings = get_pi_settings(db)
    premium = has_feature(db, owner_id, FEATURE_PI_PREMIUM)
    ym = utc_year_month()
    month = (
        db.query(PiMonthlyUsage)
        .filter(PiMonthlyUsage.user_id == owner_id, PiMonthlyUsage.year_month == ym)
        .first()
    )
    used = int(month.ai_projects_used) if month else 0
    limit = int(settings["premium_project_limit"] if premium else settings["free_project_limit"])
    already = bool(project_id and project_has_successful_ai(db, project_id))
    remaining = max(0, limit - used)
    can_generate_new = already or remaining > 0
    regen_limit = int(
        settings["premium_regenerations_per_project"] if premium else settings["free_regenerations_per_project"]
    )
    regen_used = regenerations_used_for_project(db, project_id) if project_id else 0
    concurrent_limit = int(settings["premium_concurrent"] if premium else settings["free_concurrent"])
    # Deterministic analyze is always allowed (abuse limited elsewhere)
    can_analyze = True
    return {
        "premium": premium,
        "plan": "premium" if premium else "free",
        "year_month": ym,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "can_analyze": can_analyze,
        "can_generate": bool(settings["ai_generate_enabled"]) and can_generate_new,
        "project_already_counted": already,
        "project_has_ai": already,
        "regenerations_used": regen_used,
        "regenerations_limit": regen_limit,
        "can_regenerate": already and regen_used < regen_limit,
        "concurrent_limit": concurrent_limit,
        "concurrent_active": concurrent_for_user(db, owner_id),
        "ai_generate_enabled": bool(settings["ai_generate_enabled"]),
        "visitor_insights_enabled": bool(settings["visitor_insights_enabled"]),
        "auto_analyze_on_upload": bool(settings["auto_analyze_on_upload"]),
        "max_input_tokens": int(
            settings["premium_max_input_tokens"] if premium else settings["free_max_input_tokens"]
        ),
        "max_output_tokens": int(
            settings["premium_max_output_tokens"] if premium else settings["free_max_output_tokens"]
        ),
        "exhausted_message": (
            None
            if can_generate_new
            else (
                f"You've used your {limit} AI projects this month. Your allowance renews next month. "
                "You can still upload projects and use automatic code insights. "
                "Your saved AI explanations remain available."
            )
        ),
    }


class QuotaExceeded(Exception):
    def __init__(self, message: str, status: dict | None = None):
        super().__init__(message)
        self.status = status or {}


def assert_can_analyze(db: Session, owner_id: UUID, project_id: UUID) -> dict:
    """Deterministic analyze is free; only blocked by feature kill switches elsewhere."""
    status = quota_status(db, owner_id, project_id)
    return status


def assert_can_generate_package(
    db: Session,
    owner_id: UUID,
    project_id: UUID,
    *,
    is_regeneration: bool,
) -> dict:
    settings = get_pi_settings(db)
    status = quota_status(db, owner_id, project_id)
    if not settings.get("ai_generate_enabled", True):
        raise QuotaExceeded("AI description generation is disabled by an administrator.", status)
    if status["concurrent_active"] >= status["concurrent_limit"]:
        raise QuotaExceeded("You already have an AI generation in progress. Please wait.", status)
    if is_regeneration:
        if not status["project_has_ai"]:
            raise QuotaExceeded("Generate an initial AI package before regenerating.", status)
        if not status["can_regenerate"]:
            raise QuotaExceeded(
                f"Regeneration limit reached for this project ({status['regenerations_used']}/{status['regenerations_limit']}).",
                status,
            )
    else:
        if not status["can_generate"]:
            raise QuotaExceeded(status["exhausted_message"] or "Monthly AI project allowance reached.", status)
    return status


def find_cached_generation(db: Session, cache_key: str) -> PiGenerationRecord | None:
    return (
        db.query(PiGenerationRecord)
        .filter(
            PiGenerationRecord.cache_key == cache_key,
            PiGenerationRecord.status.in_(("succeeded", "cache_hit")),
            PiGenerationRecord.draft_id.isnot(None),
        )
        .order_by(PiGenerationRecord.created_at.desc())
        .first()
    )


def reserve_generation(
    db: Session,
    *,
    user_id: UUID,
    project_id: UUID,
    snapshot_id: UUID | None,
    snapshot_revision: str,
    selection: dict,
    idempotency_key: str,
    is_regeneration: bool,
    advanced: bool = False,
    community: bool = False,
    admin: bool = False,
    force: bool = False,
) -> tuple[PiGenerationRecord, ProjectAiDescriptionDraft | None]:
    """Atomically reserve quota or return a cache hit draft (no charge).

    community=True: visitor/community summaries — still cost-protected, but does not
    consume the user's monthly owner AI-project entitlement (tracked separately).
    admin=True: platform admin regenerate — cost-protected, never burns user allowance.
    force=True: skip successful cache hits (always run a new model call when reserved).
    """
    settings = get_pi_settings(db)
    if community or admin:
        # Use free-tier token budgets; skip owner package entitlement checks.
        premium = False
        status = {
            "premium": False,
            "project_has_ai": project_has_successful_ai(db, project_id),
            "max_input_tokens": int(settings["free_max_input_tokens"]),
            "max_output_tokens": int(settings["free_max_output_tokens"]),
            "limit": int(settings["free_project_limit"]),
            "exhausted_message": None,
        }
    else:
        status = assert_can_generate_package(db, user_id, project_id, is_regeneration=is_regeneration)
        premium = status["premium"]
    model = pick_model(settings, premium=premium, advanced=advanced and not community and not admin)
    selection = normalize_outputs(selection, premium=premium and not community and not admin)
    # Community/admin packages always include simple English + short; never long premium fields
    if community or admin:
        selection = normalize_outputs({"detailed_description": False}, premium=False)
    cache_key = make_cache_key(
        project_id=project_id,
        snapshot_revision=snapshot_revision,
        selection=selection,
        model=model,
    )
    if force:
        cache_key = f"{cache_key}:force:{idempotency_key[-24:]}"

    existing_idem = (
        db.query(PiGenerationRecord)
        .filter(PiGenerationRecord.idempotency_key == idempotency_key)
        .first()
    )
    if existing_idem:
        draft = None
        if existing_idem.draft_id:
            draft = db.query(ProjectAiDescriptionDraft).filter_by(id=existing_idem.draft_id).first()
        return existing_idem, draft

    if not force:
        cached = find_cached_generation(db, cache_key)
        if cached and cached.draft_id:
            draft = db.query(ProjectAiDescriptionDraft).filter_by(id=cached.draft_id).first()
            kind = (
                "admin"
                if admin
                else ("community" if community else ("regeneration" if is_regeneration else "initial"))
            )
            hit = PiGenerationRecord(
                id=uuid.uuid4(),
                user_id=user_id,
                project_id=project_id,
                snapshot_id=snapshot_id,
                draft_id=cached.draft_id,
                idempotency_key=idempotency_key,
                cache_key=cache_key,
                kind=kind,
                status="cache_hit",
                output_selection=selection,
                model=model,
                prompt_version=PROMPT_VERSION,
                analyzer_version=ANALYZER_VERSION,
                year_month=utc_year_month(),
                counts_as_project=False,
                completed_at=datetime.now(timezone.utc),
            )
            db.add(hit)
            db.flush()
            return hit, draft

    max_in = status["max_input_tokens"]
    max_out = status["max_output_tokens"]
    estimate = estimate_worst_case_usd(model, max_in, max_out)
    try:
        assert_budget_allows(db, settings, user_id=user_id, estimate=estimate)
    except BudgetExceeded as exc:
        raise QuotaExceeded(str(exc), status) from exc

    month = _month_row(db, user_id)
    counts_as_project = (
        (not community)
        and (not admin)
        and (not is_regeneration)
        and (not status["project_has_ai"])
    )
    if counts_as_project and month.ai_projects_used >= status["limit"]:
        raise QuotaExceeded(status["exhausted_message"] or "Monthly AI project allowance reached.", status)

    if counts_as_project:
        month.ai_projects_used = int(month.ai_projects_used) + 1
    if is_regeneration and not community and not admin:
        month.regenerations_used = int(month.regenerations_used) + 1
    month.requests_count = int(month.requests_count) + 1

    kind = (
        "admin"
        if admin
        else ("community" if community else ("regeneration" if is_regeneration else "initial"))
    )
    record = PiGenerationRecord(
        id=uuid.uuid4(),
        user_id=user_id,
        project_id=project_id,
        snapshot_id=snapshot_id,
        idempotency_key=idempotency_key,
        cache_key=cache_key,
        kind=kind,
        status="reserved",
        output_selection=selection,
        model=model,
        prompt_version=PROMPT_VERSION,
        analyzer_version=ANALYZER_VERSION,
        year_month=utc_year_month(),
        counts_as_project=counts_as_project,
        reserved_cost_usd=estimate,
    )
    db.add(record)
    db.flush()
    return record, None


def commit_generation(
    db: Session,
    record: PiGenerationRecord,
    *,
    draft_id: UUID,
    job_id: UUID | None,
    tokens_in: int,
    tokens_out: int,
    tokens_cached: int = 0,
    actual_cost: float | None,
) -> None:
    record.status = "succeeded"
    record.draft_id = draft_id
    record.job_id = job_id
    record.tokens_in = tokens_in
    record.tokens_out = tokens_out
    record.tokens_cached = tokens_cached
    record.actual_cost_usd = actual_cost
    record.completed_at = datetime.now(timezone.utc)
    month = _month_row(db, record.user_id, record.year_month)
    month.tokens_in = int(month.tokens_in) + int(tokens_in)
    month.tokens_out = int(month.tokens_out) + int(tokens_out)
    if actual_cost:
        month.cost_usd = float(month.cost_usd) + float(actual_cost)
    db.flush()


def release_generation(db: Session, record: PiGenerationRecord, *, error: str | None = None) -> None:
    if record.status != "reserved":
        return
    record.status = "released"
    record.error = (error or "")[:2000]
    record.completed_at = datetime.now(timezone.utc)
    month = _month_row(db, record.user_id, record.year_month)
    if record.counts_as_project and month.ai_projects_used > 0:
        month.ai_projects_used = int(month.ai_projects_used) - 1
    if record.kind == "regeneration" and month.regenerations_used > 0:
        month.regenerations_used = int(month.regenerations_used) - 1
    db.flush()


def admin_usage_overview(db: Session) -> dict:
    settings = get_pi_settings(db)
    ym = utc_year_month()
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = datetime.strptime(ym + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)

    def _sum_cost(since: datetime) -> float:
        from app.models.project_intelligence import ProjectAiUsageLedger

        return float(
            db.query(func.coalesce(func.sum(ProjectAiUsageLedger.estimated_cost_usd), 0.0))
            .filter(ProjectAiUsageLedger.created_at >= since)
            .scalar()
            or 0.0
        )

    def _sum_tokens(since: datetime) -> tuple[int, int]:
        from app.models.project_intelligence import ProjectAiUsageLedger

        tin = int(
            db.query(func.coalesce(func.sum(ProjectAiUsageLedger.tokens_in), 0))
            .filter(ProjectAiUsageLedger.created_at >= since)
            .scalar()
            or 0
        )
        tout = int(
            db.query(func.coalesce(func.sum(ProjectAiUsageLedger.tokens_out), 0))
            .filter(ProjectAiUsageLedger.created_at >= since)
            .scalar()
            or 0
        )
        return tin, tout

    tin_d, tout_d = _sum_tokens(day_start)
    tin_m, tout_m = _sum_tokens(month_start)
    req_m = int(
        db.query(func.count(PiGenerationRecord.id))
        .filter(PiGenerationRecord.year_month == ym, PiGenerationRecord.status.in_(("succeeded", "cache_hit", "failed")))
        .scalar()
        or 0
    )
    failed_m = int(
        db.query(func.count(PiGenerationRecord.id))
        .filter(PiGenerationRecord.year_month == ym, PiGenerationRecord.status == "failed")
        .scalar()
        or 0
    )
    enhanced = int(
        db.query(func.count(func.distinct(PiGenerationRecord.project_id)))
        .filter(PiGenerationRecord.year_month == ym, PiGenerationRecord.status.in_(("succeeded", "cache_hit")))
        .scalar()
        or 0
    )
    near = (
        db.query(PiMonthlyUsage)
        .filter(PiMonthlyUsage.year_month == ym, PiMonthlyUsage.ai_projects_used >= max(1, int(settings["free_project_limit"]) - 2))
        .order_by(PiMonthlyUsage.ai_projects_used.desc())
        .limit(20)
        .all()
    )
    premium_active = 0
    try:
        from app.models.entitlements import Entitlement

        premium_active = int(
            db.query(func.count(Entitlement.id))
            .filter(Entitlement.feature == FEATURE_PI_PREMIUM)
            .scalar()
            or 0
        )
    except Exception:
        premium_active = 0

    return {
        "year_month": ym,
        "settings": settings,
        "daily_spend_usd": _sum_cost(day_start),
        "monthly_spend_usd": _sum_cost(month_start),
        "daily_tokens_in": tin_d,
        "daily_tokens_out": tout_d,
        "monthly_tokens_in": tin_m,
        "monthly_tokens_out": tout_m,
        "monthly_requests": req_m,
        "failed_generations": failed_m,
        "unique_enhanced_projects": enhanced,
        "active_premium_entitlements": premium_active,
        "ai_generate_enabled": bool(settings["ai_generate_enabled"]),
        "near_quota_users": [
            {
                "user_id": str(r.user_id),
                "ai_projects_used": r.ai_projects_used,
                "regenerations_used": r.regenerations_used,
                "cost_usd": r.cost_usd,
            }
            for r in near
        ],
    }
