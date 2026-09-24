"""
Main application file for the DevZGo backend. This sets up the FastAPI app, includes route modules,
and ensures database tables are created on startup.
The root endpoint provides a simple health check to confirm the backend is running.

Storage note
------------
Only `storage/covers` and `storage/videos` are mounted as public static files.
Project workspaces (`storage/project_<uuid>/`) are NEVER publicly mounted — they are
served only through permissioned API routes under `/projects/{id}/file` and `/file/raw`.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.routes.auth import router as auth_router
from app.routes.projects import router as projects_router
from app.routes.media import router as media_router

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
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    # Any localhost / 127.0.0.1 port (Vite port changes, teammates, etc.)
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    # Explicit methods so DELETE is never omitted by proxies or middleware
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
)

# Public media only — do NOT mount the whole storage/ tree (workspaces are private).
storage_root = Path("storage")
covers_dir = storage_root / "covers"
videos_dir = storage_root / "videos"
covers_dir.mkdir(parents=True, exist_ok=True)
videos_dir.mkdir(parents=True, exist_ok=True)
app.mount("/storage/covers", StaticFiles(directory=str(covers_dir)), name="storage_covers")
app.mount("/storage/videos", StaticFiles(directory=str(videos_dir)), name="storage_videos")

app.include_router(media_router)

# Create the database tables based on the models defined in the Base metadata
# Base.metadata.create_all(bind=engine)

app.include_router(auth_router)
app.include_router(projects_router)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
(static_dir / "covers").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def root():
    return {"message": "DevZGo Backend is running!"}
