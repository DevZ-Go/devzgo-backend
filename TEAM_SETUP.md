# Team setup: run DevZ-Go on your own computer

Everyone runs the API and the website on their own machine, and everyone connects to the same cloud services:

| Service | What it holds |
|---------|---------------|
| Neon (Postgres) | Every account, project, message and challenge |
| Cloudflare R2 | Avatars, covers, videos, chat attachments and uploaded code |
| GitHub OAuth app and GitHub App | "Continue with GitHub" and the code workspace |
| Resend | Password reset emails |
| Upstash Redis | Passes live events between everyone's APIs, so messages and notifications arrive instantly |

Because the data is shared, you sign in with the same account on any machine, and anything you create is real and visible to the whole team.

Each API only pushes live updates to the people connected to it. The shared Redis (`REDIS_URL` in `.env`) relays them to everyone else's API. Without it, chatting still works, but a message from a teammate only shows up after you refresh or reopen the conversation.

## 1. Install once

- [Git](https://git-scm.com/downloads)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/), opened and showing "Engine running"
- [Node.js](https://nodejs.org/) 20 or newer (`node -v` to check)

On Windows, run the commands below in PowerShell or Git Bash.

## 2. Get the code

Both repositories must be on the same branch.

```bash
mkdir DevZGo
cd DevZGo
git clone https://github.com/DevZ-Go/devzgo-backend.git
git clone https://github.com/DevZ-Go/devzgo-frontend.git
git -C devzgo-backend checkout feature/code-workspace
git -C devzgo-frontend checkout feature/code-workspace
```

Once that branch is merged, use `main` instead.

## 3. Add the shared settings

Ask the project owner for the team settings file (`devzgo-team.env`). It is sent privately, never through GitHub or a group chat.

Save it as `.env` inside `devzgo-backend` (the name starts with a dot and has no other extension):

```bash
# macOS / Linux / Git Bash
cp ~/Downloads/devzgo-team.env devzgo-backend/.env
```

```powershell
# Windows PowerShell
Copy-Item $HOME\Downloads\devzgo-team.env devzgo-backend\.env
```

`.env` is git-ignored. Never commit it, paste it anywhere, or share a screenshot of it.

## 4. Start the API

```bash
cd devzgo-backend
docker compose up -d --build
docker compose logs -f backend
```

The first build takes a few minutes. Each start then takes about 40 seconds while the API checks the database. Wait for these lines, then press Ctrl+C to stop watching the log (the API keeps running):

```
[bootstrap] Schema is up to date (...).
INFO:     Application startup complete.
```

Open http://localhost:8000/docs. If the API page loads, the backend is connected to the cloud.

## 5. Start the website

In a second terminal:

```bash
cd devzgo-frontend
cp .env.example .env.local        # Windows PowerShell: Copy-Item .env.example .env.local
npm install
npm run dev
```

Open http://localhost:5173 and sign in with your usual DevZ-Go account.

## Every day

```bash
git -C devzgo-backend pull
git -C devzgo-frontend pull

cd devzgo-backend
docker compose up -d --build      # rebuilding picks up backend changes
cd ../devzgo-frontend
npm install
npm run dev
```

Stop the API with `docker compose down` in `devzgo-backend`. This only stops your local containers; the cloud data stays.

## Rules for the shared cloud

- Do not run `alembic upgrade head`, set `MIGRATE_ON_START=true`, or set `ALLOW_DEMO_SEED=true` with the team `.env`. Database changes are reviewed and applied once, by one person.
- Do not create migrations with `alembic revision --autogenerate` unless the team agreed on a schema change; never generate an "initial schema" one. `alembic/versions` should only contain committed files (`git status alembic` shows nothing).
- Keep every value in `.env` exactly as received. `GITHUB_TOKEN_ENCRYPTION_KEY` in particular must match on every machine, or saved GitHub connections stop working for the others.
- Run tests only against the local test database (`scripts/on_test_db.sh devzgo_test pytest -q`), never against Neon.
- Use test accounts for experiments, and do not delete other people's accounts, projects or messages.

## If something goes wrong

| What you see | What to do |
|--------------|------------|
| `Cannot connect to the Docker daemon` | Open Docker Desktop and wait until it says the engine is running. |
| `port is already allocated` (5432, 8000, 9000 or 5173) | Another program uses that port, often a locally installed Postgres. Stop it, then run `docker compose up -d` again. |
| The page shows connection errors right after starting | The API is still starting (about 40 seconds). Wait, then hard-reload with Cmd+Shift+R (Mac) or Ctrl+Shift+R (Windows). |
| Log says `WARNING: shared database is at X, code expects Y` | Your branch is behind or ahead of the database. Pull the latest code; do not run migrations yourself. |
| Log says `the migrations have 2 heads` | A migration file that is not in git sits in `alembic/versions` (`git status alembic`). Move it out of that folder, then `docker compose up -d --build`. Do not run `alembic merge` or `alembic stamp`. |
| Log says `the database is at revision X, which is not in alembic/versions` | Your local database was built from an uncommitted migration. Ask before changing anything; never run this against the shared database. |
| Images or videos do not load | Check that `.env` came through complete (it contains `STORAGE_BACKEND=s3` and the `S3_` settings), then `docker compose up -d --build`. |
| GitHub sign-in fails after redirecting | Use exactly http://localhost:5173 and http://localhost:8000; GitHub only accepts those addresses for this app. |
| Teammates' messages only appear after a refresh | `REDIS_URL` is missing or different from everyone else's. Copy it from the team file, then `docker compose up -d`. The log should say `Realtime: using Redis pub/sub`. |
| The first click after a break is slow | Neon sleeps after about 5 minutes without use and takes a couple of seconds to wake. |
| No password reset email | Until a domain is verified in Resend, reset emails only reach the Resend account owner. |
| Website changes do not show up | Stop `npm run dev` with Ctrl+C, start it again, and hard-reload the page. |
