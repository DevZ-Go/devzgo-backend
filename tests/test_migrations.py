"""Migrations build the exact model schema from empty and from a legacy (pre-Alembic) database."""

import os

import pytest
from sqlalchemy import create_engine, text

TEST_DB = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")

LEGACY_INDEXES = ["ix_activities_created", "ix_messages_receiver_unread", "ix_notifications_recipient_created", "uq_users_github_id"]


@pytest.fixture
def migr_engine():
    """A separate throwaway database (<name>_migr_test) so the API tests keep their own."""
    base, name = TEST_DB.rsplit("/", 1)
    migr_name = name.removesuffix("_test") + "_migr_test"
    admin = create_engine(TEST_DB, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": migr_name}).first():
            conn.execute(text(f'CREATE DATABASE "{migr_name}"'))
    admin.dispose()
    engine = create_engine(f"{base}/{migr_name}")
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    yield engine
    engine.dispose()


def _run(engine, action, target):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        getattr(command, action)(cfg, target)


def _diff(engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.db.base import Base

    with engine.connect() as conn:
        return compare_metadata(MigrationContext.configure(conn), Base.metadata)


def test_empty_database_upgrade_downgrade_upgrade(migr_engine):
    _run(migr_engine, "upgrade", "head")
    assert _diff(migr_engine) == []
    _run(migr_engine, "downgrade", "base")
    _run(migr_engine, "upgrade", "head")
    assert _diff(migr_engine) == []


def test_legacy_create_all_database_upgrades_cleanly(migr_engine):
    """Old databases were built by create_all + bootstrap scripts and had no Alembic history."""
    from app.db.base import Base

    Base.metadata.create_all(migr_engine)
    # Remove what later migrations add, so the schema matches a database from before Alembic.
    _run(migr_engine, "stamp", "head")
    _run(migr_engine, "downgrade", "003_baseline")
    with migr_engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        for name in LEGACY_INDEXES:
            conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        conn.execute(text("ALTER TABLE users ADD CONSTRAINT users_github_id_key UNIQUE (github_id)"))
        conn.execute(text("ALTER TABLE projects DROP COLUMN language_stats"))
        conn.execute(text("INSERT INTO users (id, username, email) VALUES (gen_random_uuid(), 'keep', 'keep@example.com')"))
    _run(migr_engine, "upgrade", "head")
    assert _diff(migr_engine) == []
    with migr_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1  # data survives


def test_existing_recruiters_become_recruiter_accounts(migr_engine):
    _run(migr_engine, "upgrade", "016_chat_room_photo")
    with migr_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, username, email) VALUES "
                          "('00000000-0000-0000-0000-00000000000a', 'hirer', 'hirer@example.com'), "
                          "('00000000-0000-0000-0000-00000000000b', 'maker', 'maker@example.com')"))
        conn.execute(text("INSERT INTO profiles (id, user_id, role, company) VALUES "
                          "(gen_random_uuid(), '00000000-0000-0000-0000-00000000000a', 'Recruiter', 'North'), "
                          "(gen_random_uuid(), '00000000-0000-0000-0000-00000000000b', 'Developer', NULL)"))
    _run(migr_engine, "upgrade", "head")
    with migr_engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT username, account_type FROM users")).all())
        assert rows == {"hirer": "recruiter", "maker": "developer"}
        assert conn.execute(text("SELECT count(*) FROM profiles WHERE open_to_work")).scalar() == 0


def test_identity_migration_stops_on_case_duplicate_usernames(migr_engine):
    _run(migr_engine, "upgrade", "003_baseline")
    with migr_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, username, email) VALUES "
                          "(gen_random_uuid(), 'Sam', 'a@example.com'), (gen_random_uuid(), 'sam', 'b@example.com')"))
    with pytest.raises(RuntimeError, match="sam: "):
        _run(migr_engine, "upgrade", "head")
    with migr_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 2
