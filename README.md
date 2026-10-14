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

## File storage

`STORAGE_BACKEND=local` (default) keeps files in `storage/`. `STORAGE_BACKEND=s3` uses any S3-compatible bucket: the RustFS container in docker compose, Cloudflare R2, Backblaze B2 or AWS S3. Keys mirror the folder layout (`covers/`, `videos/`, `project_<id>/`, plus `sources/<id>.zip` for the original upload). Media URLs stay `/storage/...` and redirect to short-lived signed links.

```bash
STORAGE_BACKEND=s3 docker compose up -d --build                      # use the local bucket
docker compose exec backend python -m app.scripts.migrate_storage_to_s3   # copy existing storage/ files in
```

Bucket console: http://localhost:9001 (devzgo / devzgo-secret). For a cloud bucket, set `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` in `.env` (see `.env.example`).

## Tests

```bash
PYTHONPATH=. pytest tests/ -q
# also against the compose bucket:
TEST_S3_ENDPOINT_URL=http://localhost:9000 S3_ACCESS_KEY_ID=devzgo S3_SECRET_ACCESS_KEY=devzgo-secret S3_REGION=us-east-1 PYTHONPATH=. pytest tests/ -q
```
