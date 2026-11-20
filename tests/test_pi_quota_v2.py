"""PI 2.0 quota / reservation unit tests (no OpenAI calls)."""

from __future__ import annotations

import uuid

import pytest

from app.services.project_intelligence.costing import estimate_worst_case_usd, utc_year_month
from app.services.project_intelligence.quota import (
    DEFAULT_PI_SETTINGS,
    make_cache_key,
    normalize_outputs,
    output_fingerprint,
)


def test_normalize_outputs_defaults_recommend_simple_english():
    out = normalize_outputs(None)
    assert out["simple_english"] is True
    assert out["short_description"] is True
    assert out["detailed_description"] is False  # Premium opt-in only


def test_cache_key_stable_for_same_inputs():
    pid = uuid.uuid4()
    sel = normalize_outputs({"simple_english": True, "short_description": True})
    a = make_cache_key(project_id=pid, snapshot_revision="abc", selection=sel, model="gpt-5.4-mini")
    b = make_cache_key(project_id=pid, snapshot_revision="abc", selection=sel, model="gpt-5.4-mini")
    assert a == b
    c = make_cache_key(project_id=pid, snapshot_revision="abc", selection=sel, model="gpt-5-mini")
    assert a != c


def test_output_fingerprint_order_independent():
    a = output_fingerprint({"short_description": True, "simple_english": False})
    b = output_fingerprint({"simple_english": False, "short_description": True})
    assert a == b


def test_estimate_rejects_unknown_model():
    with pytest.raises(ValueError):
        estimate_worst_case_usd("unknown-model-xyz", 1000, 100)


def test_estimate_known_model_positive():
    cost = estimate_worst_case_usd("gpt-5.4-mini", 6000, 900)
    assert cost > 0
    assert cost < float(DEFAULT_PI_SETTINGS["per_request_cost_ceiling_usd"]) * 2


def test_year_month_format():
    ym = utc_year_month()
    assert len(ym) == 7
    assert ym[4] == "-"
