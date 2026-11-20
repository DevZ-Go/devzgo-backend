"""Versions and budgets for Project Intelligence. Keep analyzer version in idempotency keys."""

from __future__ import annotations

import hashlib
import os

ANALYZER_VERSION = "pi_analyzer_v2"
COMPOSER_VERSION = "rule_v2"
POLICY_VERSION = "pi_policy_v1"
PROMPT_VERSION = "pi_prompt_v3"

# Budgets (overridable via env for ops)
MAX_SCAN_FILES = int(os.getenv("PI_MAX_SCAN_FILES", "800"))
MAX_FILE_BYTES = int(os.getenv("PI_MAX_FILE_BYTES", str(256 * 1024)))
MAX_TOTAL_BYTES = int(os.getenv("PI_MAX_TOTAL_BYTES", str(8 * 1024 * 1024)))
MAX_WALL_SECONDS = float(os.getenv("PI_MAX_WALL_SECONDS", "45"))
MAX_CLAIMS = int(os.getenv("PI_MAX_CLAIMS", "200"))
MAX_AI_INPUT_CHARS = int(os.getenv("PI_MAX_AI_INPUT_CHARS", "24000"))
MAX_AI_OUTPUT_TOKENS = int(os.getenv("PI_MAX_AI_OUTPUT_TOKENS", "2500"))
MONTHLY_TOKEN_SOFT = int(os.getenv("PI_MONTHLY_TOKEN_SOFT", "200000"))
MONTHLY_TOKEN_HARD = int(os.getenv("PI_MONTHLY_TOKEN_HARD", "500000"))

CONSENT_PURPOSE_EXTERNAL_AI = "project_intelligence_external_ai"


def policy_hash() -> str:
    raw = f"{POLICY_VERSION}|{MAX_SCAN_FILES}|{MAX_FILE_BYTES}|{MAX_TOTAL_BYTES}|{MAX_CLAIMS}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
