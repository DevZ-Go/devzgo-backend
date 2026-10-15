"""
Startup database step for DevZ-Go.

1. Apply Alembic migrations automatically on local databases (or when MIGRATE_ON_START=true).
   Shared databases (e.g. Neon) are only reported, so schema changes there are applied deliberately:
     alembic upgrade head
2. Seed the techstacks catalog.

Usage:
  python -m app.scripts.bootstrap_db
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from app.db.session import DATABASE_URL, engine
from app.scripts.seed_techstacks import seed

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
LOCAL_HOSTS = {"db", "localhost", "127.0.0.1"}


def migrate() -> None:
    cfg = Config(str(ALEMBIC_INI))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current == head:
        print(f"[bootstrap] Schema is up to date ({head}).")
    elif make_url(DATABASE_URL).host in LOCAL_HOSTS or os.getenv("MIGRATE_ON_START", "").lower() == "true":
        print(f"[bootstrap] Migrating {current or 'empty'} -> {head}...")
        command.upgrade(cfg, "head")
    else:
        print(f"[bootstrap] WARNING: shared database is at {current or 'no revision'}, code expects {head}. "
              "Review the pending migrations, then run `alembic upgrade head` (or set MIGRATE_ON_START=true).")


def bootstrap() -> None:
    migrate()
    if inspect(engine).has_table("techstacks"):
        print("[bootstrap] Seeding tech stacks...")
        seed()
    print("[bootstrap] Done.")


if __name__ == "__main__":
    bootstrap()
