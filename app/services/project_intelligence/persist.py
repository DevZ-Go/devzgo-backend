"""Persist analyzer claims and composed insights."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.project_intelligence import (
    ProjectEvidenceClaim,
    ProjectEvidenceRef,
    ProjectPlainInsight,
)
from app.services.project_intelligence.analyzer import AnalysisBundle, RawClaim
from app.services.project_intelligence.composer import compose_insights
from app.services.project_intelligence.constants import ANALYZER_VERSION, COMPOSER_VERSION


def replace_claims(
    db: Session,
    *,
    project_id: UUID,
    snapshot_id: UUID,
    job_id: UUID,
    revision: str,
    claims: list[RawClaim],
) -> list[ProjectEvidenceClaim]:
    db.query(ProjectEvidenceClaim).filter(
        ProjectEvidenceClaim.project_id == project_id,
        ProjectEvidenceClaim.snapshot_id == snapshot_id,
    ).delete(synchronize_session=False)
    db.flush()

    rows: list[ProjectEvidenceClaim] = []
    for raw in claims:
        row = ProjectEvidenceClaim(
            project_id=project_id,
            snapshot_id=snapshot_id,
            job_id=job_id,
            claim_type=raw.claim_type,
            wording=raw.wording,
            support_class=raw.support_class,
            source_file=raw.source_file,
            source_location=raw.source_location,
            source_revision=revision,
            analyzer_version=ANALYZER_VERSION,
            visibility=raw.visibility,
            attribution_status="unattributed",
            extra=raw.extra or {},
        )
        db.add(row)
        db.flush()
        for ref in raw.refs or []:
            db.add(ProjectEvidenceRef(
                claim_id=row.id,
                source_file=ref.get("source_file") or raw.source_file or "",
                line_start=ref.get("line_start"),
                line_end=ref.get("line_end"),
                snippet_hash=ref.get("snippet_hash"),
                label=ref.get("label"),
            ))
        rows.append(row)
    db.flush()
    return rows


def save_insights(
    db: Session,
    *,
    project_id: UUID,
    snapshot_id: UUID,
    job_id: UUID,
    title: str,
    bundle: AnalysisBundle,
    claim_ids: list[str],
    audience: str = "public",
) -> ProjectPlainInsight:
    composed = compose_insights(
        title=title,
        claims=bundle.claims,
        tech_names=bundle.tech_names,
        truncated=bundle.truncated,
        coverage_pct=bundle.coverage_pct,
    )
    # Keep only latest insight row per project for simplicity
    db.query(ProjectPlainInsight).filter(ProjectPlainInsight.project_id == project_id).delete(
        synchronize_session=False
    )
    row = ProjectPlainInsight(
        project_id=project_id,
        snapshot_id=snapshot_id,
        job_id=job_id,
        audience=audience,
        everyday=composed["everyday"],
        technical=composed["technical"],
        uncertainties=composed["uncertainties"],
        claim_ids=claim_ids,
        composer_version=composed.get("composer_version") or COMPOSER_VERSION,
    )
    db.add(row)
    db.flush()
    return row
