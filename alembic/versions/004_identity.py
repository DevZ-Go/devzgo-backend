"""case-insensitive unique usernames and token versions

Revision ID: 004_identity
Revises: 003_baseline
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_identity"
down_revision: Union[str, None] = "003_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    clashes = op.get_bind().execute(sa.text(
        "SELECT lower(username), string_agg(username, ', ') FROM users GROUP BY lower(username) HAVING count(*) > 1"
    )).all()
    if clashes:
        report = "; ".join(f"{low}: {names}" for low, names in clashes)
        raise RuntimeError(f"Usernames that differ only by case must be resolved by hand first: {report}")
    op.create_index("uq_users_username_lower", "users", [sa.text("lower(username)")], unique=True)
    op.add_column("users", sa.Column("token_version", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("users", "token_version")
    op.drop_index("uq_users_username_lower", table_name="users")
