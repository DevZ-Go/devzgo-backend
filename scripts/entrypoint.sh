#!/usr/bin/env bash
# Docker entrypoint: wait for Postgres, bootstrap schema + seed, then start the API.
set -euo pipefail

echo "[entrypoint] Waiting for database..."
python - <<'PY'
import os, time, sys
from sqlalchemy import create_engine, text

url = os.environ.get("DATABASE_URL", "postgresql://devzgo:devzgo@db:5432/devzgo_db")
for i in range(60):
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[entrypoint] Database is ready.")
        sys.exit(0)
    except Exception as exc:
        print(f"[entrypoint] DB not ready ({i+1}/60): {exc}")
        time.sleep(1)
print("[entrypoint] Timed out waiting for database.", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] Bootstrapping schema and tech stacks..."
python -m app.scripts.bootstrap_db

echo "[entrypoint] Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
