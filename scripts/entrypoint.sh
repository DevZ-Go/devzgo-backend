#!/usr/bin/env bash
# Docker entrypoint: wait for Postgres, bootstrap schema + seed, then start the API.
set -euo pipefail

# A localhost URL in .env is meant for non-Docker runs; inside the container Postgres is "db".
DATABASE_URL="${DATABASE_URL:-postgresql://devzgo:devzgo@db:5432/devzgo_db}"
DATABASE_URL="${DATABASE_URL/@localhost:/@db:}"
export DATABASE_URL="${DATABASE_URL/@127.0.0.1:/@db:}"

echo "[entrypoint] Waiting for database..."
python - <<'PY'
import os, time, sys
from sqlalchemy import create_engine, text

from app.db.session import DATABASE_URL as url
for i in range(60):
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[entrypoint] Database is ready.")
        sys.exit(0)
    except Exception as exc:
        # Driver message only; str(exc) can echo the URL (and password) on parse errors.
        reason = str(getattr(exc, "orig", None) or type(exc).__name__).strip().splitlines()[0]
        print(f"[entrypoint] DB not ready ({i+1}/60): {reason}")
        time.sleep(1)
print("[entrypoint] Timed out waiting for database.", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] Bootstrapping schema and tech stacks..."
python -m app.scripts.bootstrap_db

echo "[entrypoint] Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
