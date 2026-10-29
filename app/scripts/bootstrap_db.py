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
import sys
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


def database_kind(url: str = DATABASE_URL) -> str:
    """Which database this is, for the log; never includes the URL or credentials."""
    host = make_url(url).host or ""
    if host in LOCAL_HOSTS:
        return "local PostgreSQL (docker compose db container)" if host == "db" else "local PostgreSQL"
    if host.endswith(".neon.tech"):
        return "Neon (shared)"
    return "remote PostgreSQL (shared)"


def single_head(script: ScriptDirectory) -> str:
    """The one migration head; exit with an explanation when the history has branched."""
    heads = script.get_heads()
    if len(heads) == 1:
        return heads[0]
    print(f"[bootstrap] ERROR: the migrations have {len(heads)} heads, so the schema to build is ambiguous:", file=sys.stderr)
    for rev in heads:
        s = script.get_revision(rev)
        print(f"[bootstrap]   {rev}  (after {s.down_revision})  {Path(s.path).name}", file=sys.stderr)
    print("[bootstrap] Usually one of them is a local file missing from git (alembic/versions), or two branches each "
          "added a migration. Review both, then commit a merge revision (alembic merge) or remove the stray local file. "
          "Nothing was changed in the database.", file=sys.stderr)
    sys.exit(1)


def migrate() -> None:
    cfg = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    head = single_head(script)
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current is not None:
        try:
            script.get_revision(current)
        except Exception:
            print(f"[bootstrap] ERROR: the database is at revision {current}, which is not in alembic/versions. "
                  "It was built from a migration that was never committed. Nothing was changed in the database.",
                  file=sys.stderr)
            sys.exit(1)
    if current == head:
        print(f"[bootstrap] Schema is up to date ({head}).")
    elif make_url(DATABASE_URL).host in LOCAL_HOSTS or os.getenv("MIGRATE_ON_START", "").lower() == "true":
        print(f"[bootstrap] Migrating {current or 'empty'} -> {head}...")
        command.upgrade(cfg, "head")
    else:
        print(f"[bootstrap] WARNING: shared database is at {current or 'no revision'}, code expects {head}. "
              "Review the pending migrations, then run `alembic upgrade head` (or set MIGRATE_ON_START=true).")


def bootstrap() -> None:
    print(f"[bootstrap] Database: {database_kind()}")
    migrate()
    if inspect(engine).has_table("techstacks"):
        print("[bootstrap] Seeding tech stacks...")
        seed()
    print("[bootstrap] Done.")


if __name__ == "__main__":
    bootstrap()
