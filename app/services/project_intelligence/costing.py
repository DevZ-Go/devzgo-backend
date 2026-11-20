"""Model allowlist + cost estimation / budget checks for Project Intelligence."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.project_intelligence import PiGenerationRecord, ProjectAiUsageLedger

# USD per 1M tokens (input, output). Unknown models are rejected.
MODEL_PRICES_USD: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5-mini": (0.25, 2.00),
}


def utc_year_month(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def utc_day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def estimate_worst_case_usd(model: str, max_in: int, max_out: int) -> float:
    prices = MODEL_PRICES_USD.get(model)
    if not prices:
        raise ValueError(f"Model '{model}' is not on the approved pricing allowlist")
    pin, pout = prices
    return round((max_in / 1_000_000) * pin + (max_out / 1_000_000) * pout, 6)


def actual_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float | None:
    prices = MODEL_PRICES_USD.get(model)
    if not prices:
        return None
    pin, pout = prices
    return round((tokens_in / 1_000_000) * pin + (tokens_out / 1_000_000) * pout, 6)


def pick_model(settings: dict, *, premium: bool, advanced: bool = False) -> str:
    approved = list(settings.get("approved_models") or list(MODEL_PRICES_USD.keys()))
    preferred = settings.get("premium_model") if (premium and advanced) else settings.get("default_model")
    preferred = preferred or "gpt-5.4-mini"
    if preferred not in approved:
        preferred = approved[0] if approved else "gpt-5.4-mini"
    if preferred not in MODEL_PRICES_USD:
        raise ValueError(f"Model '{preferred}' has no known pricing")
    return preferred


def sum_cost_since(db: Session, *, since: datetime, user_id: UUID | None = None) -> float:
    q = db.query(func.coalesce(func.sum(ProjectAiUsageLedger.estimated_cost_usd), 0.0)).filter(
        ProjectAiUsageLedger.created_at >= since
    )
    if user_id is not None:
        q = q.filter(ProjectAiUsageLedger.user_id == user_id)
    return float(q.scalar() or 0.0)


def reserved_open_cost(db: Session, *, user_id: UUID | None = None) -> float:
    q = db.query(func.coalesce(func.sum(PiGenerationRecord.reserved_cost_usd), 0.0)).filter(
        PiGenerationRecord.status == "reserved"
    )
    if user_id is not None:
        q = q.filter(PiGenerationRecord.user_id == user_id)
    return float(q.scalar() or 0.0)


def assert_budget_allows(db: Session, settings: dict, *, user_id: UUID, estimate: float) -> None:
    if estimate > float(settings["per_request_cost_ceiling_usd"]):
        raise BudgetExceeded("This request exceeds the per-request cost ceiling.")

    day_start = utc_day_start()
    month = utc_year_month()
    user_day = sum_cost_since(db, since=day_start, user_id=user_id) + reserved_open_cost(db, user_id=user_id)
    if user_day + estimate > float(settings["user_daily_cost_ceiling_usd"]):
        raise BudgetExceeded("Daily AI spending limit reached. Try again tomorrow.")

    # Monthly from ledger + open reservations (approx)
    month_start = datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
    user_month = sum_cost_since(db, since=month_start, user_id=user_id) + reserved_open_cost(db, user_id=user_id)
    if user_month + estimate > float(settings["user_monthly_cost_ceiling_usd"]):
        raise BudgetExceeded("Monthly AI spending limit reached for your account.")

    global_day = sum_cost_since(db, since=day_start) + reserved_open_cost(db)
    if global_day + estimate > float(settings["global_daily_budget_usd"]):
        raise BudgetExceeded("Platform AI daily budget is exhausted. Generation is temporarily unavailable.")

    global_month = sum_cost_since(db, since=month_start) + reserved_open_cost(db)
    if global_month + estimate > float(settings["global_monthly_budget_usd"]):
        raise BudgetExceeded("Platform AI monthly budget is exhausted. Generation is temporarily unavailable.")

    active = (
        db.query(func.count(PiGenerationRecord.id))
        .filter(PiGenerationRecord.status == "reserved")
        .scalar()
        or 0
    )
    if int(active) >= int(settings["max_concurrent_paid_calls"]):
        raise BudgetExceeded("Too many AI generations are running. Please wait a moment and try again.")


class BudgetExceeded(Exception):
    pass
