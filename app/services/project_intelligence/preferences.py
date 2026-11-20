"""Global and per-project Project Intelligence preferences."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.project_intelligence import ProjectAnalysisPreference, ProjectIntelligenceSettings

AUDIENCES = frozenset({"private", "connections", "recruiters", "public"})


def get_user_pref(db: Session, user_id: UUID) -> ProjectAnalysisPreference:
    row = db.query(ProjectAnalysisPreference).filter(ProjectAnalysisPreference.user_id == user_id).first()
    if row:
        return row
    row = ProjectAnalysisPreference(user_id=user_id, auto_analyze_default=True)
    db.add(row)
    db.flush()
    return row


def set_user_pref(db: Session, user_id: UUID, *, auto_analyze_default: bool) -> ProjectAnalysisPreference:
    row = get_user_pref(db, user_id)
    row.auto_analyze_default = auto_analyze_default
    db.flush()
    return row


def get_project_settings(db: Session, project_id: UUID) -> ProjectIntelligenceSettings:
    row = db.query(ProjectIntelligenceSettings).filter(ProjectIntelligenceSettings.project_id == project_id).first()
    if row:
        return row
    row = ProjectIntelligenceSettings(project_id=project_id)
    db.add(row)
    db.flush()
    return row


def update_project_settings(db: Session, project_id: UUID, data: dict) -> ProjectIntelligenceSettings:
    row = get_project_settings(db, project_id)
    if "intelligence_enabled" in data:
        row.intelligence_enabled = data["intelligence_enabled"]
    if "external_ai_allowed" in data:
        row.external_ai_allowed = bool(data["external_ai_allowed"])
    if "insight_audience" in data and data["insight_audience"] in AUDIENCES:
        row.insight_audience = data["insight_audience"]
    db.flush()
    return row


def analysis_allowed(db: Session, project_id: UUID, owner_id: UUID) -> bool:
    """Deterministic analysis: project override if set, else user global default."""
    settings = get_project_settings(db, project_id)
    if settings.intelligence_enabled is False:
        return False
    if settings.intelligence_enabled is True:
        return True
    return bool(get_user_pref(db, owner_id).auto_analyze_default)


def external_ai_allowed(db: Session, project_id: UUID) -> bool:
    return bool(get_project_settings(db, project_id).external_ai_allowed)
