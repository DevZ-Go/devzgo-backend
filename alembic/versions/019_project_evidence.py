"""project demo link, reported outcomes and recorded source facts

Revision ID: 019_project_evidence
Revises: 018_recruiting
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "019_project_evidence"
down_revision: Union[str, None] = "018_recruiting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("live_url", sa.String(length=500)),
    ("impact_note", sa.Text()),
    ("source_facts", postgresql.JSONB()),
    ("source_analyzed_at", sa.DateTime(timezone=True)),
    ("source_facts_version", sa.Integer()),
)


def _columns() -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns("projects")}


def upgrade() -> None:
    existing = _columns()
    for name, type_ in COLUMNS:
        if name not in existing:
            op.add_column("projects", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    existing = _columns()
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column("projects", name)
