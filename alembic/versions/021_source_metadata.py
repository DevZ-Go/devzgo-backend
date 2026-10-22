"""where a project's source comes from and the state of its stored copy

Revision ID: 021_source_metadata
Revises: 020_technical_challenges
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "021_source_metadata"
down_revision: Union[str, None] = "020_technical_challenges"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("github_repo_id", sa.BigInteger()),
    ("github_full_name", sa.String(length=200)),
    ("github_default_branch", sa.String(length=255)),
    ("snapshot_origin", sa.String(length=16)),
    ("snapshot_ref", sa.String(length=255)),
    ("snapshot_commit_sha", sa.String(length=40)),
    ("snapshot_synced_at", sa.DateTime(timezone=True)),
    ("sync_state", sa.String(length=16)),
    ("sync_error", sa.String(length=300)),
    ("sync_updated_at", sa.DateTime(timezone=True)),
)


def _columns() -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns("projects")}


def upgrade() -> None:
    existing = _columns()
    if "source_kind" not in existing:
        op.add_column("projects", sa.Column("source_kind", sa.String(length=16), nullable=False, server_default="zip"))
    for name, type_ in COLUMNS:
        if name not in existing:
            op.add_column("projects", sa.Column(name, type_, nullable=True))
    # Only the new columns are written, from values already on each row.
    op.execute("UPDATE projects SET source_kind = 'github' WHERE github_url IS NOT NULL AND source_kind = 'zip'")
    op.execute(
        r"""
        UPDATE projects
        SET github_full_name = regexp_replace(
            (regexp_match(github_url, '^https?://(www\.)?github\.com/([^/?#]+/[^/?#]+)'))[2], '\.git$', '')
        WHERE github_url IS NOT NULL AND github_full_name IS NULL
        """
    )


def downgrade() -> None:
    existing = _columns()
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column("projects", name)
    if "source_kind" in existing:
        op.drop_column("projects", "source_kind")
