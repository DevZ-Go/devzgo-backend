#!/usr/bin/env bash
# Run a command against a throwaway local database, never the shared one:
#   scripts/on_test_db.sh devzgo_test pytest -q
#   scripts/on_test_db.sh devzgo_migr_empty_test alembic upgrade head
set -euo pipefail
db="$1"; shift
[[ "$db" == *_test ]] || { echo "Refusing: database name must end in _test" >&2; exit 1; }
cd "$(dirname "$0")/.."
docker compose exec -T db sh -c "psql -U devzgo -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='$db'\" | grep -q 1 || createdb -U devzgo $db"
exec docker compose run --rm --no-deps -T -v "$PWD":/app -w /app \
  -e DATABASE_URL="postgresql://devzgo:devzgo@db:5432/$db" -e TEST_DATABASE_URL="postgresql://devzgo:devzgo@db:5432/$db" \
  -e STORAGE_BACKEND=local -e PYTHONPATH=/app --entrypoint "" backend "$@"
