"""size and detected kind of each stored project file

Revision ID: 022_file_metadata
Revises: 021_source_metadata
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "022_file_metadata"
down_revision: Union[str, None] = "021_source_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns("files")}


def upgrade() -> None:
    existing = _columns()
    if "size_bytes" not in existing:
        op.add_column("files", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    if "kind" not in existing:
        op.add_column("files", sa.Column("kind", sa.String(length=16), nullable=True))


def downgrade() -> None:
    existing = _columns()
    if "kind" in existing:
        op.drop_column("files", "kind")
    if "size_bytes" in existing:
        op.drop_column("files", "size_bytes")
