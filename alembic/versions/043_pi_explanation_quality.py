"""Add PI explanation quality / schema version on publications.

Revision ID: 043_pi_explanation_quality
Revises: 042_comments_clubs
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "043_pi_explanation_quality"
down_revision: Union[str, None] = "042_comments_clubs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    if table not in inspect(bind).get_table_names():
        return False
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "project_ai_publications", "quality_status"):
        op.add_column(
            "project_ai_publications",
            sa.Column("quality_status", sa.String(32), nullable=False, server_default="unknown"),
        )
    if not _has_column(bind, "project_ai_publications", "schema_version"):
        op.add_column(
            "project_ai_publications",
            sa.Column("schema_version", sa.String(40), nullable=False, server_default="legacy"),
        )
    if not _has_column(bind, "project_ai_publications", "prompt_version"):
        op.add_column(
            "project_ai_publications",
            sa.Column("prompt_version", sa.String(40), nullable=True),
        )
    if not _has_column(bind, "project_ai_publications", "explanation_kind"):
        op.add_column(
            "project_ai_publications",
            sa.Column("explanation_kind", sa.String(32), nullable=False, server_default="ai"),
        )
    if not _has_column(bind, "project_ai_publications", "quality_reasons"):
        from sqlalchemy.dialects import postgresql

        op.add_column(
            "project_ai_publications",
            sa.Column("quality_reasons", postgresql.JSONB(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for col in ("quality_reasons", "explanation_kind", "prompt_version", "schema_version", "quality_status"):
        if _has_column(bind, "project_ai_publications", col):
            op.drop_column("project_ai_publications", col)
