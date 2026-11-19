"""Move the admin owner address to idevzgo.ca@gmail.com

Revision ID: 032_owner_email
Revises: 031_platform_admin
"""
from typing import Sequence, Union

from alembic import op

revision: str = "032_owner_email"
down_revision: Union[str, None] = "031_platform_admin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = "devzgo@gmail.com"
NEW = "idevzgo.ca@gmail.com"


def upgrade() -> None:
    op.execute(f"DELETE FROM platform_admin_invites WHERE email = '{OLD}'")
    op.execute(
        "INSERT INTO platform_admin_invites (email, role) "
        f"VALUES ('{NEW}', 'owner') "
        "ON CONFLICT (email) DO UPDATE SET role = 'owner'"
    )
    op.execute(
        "INSERT INTO platform_admins (user_id, role) "
        "SELECT id, 'owner' FROM users "
        f"WHERE lower(email) = '{NEW}' "
        "ON CONFLICT (user_id) DO UPDATE SET role = 'owner'"
    )
    op.execute(
        "DELETE FROM platform_admins "
        "WHERE role = 'owner' AND user_id IN ("
        f"SELECT id FROM users WHERE lower(email) = '{OLD}')"
    )
    op.execute(
        "DELETE FROM platform_admin_invites AS invite USING users "
        "WHERE lower(users.email) = invite.email AND invite.email = "
        f"'{NEW}'"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM platform_admin_invites WHERE email = '{NEW}'")
    op.execute(
        "INSERT INTO platform_admin_invites (email, role) "
        f"VALUES ('{OLD}', 'owner') "
        "ON CONFLICT (email) DO UPDATE SET role = 'owner'"
    )
    op.execute(
        "INSERT INTO platform_admins (user_id, role) "
        "SELECT id, 'owner' FROM users "
        f"WHERE lower(email) = '{OLD}' "
        "ON CONFLICT (user_id) DO UPDATE SET role = 'owner'"
    )
    op.execute(
        "DELETE FROM platform_admins "
        "WHERE role = 'owner' AND user_id IN ("
        f"SELECT id FROM users WHERE lower(email) = '{NEW}')"
    )
    op.execute(
        "DELETE FROM platform_admin_invites AS invite USING users "
        "WHERE lower(users.email) = invite.email AND invite.email = "
        f"'{OLD}'"
    )
