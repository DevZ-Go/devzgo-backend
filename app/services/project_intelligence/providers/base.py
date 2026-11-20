"""AI provider abstraction for Project Intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Type

from pydantic import BaseModel


@dataclass
class ProviderResult:
    data: dict[str, Any]
    model: str
    tokens_in: int
    tokens_out: int
    estimated_cost_usd: float | None
    raw: Any = None
    tokens_cached: int = 0
    incomplete: bool = False
    incomplete_reason: str | None = None


class IncompleteGenerationError(Exception):
    """Model stopped before producing valid structured JSON (often output-token limit)."""

    def __init__(
        self,
        message: str,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        tokens_cached: int = 0,
        estimated_cost_usd: float | None = None,
        model: str | None = None,
        reason: str | None = None,
    ):
        super().__init__(message)
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.tokens_cached = tokens_cached
        self.estimated_cost_usd = estimated_cost_usd
        self.model = model
        self.reason = reason


class GenerationRefusedError(Exception):
    def __init__(self, message: str = "The AI provider declined this request."):
        super().__init__(message)


class AiProvider(Protocol):
    name: str

    def generate_project_description(
        self,
        *,
        system: str,
        user: str,
        schema_model: Type[BaseModel],
        max_output_tokens: int | None = None,
    ) -> ProviderResult:
        ...
