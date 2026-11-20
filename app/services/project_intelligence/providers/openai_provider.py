"""OpenAI Responses API with official structured parse — no blind JSON loads."""

from __future__ import annotations

import json
import os
from typing import Any, Type

from pydantic import BaseModel, ValidationError

from app.services.project_intelligence.constants import MAX_AI_OUTPUT_TOKENS
from app.services.project_intelligence.costing import MODEL_PRICES_USD, actual_cost_usd
from app.services.project_intelligence.providers.base import (
    GenerationRefusedError,
    IncompleteGenerationError,
    ProviderResult,
)


class OpenAIResponsesProvider:
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("PROJECT_AI_MODEL", "gpt-5.4-mini")
        if self.model not in MODEL_PRICES_USD:
            raise RuntimeError(
                f"Model '{self.model}' is not on the approved pricing allowlist. "
                f"Allowed: {', '.join(MODEL_PRICES_USD)}"
            )
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

    def generate_project_description(
        self,
        *,
        system: str,
        user: str,
        schema_model: Type[BaseModel],
        max_output_tokens: int | None = None,
        # legacy kw ignored
        schema: dict | None = None,
    ) -> ProviderResult:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, timeout=90.0, max_retries=1)
        out_tokens = int(max_output_tokens or MAX_AI_OUTPUT_TOKENS)
        # Reasoning models count thinking toward max_output_tokens; keep effort low.
        create_kwargs: dict[str, Any] = {
            "model": self.model,
            "store": False,
            "max_output_tokens": out_tokens,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "text_format": schema_model,
        }
        if self.model.startswith("gpt-5"):
            create_kwargs["reasoning"] = {"effort": "low"}

        try:
            response = client.responses.parse(**create_kwargs)
        except Exception as exc:
            # Fall back to json_schema format if parse path rejects kwargs
            if "text_format" in str(exc) or "unexpected" in str(exc).lower():
                response = self._create_with_json_schema(client, system, user, schema_model, out_tokens)
            else:
                raise

        usage = _usage_tuple(response)
        cost = actual_cost_usd(self.model, usage[0], usage[1])

        status = getattr(response, "status", None)
        incomplete = getattr(response, "incomplete_details", None)
        incomplete_reason = None
        if incomplete is not None:
            incomplete_reason = getattr(incomplete, "reason", None) or str(incomplete)

        # Refusals
        for item in getattr(response, "output", None) or []:
            for content in getattr(item, "content", None) or []:
                if getattr(content, "type", None) == "refusal":
                    raise GenerationRefusedError(
                        getattr(content, "refusal", None) or "The AI provider declined this request."
                    )

        parsed = getattr(response, "output_parsed", None)
        if status == "incomplete" or incomplete_reason:
            raise IncompleteGenerationError(
                "The AI response was cut off before it finished. Please try again — "
                "we keep your project allowance until a complete draft is ready.",
                tokens_in=usage[0],
                tokens_out=usage[1],
                tokens_cached=usage[2],
                estimated_cost_usd=cost,
                model=self.model,
                reason=str(incomplete_reason or status or "incomplete"),
            )

        if parsed is not None:
            if isinstance(parsed, BaseModel):
                data = parsed.model_dump()
            elif isinstance(parsed, dict):
                data = parsed
            else:
                data = schema_model.model_validate(parsed).model_dump()
            return ProviderResult(
                data=data,
                model=self.model,
                tokens_in=usage[0],
                tokens_out=usage[1],
                estimated_cost_usd=cost,
                tokens_cached=usage[2],
            )

        # Fallback: extract text and validate — never json.loads incomplete blobs blindly
        text = _output_text(response)
        if not text or not text.strip():
            raise IncompleteGenerationError(
                "The AI did not return a usable draft. Please try again in a moment.",
                tokens_in=usage[0],
                tokens_out=usage[1],
                tokens_cached=usage[2],
                estimated_cost_usd=cost,
                model=self.model,
                reason="empty",
            )
        if not _looks_like_complete_json(text):
            raise IncompleteGenerationError(
                "The AI response was incomplete. Please try again — "
                "your monthly AI project count is not charged for this attempt.",
                tokens_in=usage[0],
                tokens_out=usage[1],
                tokens_cached=usage[2],
                estimated_cost_usd=cost,
                model=self.model,
                reason="malformed_json",
            )
        try:
            raw = json.loads(text)
            data = schema_model.model_validate(raw).model_dump()
        except (json.JSONDecodeError, ValidationError) as exc:
            raise IncompleteGenerationError(
                "The AI response could not be verified. Please try again — "
                "your monthly AI project count is not charged for this attempt.",
                tokens_in=usage[0],
                tokens_out=usage[1],
                tokens_cached=usage[2],
                estimated_cost_usd=cost,
                model=self.model,
                reason="parse_error",
            ) from exc

        return ProviderResult(
            data=data,
            model=self.model,
            tokens_in=usage[0],
            tokens_out=usage[1],
            estimated_cost_usd=cost,
            tokens_cached=usage[2],
        )

    def _create_with_json_schema(self, client, system, user, schema_model, out_tokens):
        schema = schema_model.model_json_schema()
        # OpenAI strict mode needs additionalProperties false recursively — Pydantic usually ok
        kwargs: dict[str, Any] = {
            "model": self.model,
            "store": False,
            "max_output_tokens": out_tokens,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "project_ai_package",
                    "strict": True,
                    "schema": _strictify(schema),
                }
            },
        }
        if self.model.startswith("gpt-5"):
            kwargs["reasoning"] = {"effort": "low"}
        return client.responses.create(**kwargs)


def _strictify(schema: dict) -> dict:
    """Ensure additionalProperties:false for OpenAI strict structured outputs."""
    out = dict(schema)
    if out.get("type") == "object":
        out["additionalProperties"] = False
        props = out.get("properties") or {}
        out["properties"] = {k: _strictify(v) if isinstance(v, dict) else v for k, v in props.items()}
        if "required" not in out:
            out["required"] = list(props.keys())
    if out.get("type") == "array" and isinstance(out.get("items"), dict):
        out["items"] = _strictify(out["items"])
    if "$defs" in out:
        out["$defs"] = {k: _strictify(v) if isinstance(v, dict) else v for k, v in out["$defs"].items()}
    if "definitions" in out:
        out["definitions"] = {
            k: _strictify(v) if isinstance(v, dict) else v for k, v in out["definitions"].items()
        }
    return out


def _usage_tuple(response: Any) -> tuple[int, int, int]:
    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
    tokens_cached = 0
    if usage is not None:
        details = getattr(usage, "input_tokens_details", None)
        if details is not None:
            tokens_cached = int(getattr(details, "cached_tokens", 0) or 0)
    return tokens_in, tokens_out, tokens_cached


def _output_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text
    chunks: list[str] = []
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            value = getattr(content, "text", None)
            if value:
                chunks.append(value)
    return "".join(chunks)


def _looks_like_complete_json(text: str) -> bool:
    s = text.strip()
    if not s.startswith("{"):
        return False
    # Cheap balance check — incomplete strings/objects fail
    if s.count("{") != s.count("}"):
        return False
    if s.count("[") != s.count("]"):
        return False
    # Odd number of unescaped quotes often means truncated string
    # (approximate; full parse still required)
    return True
