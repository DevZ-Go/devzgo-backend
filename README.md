# DevZ-Go Backend

FastAPI + PostgreSQL API for the DevZ-Go Capstone project.

## Requirements

- Python 3.11+
- Docker Desktop (recommended on macOS) **or** a local PostgreSQL 15 instance

## Quick start (Docker — recommended)

```bash
cd devzgo-backend
docker compose up --build
```

On startup the container:

1. Waits for Postgres
2. Runs `python -m app.scripts.bootstrap_db` (create tables, ensure analysis columns, seed tech stacks)
3. Starts uvicorn on port **8000**

- API: http://127.0.0.1:8000
- Docs: http://127.0.0.1:8000/docs
- Postgres: `localhost:5432` (`devzgo` / `devzgo` / `devzgo_db`)

Manual bootstrap (host or `docker compose exec backend`):

```bash
python -m app.scripts.bootstrap_db
```

## Local run (API outside Docker)

```bash
cp .env.example .env
pip install -r requirements.txt
# Postgres must match DATABASE_URL
python -m app.scripts.bootstrap_db
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Always start uvicorn from the **repo root** so `storage/` resolves correctly.

## Project analysis

After ZIP upload the backend runs deterministic analysis (`app/services/project_analysis.py`):

- **Languages** — byte-weighted percentages (skips `node_modules`, `.git`, `dist`, …)
- **Tech detection** — extensions + `package.json` / `requirements.txt` / `pyproject.toml` / Java build files / Dockerfile / Flutter markers

Persisted on the project:

| Field | Meaning |
|-------|---------|
| `language_stats` | `[{name, percentage}, …]` |
| `detected_tech_stack_ids` | Catalog ids from last analysis |
| `tech_stacks` (M2M) | **Confirmed** stacks (user / confirm modal) |

Re-upload **does not** overwrite confirmed tech stacks when some already exist; it only refreshes detected + languages. First upload with empty confirmed stacks auto-applies detection.

Endpoints:

- `POST /projects/{id}/workspace/upload` — returns `languages`, `detected_tech_stacks`, `detected_tech_stack_ids`
- `GET /projects/{id}` — includes `languages`, `detected_*`, confirmed `tech_stacks`
- `GET /projects/{id}/analysis` — analysis-focused payload

## Workspace upload limits

| Limit | Value |
|-------|-------|
| Max ZIP upload | 50 MiB |
| Max extracted size | 200 MiB |
| Max file count | 5,000 |
| Symlinks in ZIP | Rejected |

Only `/storage/covers` and `/storage/videos` are public static mounts.

## Tests

```bash
pip install -r requirements.txt
PYTHONPATH=. pytest tests/ -q
```
