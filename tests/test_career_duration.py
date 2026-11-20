"""Career duration math: overlaps merge; unknown dates stay unknown."""

from app.services.career_duration import (
    category_totals, format_months, merge_intervals, months_in_intervals, skill_totals, span_from_fields,
)


def test_unknown_when_start_incomplete():
    span = span_from_fields(2020, None, 2021, 6)
    assert span.known is False
    assert format_months(None, known=False)["label"] == "Unknown duration"


def test_overlap_merge_does_not_double_count():
    spans = [
        span_from_fields(2020, 1, 2021, 6),
        span_from_fields(2021, 1, 2022, 1),
    ]
    merged = merge_intervals(spans)
    assert months_in_intervals(merged) == 25  # Jan 2020 through Jan 2022 inclusive


def test_categories_stay_separate():
    rows = [
        {"category": "full_time", "start_year": 2020, "start_month": 1, "end_year": 2021, "end_month": 1, "is_current": False},
        {"category": "internship", "start_year": 2019, "start_month": 5, "end_year": 2019, "end_month": 8, "is_current": False},
        {"category": "academic_project", "start_year": 2018, "start_month": 1, "end_year": 2022, "end_month": 12, "is_current": False},
    ]
    totals = category_totals(rows)
    assert totals["full_time"]["known"] is True
    assert totals["internship"]["months"] == 4
    assert totals["academic_project"]["months"] == 60
    assert totals["full_time"]["months"] != totals["academic_project"]["months"]


def test_skill_intervals_not_full_job_span():
    intervals = [
        {"tech_name": "React", "start_year": 2021, "start_month": 1, "end_year": 2021, "end_month": 6, "is_current": False},
        {"tech_name": "React", "start_year": 2022, "start_month": 1, "end_year": 2022, "end_month": 3, "is_current": False},
    ]
    totals = skill_totals(intervals)
    assert totals["React"]["months"] == 9  # 6 + 3, non-overlapping
