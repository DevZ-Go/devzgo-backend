from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class GlobalPrefOut(BaseModel):
    auto_analyze_default: bool


class GlobalPrefIn(BaseModel):
    auto_analyze_default: bool


class ProjectPrefOut(BaseModel):
    intelligence_enabled: Optional[bool] = None
    external_ai_allowed: bool = False
    insight_audience: str = "public"
    effective_auto_analyze: bool


class ProjectPrefIn(BaseModel):
    intelligence_enabled: Optional[bool] = None
    external_ai_allowed: Optional[bool] = None
    insight_audience: Optional[str] = None


class JobStatusOut(BaseModel):
    job_id: Optional[UUID] = None
    status: Optional[str] = None
    job_kind: Optional[str] = None
    coverage_pct: Optional[float] = None
    progress: dict = Field(default_factory=dict)
    error: Optional[str] = None
    cancel_requested: bool = False
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    snapshot_id: Optional[UUID] = None


class AnalyzeIn(BaseModel):
    force: bool = False


class GenerateOutputsIn(BaseModel):
    short_description: bool = True
    detailed_description: bool = True
    simple_english: bool = True
    technical_details: bool = False
    architecture_walkthrough: bool = False
    recruiter_report: bool = False


class GenerateIn(BaseModel):
    parent_draft_id: Optional[UUID] = None
    grant_consent: bool = False
    outputs: Optional[GenerateOutputsIn] = None
    include_detailed: bool = False  # Premium only
    idempotency_key: Optional[str] = Field(default=None, max_length=200)
    regenerate: bool = False


class ClaimOut(BaseModel):
    id: UUID
    claim_type: str
    wording: str
    support_class: str
    source_file: Optional[str] = None
    source_location: Optional[str] = None
    source_revision: str
    visibility: str
    attribution_status: str
    refs: List[dict] = Field(default_factory=list)


class InsightsOut(BaseModel):
    available: bool
    audience: Optional[str] = None
    everyday: dict = Field(default_factory=dict)
    technical: dict = Field(default_factory=dict)
    uncertainties: List[str] = Field(default_factory=list)
    snapshot_id: Optional[UUID] = None
    composer_version: Optional[str] = None
    redacted: bool = False
    developer: dict = Field(default_factory=dict)
    simple_english: dict = Field(default_factory=dict)
    # Flexible brief for the redesigned Understand UI
    project_brief: dict = Field(default_factory=dict)
    explanation_kind: Optional[str] = None  # ai | deterministic | developer | none
    quality_status: Optional[str] = None
    ai_explanation_valid: bool = False
    schema_version: Optional[str] = None


class DraftOut(BaseModel):
    id: UUID
    status: str
    model: Optional[str] = None
    short_description: Optional[str] = None
    full_overview: Optional[str] = None
    key_features: List[Any] = Field(default_factory=list)
    problem_solved: Optional[str] = None
    potential_audience: Optional[str] = None
    how_it_works_simple: Optional[str] = None
    technical_explanation: Optional[str] = None
    resume_bullets: List[Any] = Field(default_factory=list)
    evidence_refs: List[Any] = Field(default_factory=list)
    parent_draft_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    uncertainties: List[str] = Field(default_factory=list)


class ApproveDraftIn(BaseModel):
    apply_short_description: bool = False
    apply_full_description: bool = False
    publish_simple: bool = True
    show_detailed_on_page: bool = False
    adopt_short: bool = False
    adopt_detailed: bool = False


class PublicationOut(BaseModel):
    ai_short_description: Optional[str] = None
    ai_detailed_description: Optional[str] = None
    simple_explanation: dict = Field(default_factory=dict)
    key_features: List[Any] = Field(default_factory=list)
    technical_explanation: Optional[str] = None
    resume_bullets: List[Any] = Field(default_factory=list)
    publish_simple: bool = False
    show_detailed_on_page: bool = False
    adopted_short: bool = False
    adopted_detailed: bool = False
    has_developer_short_backup: bool = False
    has_developer_full_backup: bool = False


class ConsentIn(BaseModel):
    project_id: UUID


class QuotaOut(BaseModel):
    premium: bool
    plan: str = "free"
    year_month: Optional[str] = None
    used: int
    limit: int
    remaining: Optional[int] = None
    can_analyze: bool = True
    can_generate: bool = True
    project_already_counted: bool = False
    project_has_ai: bool = False
    regenerations_used: int = 0
    regenerations_limit: int = 1
    can_regenerate: bool = False
    concurrent_limit: int = 1
    concurrent_active: int = 0
    ai_generate_enabled: bool = True
    visitor_insights_enabled: bool = True
    auto_analyze_on_upload: bool = True
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    exhausted_message: Optional[str] = None


class VisitorAiStatusOut(BaseModel):
    is_admin: bool = False
    visitor_ai_enabled: bool = True
    used: int = 0
    limit: Optional[int] = 5
    remaining: Optional[int] = 5
    can_request_new: bool = True
    summary_ready: bool = False
    can_generate: bool = False
    is_team: bool = False
