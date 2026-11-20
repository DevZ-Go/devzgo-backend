"""Regression: incomplete / malformed AI JSON must not be parsed blindly."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.project_intelligence.providers.base import IncompleteGenerationError
from app.services.project_intelligence.providers.openai_provider import (
    OpenAIResponsesProvider,
    _looks_like_complete_json,
)
from app.services.project_intelligence.schemas_ai import FreeAiPackageSchema
from app.services.project_intelligence.quota import normalize_outputs


def test_looks_like_complete_json_rejects_truncated_string():
    # Simulates the production failure: unterminated string mid-JSON
    bad = '{"short_description": "Hello", "how_it_works_simple": "This project is a '
    assert _looks_like_complete_json(bad) is False


def test_looks_like_complete_json_accepts_balanced():
    good = json.dumps({"short_description": "Hi", "how_it_works_simple": "It helps people."})
    assert _looks_like_complete_json(good) is True


def test_normalize_forces_simple_english_and_blocks_free_detailed():
    free = normalize_outputs({"detailed_description": True, "simple_english": False}, premium=False)
    assert free["simple_english"] is True
    assert free["detailed_description"] is False
    prem = normalize_outputs({"detailed_description": True}, premium=True)
    assert prem["detailed_description"] is True
    assert prem["simple_english"] is True


def test_provider_raises_on_incomplete_status(monkeypatch):
    provider = OpenAIResponsesProvider.__new__(OpenAIResponsesProvider)
    provider.model = "gpt-5.4-mini"
    provider.api_key = "sk-test"
    provider.name = "openai"

    class FakeClient:
        class responses:
            @staticmethod
            def parse(**kwargs):
                return SimpleNamespace(
                    status="incomplete",
                    incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                    output_parsed=None,
                    output=[],
                    output_text='{"short_description": "cut off',
                    usage=SimpleNamespace(
                        input_tokens=100,
                        output_tokens=900,
                        input_tokens_details=SimpleNamespace(cached_tokens=0),
                    ),
                )

    monkeypatch.setattr("openai.OpenAI", lambda **kw: FakeClient())

    with pytest.raises(IncompleteGenerationError) as ei:
        OpenAIResponsesProvider.generate_project_description(
            provider,
            system="sys",
            user="user",
            schema_model=FreeAiPackageSchema,
            max_output_tokens=900,
        )
    assert ei.value.tokens_out == 900
    assert ei.value.reason == "max_output_tokens"


def test_provider_raises_on_malformed_complete_status(monkeypatch):
    provider = OpenAIResponsesProvider.__new__(OpenAIResponsesProvider)
    provider.model = "gpt-5.4-mini"
    provider.api_key = "sk-test"
    provider.name = "openai"

    class FakeClient:
        class responses:
            @staticmethod
            def parse(**kwargs):
                return SimpleNamespace(
                    status="completed",
                    incomplete_details=None,
                    output_parsed=None,
                    output=[],
                    output_text='{"short_description": "Unterminated string starting at',
                    usage=SimpleNamespace(
                        input_tokens=50,
                        output_tokens=400,
                        input_tokens_details=None,
                    ),
                )

    monkeypatch.setattr("openai.OpenAI", lambda **kw: FakeClient())

    with pytest.raises(IncompleteGenerationError) as ei:
        OpenAIResponsesProvider.generate_project_description(
            provider,
            system="sys",
            user="user",
            schema_model=FreeAiPackageSchema,
            max_output_tokens=900,
        )
    assert ei.value.reason == "malformed_json"
