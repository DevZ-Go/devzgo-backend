#!/usr/bin/env bash
# Docker / Render entrypoint: wait for Postgres, bootstrap schema, then start uvicorn.
set -euo pipefail

# A localhost URL in .env is meant for non-Docker runs; inside compose Postgres is "db".
# Neon / Render URLs are left unchanged.
DATABASE_URL="${DATABASE_URL:-postgresql://devzgo:devzgo@db:5432/devzgo_db}"
DATABASE_URL="${DATABASE_URL/@localhost:/@db:}"
export DATABASE_URL="${DATABASE_URL/@127.0.0.1:/@db:}"

# Render injects PORT; local compose defaults to 8000.
PORT="${PORT:-8000}"
# Realtime uses an in-process broadcaster unless REDIS_URL is set — keep a single worker
# (and a single Render instance) so WebSocket fans-out stay correct.
if [ -z "${REDIS_URL:-}" ]; then
  WORKERS=1
else
  WORKERS="${UVICORN_WORKERS:-${WEB_CONCURRENCY:-1}}"
fi

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

echo "[entrypoint] Checking the schema (migrates local databases only) and optional seed..."
python -m app.scripts.bootstrap_db

echo "[entrypoint] Starting uvicorn on 0.0.0.0:${PORT} (workers=${WORKERS}, no reload)..."
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WORKERS}" \
  --proxy-headers \
  --forwarded-allow-ips='*'
