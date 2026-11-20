"""
Main application file for the DevZGo backend. This sets up the FastAPI app, includes route modules,
and ensures database tables are created on startup.
The root endpoint provides a simple health check to confirm the backend is running.

Storage note: only covers and videos are publicly mounted. Project workspaces stay
behind permissioned /projects/{id}/file routes.
"""

import asyncio
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# Importing CORSMiddleware to handle Cross-Origin Resource Sharing, allowing the frontend to communicate with the backend
from fastapi.middleware.cors import CORSMiddleware

from app.core.errors import ApiError, api_error_handler
from app.db import base as _models  # noqa: F401  registers every model before the routes use them
from app.routes.auth import router as auth_router
from app.routes.projects import router as projects_router
from app.routes.project_comments import router as project_comments_router
from app.routes.network import router as network_router
from app.routes.connections import router as connections_router
from app.routes.messages import router as messages_router
from app.routes.safety import router as safety_router
from app.routes.collaborations import router as collaborations_router
from app.routes.notifications import router as notifications_router
from app.routes.profiles import router as profiles_router
from app.routes.analytics import router as analytics_router
from app.routes.media import router as media_router
from app.routes.realtime import router as realtime_router
from app.routes.github_auth import router as github_auth_router
from app.routes.github_import import router as github_import_router
from app.routes.github_history import router as github_history_router
from app.routes.wraps import router as wraps_router
from app.routes.recruiting import router as recruiting_router
from app.routes.activity_log import router as activity_log_router
from app.routes.evidence import router as evidence_router
from app.routes.matching import router as matching_router
from app.routes.challenges import router as challenges_router
from app.routes.file_preview import router as file_preview_router
from app.routes.github_connection import callback_router as github_app_callback_router
from app.routes.github_connection import router as github_connection_router
from app.routes.workspace import router as workspace_router
from app.routes.admin import router as admin_router
from app.routes.usage import router as usage_router
from app.routes.email_hooks import router as email_hooks_router
from app.routes.career import router as career_router
from app.routes.career_imports import router as career_imports_router
from app.routes.badges import router as badges_router
from app.routes.resume_studio import router as resume_studio_router
from app.routes.evaluations import router as evaluations_router
from app.routes.project_intelligence import router as project_intelligence_router
from app.routes.clubs import router as clubs_router
from app.realtime.broadcaster import broadcaster
from app.services import storage

app = FastAPI(
    title="DevZGo API",
    description=(
        "**Authentication in Swagger:** click **Authorize** (lock), choose **HTTPBearer**, and paste "
        "only the JWT value returned as `access_token` from **POST /auth/login** (not the word Bearer). "
        "Then try protected routes such as **PUT** and **DELETE** on `/projects/{project_id}`.\n\n"
        "Under `/projects/{project_id}` you should see **get**, **put**, and **delete** — expand that path if the UI is collapsed."
    ),
    version="1.0.0",
)

# CORS must run early so browser preflight (OPTIONS) on JSON POSTs — e.g. /auth/register —
# gets Access-Control-* headers. Login uses form-urlencoded and often skips preflight, which
# is why register can fail while login appears to work.
def _cors_origins() -> list[str]:
    defaults = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
        "https://www.devzgo.com",
        "https://devzgo.com",
        "https://devzgo-frontend.pages.dev",
    ]
    extra: list[str] = []
    frontend = (os.getenv("FRONTEND_URL") or "").rstrip("/")
    if frontend:
        extra.append(frontend)
    # Comma-separated production origins, e.g. https://app.example.com,https://www.example.com
    for part in (os.getenv("CORS_ORIGINS") or "").split(","):
        origin = part.strip().rstrip("/")
        if origin:
            extra.append(origin)
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    out: list[str] = []
    for origin in defaults + extra:
        if origin not in seen:
            seen.add(origin)
            out.append(origin)
    return out


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    # Local Vite ports, plus *.pages.dev preview URLs when CORS_ORIGIN_REGEX is unset.
    allow_origin_regex=os.getenv(
        "CORS_ORIGIN_REGEX",
        r"https?://(localhost|127\.0\.0\.1)(:\d+)?$|https://.*\.pages\.dev$",
    ),
    allow_credentials=True,
    # Explicit methods so DELETE is never omitted by proxies or middleware
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
)

# Public media only: covers, videos, profile photos, club art. Do not expose storage/project_<uuid>/.
_PUBLIC_MEDIA_KINDS = ("covers", "videos", "avatars", "resumes", "club-banners", "club-avatars")
if storage.is_s3():
    @app.get("/storage/{kind}/{name}", include_in_schema=False)
    def media_redirect(
        kind: Literal["covers", "videos", "avatars", "resumes", "club-banners", "club-avatars"],
        name: str,
    ):
        return RedirectResponse(storage.url(f"{kind}/{name}"))
else:
    for kind in _PUBLIC_MEDIA_KINDS:
        (storage.ROOT / kind).mkdir(parents=True, exist_ok=True)
        app.mount(f"/storage/{kind}", StaticFiles(directory=str(storage.ROOT / kind)), name=f"storage_{kind}")
app.include_router(media_router)

# The schema comes only from Alembic migrations (app/scripts/bootstrap_db.py runs them at container start).

app.add_exception_handler(ApiError, api_error_handler)
app.include_router(auth_router)
app.include_router(github_auth_router)
app.include_router(github_import_router)
app.include_router(github_history_router)
app.include_router(projects_router)
app.include_router(project_comments_router)
app.include_router(network_router)
app.include_router(connections_router)
app.include_router(messages_router)
app.include_router(safety_router)
app.include_router(collaborations_router)
app.include_router(notifications_router)
app.include_router(profiles_router)
app.include_router(analytics_router)
app.include_router(realtime_router)
app.include_router(wraps_router)
app.include_router(recruiting_router)
app.include_router(activity_log_router)
app.include_router(evidence_router)
app.include_router(matching_router)
app.include_router(challenges_router)
app.include_router(file_preview_router)
app.include_router(github_connection_router)
app.include_router(github_app_callback_router)
app.include_router(workspace_router)
app.include_router(admin_router)
app.include_router(usage_router)
app.include_router(email_hooks_router)
app.include_router(career_router)
app.include_router(career_imports_router)
app.include_router(badges_router)
app.include_router(resume_studio_router)
app.include_router(evaluations_router)
app.include_router(project_intelligence_router)
app.include_router(clubs_router)


def _sweep_workspace() -> None:
    """Drop expired drafts and fail sync jobs that were left running. A missing table must not stop the API."""
    from app.db.session import SessionLocal
    from app.services import workspace_drafts, workspace_sync

    with SessionLocal() as db:
        workspace_drafts.drop_expired(db)
        workspace_sync.recover(db)


@app.on_event("startup")
async def start_realtime():
    await broadcaster.start()
    try:
        await asyncio.to_thread(_sweep_workspace)
    except Exception:
        pass
    if os.getenv("WRAP_JOB", "on").lower() != "off":
        asyncio.create_task(_store_finished_wraps())


async def _store_finished_wraps():
    """Every few hours, store wraps for users whose month just ended in their timezone. Idempotent, so several instances are fine."""
    from app.db.session import SessionLocal
    from app.scripts.generate_wraps import generate

    def run():
        with SessionLocal() as db:
            return generate(db)

    while True:
        try:
            stored = await asyncio.to_thread(run)
            if stored:
                print(f"[wraps] stored {stored} monthly wrap(s)")
        except Exception as exc:
            print(f"[wraps] job failed: {type(exc).__name__}")
        await asyncio.sleep(6 * 3600)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
(static_dir / "covers").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/")
def root():
    return {"message": "DevZGo Backend is running!"}


@app.get("/health")
def health():
    """Lightweight liveness for Render / load balancers. Does not migrate or seed."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content={"status": "degraded"})
