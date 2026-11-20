"""Resolve configured AI provider for Project Intelligence."""

from __future__ import annotations

import os

from app.services.project_intelligence.providers.base import AiProvider
from app.services.project_intelligence.providers.openai_provider import OpenAIResponsesProvider


def get_provider(model: str | None = None) -> AiProvider:
    name = (os.getenv("PROJECT_AI_PROVIDER") or "openai").strip().lower()
    if name == "openai":
        return OpenAIResponsesProvider(model=model)
    if name in {"gemini", "claude"}:
        raise RuntimeError(f"Provider {name} is not implemented yet; set PROJECT_AI_PROVIDER=openai")
    raise RuntimeError(f"Unknown PROJECT_AI_PROVIDER: {name}")
