"""Calendar-month experience duration. Overlaps are merged; unknown dates stay unknown."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional


@dataclass(frozen=True)
class MonthSpan:
    start: Optional[int]  # months since year 0 (year*12 + month)
    end: Optional[int]
    known: bool


def to_month_index(year: Optional[int], month: Optional[int]) -> Optional[int]:
    if year is None or month is None:
        return None
    if not (1 <= month <= 12) or year < 1900 or year > 2100:
        return None
    return year * 12 + (month - 1)


def current_month_index(today: Optional[date] = None) -> int:
    today = today or date.today()
    return today.year * 12 + (today.month - 1)


def span_from_fields(
    start_year: Optional[int],
    start_month: Optional[int],
    end_year: Optional[int],
    end_month: Optional[int],
    *,
    is_current: bool = False,
    today: Optional[date] = None,
) -> MonthSpan:
    start = to_month_index(start_year, start_month)
    if start is None:
        return MonthSpan(None, None, known=False)
    if is_current:
        end = current_month_index(today)
    else:
        end = to_month_index(end_year, end_month)
        if end is None:
            return MonthSpan(start, None, known=False)
    if end < start:
        return MonthSpan(None, None, known=False)
    return MonthSpan(start, end, known=True)


def merge_intervals(spans: Iterable[MonthSpan]) -> list[tuple[int, int]]:
    """Merge overlapping inclusive month intervals. Unknown spans are skipped."""
    intervals = sorted((s.start, s.end) for s in spans if s.known and s.start is not None and s.end is not None)
    if not intervals:
        return []
    merged: list[list[int]] = [[intervals[0][0], intervals[0][1]]]
    for start, end in intervals[1:]:
        last = merged[-1]
        if start <= last[1] + 1:
            last[1] = max(last[1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def months_in_intervals(intervals: list[tuple[int, int]]) -> int:
    return sum(end - start + 1 for start, end in intervals)


def format_months(total_months: Optional[int], *, known: bool) -> dict:
    if not known or total_months is None:
        return {"known": False, "months": None, "years": None, "label": "Unknown duration"}
    years, months = divmod(total_months, 12)
    if years and months:
        label = f"{years} year{'s' if years != 1 else ''} {months} month{'s' if months != 1 else ''}"
    elif years:
        label = f"{years} year{'s' if years != 1 else ''}"
    else:
        label = f"{months} month{'s' if months != 1 else ''}"
    return {"known": True, "months": total_months, "years": round(total_months / 12, 2), "label": label}


def category_totals(experiences: list[dict], *, today: Optional[date] = None) -> dict[str, dict]:
    """experiences: dicts with category + date fields. Academic/independent stay separate."""
    by_cat: dict[str, list[MonthSpan]] = {}
    for row in experiences:
        span = span_from_fields(
            row.get("start_year"), row.get("start_month"), row.get("end_year"), row.get("end_month"),
            is_current=bool(row.get("is_current")), today=today,
        )
        by_cat.setdefault(row["category"], []).append(span)
    out: dict[str, dict] = {}
    for category, spans in by_cat.items():
        known_spans = [s for s in spans if s.known]
        if not known_spans and spans:
            out[category] = format_months(None, known=False)
            continue
        total = months_in_intervals(merge_intervals(known_spans))
        out[category] = format_months(total, known=True)
    return out


def skill_totals(intervals: list[dict], *, today: Optional[date] = None) -> dict[str, dict]:
    """Skill-specific duration from confirmed usage intervals only — never the full job span."""
    by_skill: dict[str, list[MonthSpan]] = {}
    for row in intervals:
        key = str(row.get("tech_name") or row.get("tech_stack_id") or "")
        if not key:
            continue
        span = span_from_fields(
            row.get("start_year"), row.get("start_month"), row.get("end_year"), row.get("end_month"),
            is_current=bool(row.get("is_current")), today=today,
        )
        by_skill.setdefault(key, []).append(span)
    return {
        skill: format_months(months_in_intervals(merge_intervals(spans)), known=any(s.known for s in spans))
        if any(s.known for s in spans) else format_months(None, known=False)
        for skill, spans in by_skill.items()
    }


# Milestone thresholds in months for Experience badges.
EXPERIENCE_MILESTONES = (6, 12, 24, 36, 60, 120)
PROFESSIONAL_CATEGORIES = {"full_time", "part_time", "contract", "freelance"}
INTERNSHIP_CATEGORIES = {"coop", "internship"}
# Never produce "years of professional employment" from these.
NON_EMPLOYMENT_CATEGORIES = {"academic_project", "independent_project", "volunteer", "other"}
