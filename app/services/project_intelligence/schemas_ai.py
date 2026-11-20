"""Compact Pydantic schemas for Free vs Premium AI packages (Responses API parse)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class EvidenceRefOut(BaseModel):
    claim_id: Optional[str] = None
    source_file: Optional[str] = None
    note: Optional[str] = None


class FreeAiPackageSchema(BaseModel):
    """Flexible plain-English project brief. Omit weak fields rather than saying 'not confirmed'."""

    short_description: str = Field(..., max_length=280)
    headline: str = Field(..., max_length=160)
    overview: str = Field(..., max_length=700)
    features: List[str] = Field(default_factory=list, max_length=6)
    purpose: str = Field(default="", max_length=400)
    audience: str = Field(default="", max_length=300)
    context: str = Field(default="", max_length=300)
    scope: str = Field(default="", max_length=300)
    workflow: str = Field(default="", max_length=700)
    limitations: List[str] = Field(default_factory=list, max_length=4)
    category: str = Field(default="", max_length=40)
    stack_nature: str = Field(default="", max_length=40)
    standalone: Optional[bool] = None
    # Legacy aliases still accepted from older model responses
    what_is_this: str = Field(default="", max_length=400)
    what_it_does: str = Field(default="", max_length=500)
    why_use_it: str = Field(default="", max_length=400)
    who_benefits: str = Field(default="", max_length=300)
    how_it_works_simple: str = Field(default="", max_length=700)
    key_features: List[str] = Field(default_factory=list, max_length=6)
    uncertainties: List[str] = Field(default_factory=list, max_length=4)
    evidence_refs: List[EvidenceRefOut] = Field(default_factory=list, max_length=8)

    @field_validator(
        "features",
        "key_features",
        "limitations",
        "uncertainties",
        mode="before",
    )
    @classmethod
    def _listify(cls, value):
        if value is None:
            return []
        return value


class PremiumAiPackageSchema(FreeAiPackageSchema):
    """Premium: free fields + long description + technical + recruiter bullets."""

    detailed_description: str = Field(default="", max_length=2500)
    technical_explanation: str = Field(default="", max_length=2000)
    resume_bullets: List[str] = Field(default_factory=list, max_length=6)

    @field_validator("resume_bullets", mode="before")
    @classmethod
    def _listify_resume(cls, value):
        if value is None:
            return []
        return value


# Backward-compatible alias used by older callers
ProjectDescriptionDraftSchema = PremiumAiPackageSchema


def schema_model_for_plan(*, premium: bool, include_detailed: bool) -> type[BaseModel]:
    if premium and include_detailed:
        return PremiumAiPackageSchema
    return FreeAiPackageSchema


def schema_for_selection(_selection: dict | None = None) -> dict:
    """Legacy JSON-schema helper — prefer schema_model_for_plan + responses.parse."""
    return FreeAiPackageSchema.model_json_schema()
