"""
One-command DB bootstrap for DevZ-Go.

1. Create missing tables from SQLAlchemy models (greenfield-safe).
2. Add any missing analysis columns (existing DBs).
3. Stamp Alembic to head when possible (incomplete history).
4. Seed the techstacks catalog.

Usage:
  python -m app.scripts.bootstrap_db
"""

from __future__ import annotations

import subprocess
import sys

from sqlalchemy import inspect, text

from app.db.base import Base
from app.db.session import engine
from app.scripts.seed_techstacks import seed


def _ensure_analysis_columns() -> None:
    """Add language_stats / detected_tech_stack_ids if missing (idempotent)."""
    insp = inspect(engine)
    if "projects" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("projects")}
    statements: list[str] = []
    if "language_stats" not in cols:
        statements.append(
            "ALTER TABLE projects ADD COLUMN language_stats JSONB NULL"
        )
    if "detected_tech_stack_ids" not in cols:
        statements.append(
            "ALTER TABLE projects ADD COLUMN detected_tech_stack_ids JSONB NULL"
        )
    if "category_other" not in cols:
        statements.append(
            "ALTER TABLE projects ADD COLUMN category_other VARCHAR(64) NULL"
        )
    if not statements:
        return
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
            print(f"[bootstrap] Applied: {stmt}")


def _stamp_alembic_head() -> None:
    """Mark Alembic at head without failing on incomplete history."""
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "stamp", "head"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        print(f"[bootstrap] Alembic stamp skipped: {exc}")


def bootstrap() -> None:
    print("[bootstrap] Creating tables from models (if needed)...")
    Base.metadata.create_all(bind=engine)
    print("[bootstrap] Ensuring analysis columns...")
    _ensure_analysis_columns()
    print("[bootstrap] Stamping Alembic head (best-effort)...")
    _stamp_alembic_head()
    print("[bootstrap] Seeding tech stacks...")
    seed()
    print("[bootstrap] Done.")


if __name__ == "__main__":
    bootstrap()
