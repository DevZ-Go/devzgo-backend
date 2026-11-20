"""Talent Intelligence: career, imports, evidence, badges, entitlements, evaluations.

Revision ID: 036_talent_intelligence
Revises: 035_email_system
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "036_talent_intelligence"
down_revision: Union[str, None] = "035_email_system"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return name in inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "resume_import_jobs"):
        op.create_table(
            "resume_import_jobs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("storage_key", sa.String(500), nullable=False),
            sa.Column("original_filename", sa.String(255), nullable=False),
            sa.Column("format", sa.String(20), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("analysis", postgresql.JSONB(), nullable=True),
            sa.Column("lease_owner", sa.String(80), nullable=True),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_resume_import_jobs_user", "resume_import_jobs", ["user_id"])
        op.create_index("ix_resume_import_jobs_claim", "resume_import_jobs", ["status", "lease_until"])

    if not _has_table(bind, "resume_extracted_items"):
        op.create_table(
            "resume_extracted_items",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resume_import_jobs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("item_type", sa.String(40), nullable=False),
            sa.Column("field_path", sa.String(120), nullable=False),
            sa.Column("extracted_value", postgresql.JSONB(), nullable=False),
            sa.Column("normalized_value", postgresql.JSONB(), nullable=True),
            sa.Column("source_location", sa.String(200), nullable=True),
            sa.Column("confidence", sa.String(20), nullable=False, server_default="medium"),
            sa.Column("review_state", sa.String(20), nullable=False, server_default="pending_review"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_resume_extracted_items_job", "resume_extracted_items", ["job_id"])

    if not _has_table(bind, "career_experiences"):
        op.create_table(
            "career_experiences",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("organization", sa.String(200), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("category", sa.String(40), nullable=False),
            sa.Column("start_year", sa.Integer(), nullable=True),
            sa.Column("start_month", sa.Integer(), nullable=True),
            sa.Column("end_year", sa.Integer(), nullable=True),
            sa.Column("end_month", sa.Integer(), nullable=True),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("location", sa.String(160), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("responsibilities", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("achievements", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
            sa.Column("provenance", postgresql.JSONB(), nullable=True),
            sa.Column("review_state", sa.String(20), nullable=False, server_default="confirmed"),
            sa.Column("visibility", sa.String(20), nullable=False, server_default="connections"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("import_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resume_import_jobs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_career_experiences_user", "career_experiences", ["user_id"])
        op.create_index("ix_career_experiences_user_review", "career_experiences", ["user_id", "review_state"])

    if not _has_table(bind, "career_skill_intervals"):
        op.create_table(
            "career_skill_intervals",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("experience_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("career_experiences.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tech_stack_id", sa.Integer(), sa.ForeignKey("techstacks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("start_year", sa.Integer(), nullable=True),
            sa.Column("start_month", sa.Integer(), nullable=True),
            sa.Column("end_year", sa.Integer(), nullable=True),
            sa.Column("end_month", sa.Integer(), nullable=True),
            sa.Column("review_state", sa.String(20), nullable=False, server_default="confirmed"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.UniqueConstraint("experience_id", "tech_stack_id", name="uq_career_skill_interval"),
        )
        op.create_index("ix_career_skill_intervals_tech", "career_skill_intervals", ["tech_stack_id"])

    if not _has_table(bind, "career_experience_projects"):
        op.create_table(
            "career_experience_projects",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("experience_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("career_experiences.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("note", sa.String(280), nullable=True),
            sa.UniqueConstraint("experience_id", "project_id", name="uq_career_experience_project"),
        )

    if not _has_table(bind, "career_education"):
        op.create_table(
            "career_education",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("institution", sa.String(200), nullable=False),
            sa.Column("degree", sa.String(200), nullable=True),
            sa.Column("field_of_study", sa.String(200), nullable=True),
            sa.Column("start_year", sa.Integer(), nullable=True),
            sa.Column("start_month", sa.Integer(), nullable=True),
            sa.Column("end_year", sa.Integer(), nullable=True),
            sa.Column("end_month", sa.Integer(), nullable=True),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
            sa.Column("review_state", sa.String(20), nullable=False, server_default="confirmed"),
            sa.Column("visibility", sa.String(20), nullable=False, server_default="connections"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_career_education_user", "career_education", ["user_id"])

    if not _has_table(bind, "career_certifications"):
        op.create_table(
            "career_certifications",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("issuer", sa.String(200), nullable=True),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("month", sa.Integer(), nullable=True),
            sa.Column("credential_url", sa.String(500), nullable=True),
            sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
            sa.Column("review_state", sa.String(20), nullable=False, server_default="confirmed"),
            sa.Column("visibility", sa.String(20), nullable=False, server_default="connections"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_career_certifications_user", "career_certifications", ["user_id"])

    if not _has_table(bind, "career_field_visibility"):
        op.create_table(
            "career_field_visibility",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("resource_type", sa.String(40), nullable=False),
            sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("field_name", sa.String(60), nullable=False),
            sa.Column("audience", sa.String(20), nullable=False, server_default="private"),
            sa.UniqueConstraint("user_id", "resource_type", "resource_id", "field_name", name="uq_career_field_visibility"),
        )
        op.create_index("ix_career_field_visibility_resource", "career_field_visibility", ["resource_type", "resource_id"])

    if not _has_table(bind, "badge_definitions"):
        op.create_table(
            "badge_definitions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("stable_key", sa.String(80), nullable=False, unique=True),
            sa.Column("family", sa.String(40), nullable=False),
            sa.Column("track", sa.String(40), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("icon_key", sa.String(80), nullable=False, server_default="medallion"),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )

    if not _has_table(bind, "badge_criteria_versions"):
        op.create_table(
            "badge_criteria_versions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("definition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("badge_definitions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("published_criteria", sa.Text(), nullable=False),
            sa.Column("rules", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.UniqueConstraint("definition_id", "version", name="uq_badge_criteria_version"),
        )

    if not _has_table(bind, "earned_badges"):
        op.create_table(
            "earned_badges",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("definition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("badge_definitions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("criteria_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("badge_criteria_versions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("awarded_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoke_reason", sa.String(280), nullable=True),
            sa.Column("evidence_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("visibility", sa.String(20), nullable=False, server_default="connections"),
            sa.Column("provenance_label", sa.String(80), nullable=True),
            sa.UniqueConstraint("user_id", "definition_id", "criteria_version_id", name="uq_earned_badge"),
        )
        op.create_index("ix_earned_badges_user", "earned_badges", ["user_id"])

    if not _has_table(bind, "skill_evidence_records"):
        op.create_table(
            "skill_evidence_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tech_stack_id", sa.Integer(), sa.ForeignKey("techstacks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("evidence_class", sa.String(40), nullable=False),
            sa.Column("source_type", sa.String(40), nullable=False),
            sa.Column("source_id", sa.String(80), nullable=True),
            sa.Column("source_ref", postgresql.JSONB(), nullable=True),
            sa.Column("review_state", sa.String(20), nullable=False, server_default="confirmed"),
            sa.Column("audience", sa.String(20), nullable=False, server_default="connections"),
            sa.Column("invalidated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("invalidation_reason", sa.String(280), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_skill_evidence_user_tech", "skill_evidence_records", ["user_id", "tech_stack_id"])
        op.create_index("ix_skill_evidence_class", "skill_evidence_records", ["evidence_class"])

    if not _has_table(bind, "assessment_achievement_records"):
        op.create_table(
            "assessment_achievement_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("submission_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("challenge_submissions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("challenge_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("technical_challenges.id", ondelete="CASCADE"), nullable=False),
            sa.Column("track", sa.String(40), nullable=False),
            sa.Column("rating_summary", postgresql.JSONB(), nullable=True),
            sa.Column("eligible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("ineligible_reason", sa.String(280), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.UniqueConstraint("submission_id", "track", name="uq_assessment_achievement"),
        )
        op.create_index("ix_assessment_achievements_user", "assessment_achievement_records", ["user_id"])

    if not _has_table(bind, "entitlements"):
        op.create_table(
            "entitlements",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("feature", sa.String(40), nullable=False),
            sa.Column("source", sa.String(40), nullable=False, server_default="dev_grant"),
            sa.Column("granted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.UniqueConstraint("user_id", "feature", name="uq_entitlement_user_feature"),
        )
        op.create_index("ix_entitlements_feature", "entitlements", ["feature"])

    if not _has_table(bind, "generated_resumes"):
        op.create_table(
            "generated_resumes",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(160), nullable=False),
            sa.Column("template_key", sa.String(40), nullable=False, server_default="classic_ats"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_generated_resumes_user", "generated_resumes", ["user_id"])

    if not _has_table(bind, "generated_resume_versions"):
        op.create_table(
            "generated_resume_versions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("resume_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("generated_resumes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("content", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("job_description_excerpt", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.UniqueConstraint("resume_id", "version", name="uq_generated_resume_version"),
        )

    if not _has_table(bind, "resume_exports"):
        op.create_table(
            "resume_exports",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("resume_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("generated_resume_versions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("format", sa.String(10), nullable=False),
            sa.Column("storage_key", sa.String(500), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_resume_exports_user", "resume_exports", ["user_id"])

    if not _has_table(bind, "consents"):
        op.create_table(
            "consents",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("purpose", sa.String(80), nullable=False),
            sa.Column("scope", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_consents_user_purpose", "consents", ["user_id", "purpose"])

    if not _has_table(bind, "candidate_evaluations"):
        op.create_table(
            "candidate_evaluations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("recruiter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("purpose", sa.String(40), nullable=False),
            sa.Column("acknowledgement", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("role_title", sa.String(160), nullable=True),
            sa.Column("requirements", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("authorized_evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("result_summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("matching_engine_version", sa.String(40), nullable=False, server_default="jd_match_v1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_candidate_evaluations_recruiter", "candidate_evaluations", ["recruiter_id", "created_at"])
        op.create_index("ix_candidate_evaluations_candidate", "candidate_evaluations", ["candidate_id"])

    if not _has_table(bind, "career_audit_logs"):
        op.create_table(
            "career_audit_logs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("subject_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("action", sa.String(80), nullable=False),
            sa.Column("resource_type", sa.String(40), nullable=False),
            sa.Column("resource_id", sa.String(80), nullable=True),
            sa.Column("detail", postgresql.JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_career_audit_subject", "career_audit_logs", ["subject_user_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "career_audit_logs", "candidate_evaluations", "consents", "resume_exports",
        "generated_resume_versions", "generated_resumes", "entitlements",
        "assessment_achievement_records", "skill_evidence_records", "earned_badges",
        "badge_criteria_versions", "badge_definitions", "career_field_visibility",
        "career_certifications", "career_education", "career_experience_projects",
        "career_skill_intervals", "career_experiences", "resume_extracted_items", "resume_import_jobs",
    ):
        if _has_table(bind, table):
            op.drop_table(table)
