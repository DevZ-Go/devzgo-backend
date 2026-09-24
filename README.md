# devzgo-backend
This is used for the backend of DevZ-Go

## Run with Docker

```bash
docker compose up --build
```

The container entrypoint waits for Postgres, runs `python -m app.scripts.bootstrap_db` (create tables, add analysis columns if missing, seed tech stacks), then starts the API on port 8000.

Team features (auth, projects, network, feed, connections, messages, collaboration, profiles, notifications, analytics) stay registered. `Base.metadata.create_all` still runs on startup.

## Workspace and analysis

After a project ZIP upload the API:

- extracts the archive under `storage/project_<id>/` with zip-slip, size, and symlink checks
- indexes files
- stores language percentages and detected technology ids
- does not overwrite confirmed tech stacks when the project already has some

Source files are not publicly mounted. Read them with:

- `GET /projects/{id}/files`
- `GET /projects/{id}/file?path=...`
- `GET /projects/{id}/file/raw?path=...`
- `GET /projects/{id}/analysis`

Covers and videos remain at `/storage/covers` and `/storage/videos`.

## Tests

```bash
PYTHONPATH=. pytest tests/ -q
```
