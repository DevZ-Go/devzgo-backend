"""Server-side feature flags for Talent Intelligence rollout. Defaults are conservative."""

from __future__ import annotations

import os


def _flag(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def numeric_match_enabled() -> bool:
    """Requirements coverage percentage — off until formal validation."""
    return _flag("FEATURE_NUMERIC_MATCH", False)


def career_enabled() -> bool:
    return _flag("FEATURE_CAREER", True)


def resume_import_enabled() -> bool:
    return _flag("FEATURE_RESUME_IMPORT", True)


def badges_enabled() -> bool:
    return _flag("FEATURE_BADGES", True)


def resume_studio_enabled() -> bool:
    return _flag("FEATURE_RESUME_STUDIO", True)


def project_intelligence_enabled() -> bool:
    """Project Intelligence (evidence analysis + plain insights + optional AI drafts). Off until eval."""
    return _flag("FEATURE_PROJECT_INTELLIGENCE", False)
