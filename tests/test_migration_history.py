"""The migration history stays one straight line, so every database has a single schema to reach."""

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))


def test_one_head():
    assert len(SCRIPT.get_heads()) == 1, SCRIPT.get_heads()


def test_one_starting_point():
    # A second root (down_revision = None), e.g. a fresh `alembic revision --autogenerate`, re-creates existing tables.
    assert SCRIPT.get_bases() == ["001_cat_other"], SCRIPT.get_bases()


def test_migration_files_are_numbered():
    # .dockerignore only ships alembic/versions/NNN_*.py, so any other name would never reach the containers.
    names = sorted(p.name for p in (ROOT / "alembic" / "versions").glob("*.py"))
    assert [n for n in names if not re.fullmatch(r"\d{3}_\w+\.py", n)] == []
